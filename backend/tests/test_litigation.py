"""Tests for litigation support endpoints and P1.4 deepening.

Covers:
- Outcome classification / amount extraction helpers (pure, code-side stats)
- compare_similar_cases statistics (never LLM-counted)
- predict_outcome auto-retrieval when similar_cases absent
- generate_litigation_report assembly + graceful degradation
- API endpoints /litigation/similar-cases and /litigation/report
"""

import pytest
from httpx import AsyncClient
from unittest.mock import AsyncMock, patch

from app.agents.litigation_support_agent import (
    LitigationSupportAgent,
    _classify_outcome,
    _extract_amounts,
    get_litigation_support_agent,
)


# ---------------------------------------------------------------------------
# Pure helpers (code-side statistics)
# ---------------------------------------------------------------------------


class TestOutcomeClassification:
    def test_supported(self):
        assert _classify_outcome("判决如下：被告支付违约金，诉讼请求部分支持") == "部分支持"

    def test_rejected(self):
        assert _classify_outcome("驳回原告的全部诉讼请求") == "驳回诉讼请求"

    def test_empty(self):
        assert _classify_outcome("") == "未知"

    def test_none_like(self):
        assert _classify_outcome(None) == "未知"


class TestAmountExtraction:
    def test_plain_yuan(self):
        amounts = _extract_amounts("赔偿经济损失50000元")
        assert 50000 in amounts

    def test_wan_yuan(self):
        amounts = _extract_amounts("判决赔偿12.5万元")
        assert 125000 in amounts

    def test_comma_numbers(self):
        amounts = _extract_amounts("支付1,200,000元")
        assert 1200000 in amounts

    def test_ignores_tiny_amounts(self):
        amounts = _extract_amounts("本案受理费50元由被告负担")
        assert 50 not in amounts  # below 100 yuan plausibility floor

    def test_empty(self):
        assert _extract_amounts("") == []
        assert _extract_amounts(None) == []


# ---------------------------------------------------------------------------
# Agent: compare_similar_cases (statistics computed code-side)
# ---------------------------------------------------------------------------

FAKE_CASES = [
    {
        "id": "c1",
        "case_number": "(2023)京01民终100号",
        "title": "张某诉李某买卖合同纠纷案",
        "court_name": "北京市第一中级人民法院",
        "cause_of_action": "买卖合同纠纷",
        "judgment_result": "部分支持：判决被告支付货款50000元",
        "key_points": "违约金约定过高应予调整",
        "outcome_bucket": "部分支持",
        "amounts_extracted": [50000],
        "relevance_score": 0.87,
    },
    {
        "id": "c2",
        "case_number": "(2022)沪02民终200号",
        "title": "甲公司诉乙公司买卖合同纠纷案",
        "court_name": "上海市第二中级人民法院",
        "cause_of_action": "买卖合同纠纷",
        "judgment_result": "驳回诉讼请求",
        "key_points": "证据不足以证明交货事实",
        "outcome_bucket": "驳回诉讼请求",
        "amounts_extracted": [],
        "relevance_score": 0.82,
    },
]


def _mock_llm_chat(payload: dict):
    """Build a mock raw LLM service returning a JSON block."""
    import json as _json

    content = "```json\n" + _json.dumps(payload, ensure_ascii=False) + "\n```"
    mock_llm = AsyncMock()
    mock_llm.chat = AsyncMock(return_value={"content": content})
    return mock_llm


class TestCompareSimilarCases:
    async def test_statistics_code_side(self):
        """Outcome distribution and amount stats must come from code, not LLM."""
        comparison_payload = {
            "fact_commonalities": ["均为买卖合同纠纷"],
            "fact_differences": ["本案有书面合同"],
            "strategy_implications": ["补强交货证据"],
        }
        agent = LitigationSupportAgent()
        with patch(
            "app.agents.litigation_support_agent.get_raw_llm_service",
            return_value=_mock_llm_chat(comparison_payload),
        ):
            result = await agent.compare_similar_cases(
                case_description="我方交付货物后对方拒付货款",
                similar_cases=FAKE_CASES,
            )

        stats = result["statistics"]
        assert stats["total_cases"] == 2
        assert stats["outcome_distribution"]["部分支持"] == 1
        assert stats["outcome_distribution"]["驳回诉讼请求"] == 1
        # median of [50000] is 50000
        assert stats["amount_stats"]["median"] == 50000
        assert result["comparison"]["fact_commonalities"] == ["均为买卖合同纠纷"]
        assert result["retrieval_meta"]["source"] == "caller_provided"

    async def test_no_cases_graceful(self):
        agent = LitigationSupportAgent()
        result = await agent.compare_similar_cases(
            case_description="某案", similar_cases=[]
        )
        assert result["similar_cases"] == []
        assert "未检索到" in result["comparison"]

    async def test_llm_failure_degrades(self):
        """LLM failure must not kill the comparison — statistics still returned."""
        broken_llm = AsyncMock()
        broken_llm.chat = AsyncMock(side_effect=RuntimeError("LLM down"))
        agent = LitigationSupportAgent()
        with patch(
            "app.agents.litigation_support_agent.get_raw_llm_service",
            return_value=broken_llm,
        ), patch("app.agents.litigation_support_agent.asyncio.sleep", new=AsyncMock()):
            result = await agent.compare_similar_cases(
                case_description="某买卖纠纷", similar_cases=FAKE_CASES
            )
        assert result["statistics"]["total_cases"] == 2
        assert "error" in result["comparison"]
        assert broken_llm.chat.await_count == 2

    async def test_transient_failure_retried(self):
        """A transient provider blip on the first call must be retried and succeed."""
        payload = {
            "fact_commonalities": ["均为买卖合同纠纷"],
            "fact_differences": ["本案金额更大"],
            "strategy_implications": ["固定送货签收证据"],
        }
        import json as _json

        flaky_llm = AsyncMock()
        flaky_llm.chat = AsyncMock(
            side_effect=[
                RuntimeError(""),  # transient network blip, empty message
                {"content": "```json\n" + _json.dumps(payload, ensure_ascii=False) + "\n```"},
            ]
        )
        agent = LitigationSupportAgent()
        with patch(
            "app.agents.litigation_support_agent.get_raw_llm_service",
            return_value=flaky_llm,
        ), patch("app.agents.litigation_support_agent.asyncio.sleep", new=AsyncMock()):
            result = await agent.compare_similar_cases(
                case_description="某买卖纠纷", similar_cases=FAKE_CASES
            )
        assert flaky_llm.chat.await_count == 2
        assert result["comparison"]["fact_commonalities"] == ["均为买卖合同纠纷"]


# ---------------------------------------------------------------------------
# Agent: predict_outcome auto-retrieval (P1.4)
# ---------------------------------------------------------------------------


class TestPredictAutoRetrieval:
    async def test_auto_retrieves_when_similar_cases_absent(self):
        prediction_payload = {
            "win_probability": "中等（约50-60%）",
            "predicted_outcome": "部分支持",
            "disclaimer": "预测仅供参考，不构成法律意见",
        }
        agent = LitigationSupportAgent()
        mock_find = AsyncMock(return_value=(FAKE_CASES, {"source": "local_court_cases"}))
        with patch.object(agent, "find_similar_cases", mock_find), patch(
            "app.agents.litigation_support_agent.get_raw_llm_service",
            return_value=_mock_llm_chat(prediction_payload),
        ):
            result = await agent.predict_outcome("我方交货后对方拒付货款12万元")

        mock_find.assert_awaited_once()
        assert result["win_probability"] == "中等（约50-60%）"
        assert result["similar_cases_retrieved"] == 2
        assert result["retrieval_meta"]["auto_retrieved"] is True

    async def test_provided_cases_skip_retrieval(self):
        prediction_payload = {"win_probability": "高（约80%）", "disclaimer": "仅供参考"}
        agent = LitigationSupportAgent()
        mock_find = AsyncMock()
        with patch.object(agent, "find_similar_cases", mock_find), patch(
            "app.agents.litigation_support_agent.get_raw_llm_service",
            return_value=_mock_llm_chat(prediction_payload),
        ):
            result = await agent.predict_outcome(
                "某案", similar_cases=FAKE_CASES
            )
        mock_find.assert_not_awaited()
        assert "retrieval_meta" not in result


# ---------------------------------------------------------------------------
# Agent: generate_litigation_report (assembly + degradation)
# ---------------------------------------------------------------------------


def _fake_analysis() -> dict:
    return {
        "success": True,
        "analysis": {
            "cause_of_action": "买卖合同纠纷",
            "legal_basis": ["《民法典》第五百七十七条"],
            "win_probability": "中等",
            "strategy": "补强交货证据后起诉",
            "risks": ["对方可能主张质量瑕疵"],
            "opponent_defenses": ["未收到货物"],
            "jurisdiction": "被告住所地法院",
            "statute_of_limitations": "三年",
        },
    }


def _fake_evidence() -> dict:
    return {
        "evidence_groups": [
            {
                "category": "事实证据",
                "items": [
                    {
                        "number": "1",
                        "name": "购销合同",
                        "type": "书证",
                        "purpose": "证明合同关系",
                        "status": "已有",
                        "urgency": "重要",
                    }
                ],
            }
        ],
        "key_evidence": ["购销合同"],
    }


def _fake_prediction() -> dict:
    return {
        "win_probability": "中等（约60%）",
        "predicted_outcome": "部分支持",
        "favorable_points": ["有书面合同"],
        "settlement_advice": "可先行协商",
    }


def _fake_comparison_result() -> dict:
    return {
        "similar_cases": FAKE_CASES,
        "statistics": {
            "total_cases": 2,
            "outcome_distribution": {"部分支持": 1, "驳回诉讼请求": 1},
            "amount_stats": {"count": 1, "min": 50000, "max": 50000, "median": 50000, "unit": "元"},
        },
        "comparison": {
            "fact_commonalities": ["均为买卖合同纠纷"],
            "fact_differences": ["本案有书面合同"],
            "strategy_implications": ["补强交货证据"],
        },
        "retrieval_meta": {"source": "local_court_cases"},
    }


class TestGenerateLitigationReport:
    async def test_full_report_assembly(self):
        """Report should contain all six sections and the disclaimer."""
        agent = LitigationSupportAgent()
        with patch.object(agent, "analyze_case", AsyncMock(return_value=_fake_analysis())), \
             patch.object(agent, "generate_evidence_list", AsyncMock(return_value=_fake_evidence())), \
             patch.object(agent, "find_similar_cases", AsyncMock(return_value=(FAKE_CASES, {"source": "local_court_cases", "vector_hits": 2}))), \
             patch.object(agent, "compare_similar_cases", AsyncMock(return_value=_fake_comparison_result())), \
             patch.object(agent, "predict_outcome", AsyncMock(return_value=_fake_prediction())):
            result = await agent.generate_litigation_report(
                case_description="我方交货后对方拒付货款12万元",
                party="plaintiff",
            )

        assert result["success"] is True
        report = result["report_markdown"]
        for section in ["一、案件基本情况与法律分析", "二、证据清单", "三、类案比对分析",
                        "四、裁判预测", "五、诉讼策略与风险提示", "六、数据来源与声明"]:
            assert section in report, f"missing section: {section}"
        assert "免责声明" in report
        assert "原告" in report
        assert "《民法典》第五百七十七条" in report
        assert "部分支持" in report
        assert "60%" in report
        assert result["sections"]["similar_cases"] == FAKE_CASES

    async def test_degrades_gracefully_on_failures(self):
        """A failing analysis phase must not abort the whole report."""
        agent = LitigationSupportAgent()
        empty_retrieval = ([], {"source": "local_court_cases", "returned": 0})
        empty_comparison = {
            "similar_cases": [],
            "statistics": {},
            "comparison": "未检索到可比较的类案。请补充更多案件细节后重试。",
            "retrieval_meta": {"source": "local_court_cases"},
        }
        with patch.object(agent, "analyze_case", AsyncMock(return_value={"success": False, "error": "boom"})), \
             patch.object(agent, "generate_evidence_list", AsyncMock(return_value={})), \
             patch.object(agent, "find_similar_cases", AsyncMock(return_value=empty_retrieval)), \
             patch.object(agent, "compare_similar_cases", AsyncMock(return_value=empty_comparison)), \
             patch.object(agent, "predict_outcome", AsyncMock(return_value={"error": "boom"})):
            result = await agent.generate_litigation_report(case_description="某案")

        assert result["success"] is True
        report = result["report_markdown"]
        assert "案件分析生成失败" in report
        assert "证据清单生成失败" in report
        assert "裁判预测生成失败" in report
        assert "未检索到相似类案" in report

    async def test_parallel_phases_called(self):
        """Phase-1 steps (analysis/evidence/retrieval) must all be invoked."""
        agent = LitigationSupportAgent()
        with patch.object(agent, "analyze_case", AsyncMock(return_value=_fake_analysis())) as m_an, \
             patch.object(agent, "generate_evidence_list", AsyncMock(return_value=_fake_evidence())) as m_ev, \
             patch.object(agent, "find_similar_cases", AsyncMock(return_value=(FAKE_CASES, {"source": "x"}))) as m_find, \
             patch.object(agent, "compare_similar_cases", AsyncMock(return_value=_fake_comparison_result())), \
             patch.object(agent, "predict_outcome", AsyncMock(return_value=_fake_prediction())):
            await agent.generate_litigation_report(case_description="某案")

        m_an.assert_awaited_once()
        m_ev.assert_awaited_once()
        m_find.assert_awaited_once()


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------


class TestLitigationAPI:
    async def test_similar_cases_endpoint(self, test_client: AsyncClient, auth_headers):
        mock_result = _fake_comparison_result()
        with patch("app.api.v1.litigation.get_litigation_support_agent") as mock_get:
            agent = AsyncMock()
            agent.compare_similar_cases = AsyncMock(return_value=mock_result)
            mock_get.return_value = agent

            resp = await test_client.post(
                "/api/v1/litigation/similar-cases",
                json={"case_description": "我方交货后对方拒付货款", "top_k": 5},
                headers=auth_headers,
            )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["similar_cases"]) == 2
        assert data["statistics"]["outcome_distribution"]["部分支持"] == 1

    async def test_similar_cases_validation(self, test_client: AsyncClient, auth_headers):
        resp = await test_client.post(
            "/api/v1/litigation/similar-cases",
            json={"case_description": ""},
            headers=auth_headers,
        )
        assert resp.status_code == 422

        resp = await test_client.post(
            "/api/v1/litigation/similar-cases",
            json={"case_description": "某案", "top_k": 99},
            headers=auth_headers,
        )
        assert resp.status_code == 422

    async def test_report_endpoint(self, test_client: AsyncClient, auth_headers):
        agent = LitigationSupportAgent()
        with patch.object(agent, "analyze_case", AsyncMock(return_value=_fake_analysis())), \
             patch.object(agent, "generate_evidence_list", AsyncMock(return_value=_fake_evidence())), \
             patch.object(agent, "find_similar_cases", AsyncMock(return_value=(FAKE_CASES, {"source": "x", "vector_hits": 2}))), \
             patch.object(agent, "compare_similar_cases", AsyncMock(return_value=_fake_comparison_result())), \
             patch.object(agent, "predict_outcome", AsyncMock(return_value=_fake_prediction())), \
             patch("app.api.v1.litigation.get_litigation_support_agent", return_value=agent):
            resp = await test_client.post(
                "/api/v1/litigation/report",
                json={"case_description": "我方交货后对方拒付货款12万元", "party": "plaintiff"},
                headers=auth_headers,
            )
        assert resp.status_code == 200
        data = resp.json()
        assert "report_markdown" in data
        assert "四、裁判预测" in data["report_markdown"]
        assert data["elapsed_seconds"] >= 0

    async def test_report_validation(self, test_client: AsyncClient, auth_headers):
        resp = await test_client.post(
            "/api/v1/litigation/report",
            json={"case_description": ""},
            headers=auth_headers,
        )
        assert resp.status_code == 422

    async def test_predict_endpoint_still_works(self, test_client: AsyncClient, auth_headers):
        """Legacy /litigation/predict keeps its contract (no auto-retrieval required)."""
        agent = LitigationSupportAgent()
        with patch.object(agent, "find_similar_cases", AsyncMock(return_value=(FAKE_CASES, {"source": "test"}))), \
             patch(
                 "app.agents.litigation_support_agent.get_raw_llm_service",
                 return_value=_mock_llm_chat({"win_probability": "高（约80%）", "disclaimer": "仅供参考"}),
             ), \
             patch("app.api.v1.litigation.get_litigation_support_agent", return_value=agent):
            resp = await test_client.post(
                "/api/v1/litigation/predict",
                json={"case_description": "某买卖合同纠纷"},
                headers=auth_headers,
            )
        assert resp.status_code == 200
        assert resp.json()["win_probability"] == "高（约80%）"


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------


class TestSingleton:
    def test_get_agent_singleton(self):
        a1 = get_litigation_support_agent()
        a2 = get_litigation_support_agent()
        assert a1 is a2
