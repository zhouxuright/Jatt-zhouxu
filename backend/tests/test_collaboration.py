"""Tests for collaboration endpoints."""

import pytest
from httpx import AsyncClient
from unittest.mock import patch, AsyncMock

from langchain_core.messages import AIMessage

from app.agents.collaboration import (
    AGENT_REGISTRY,
    COLLABORATION_PATTERNS,
    MultiAgentCollaborator,
    create_multi_agent_collaborator,
    register_agent,
)
from app.agents.supervisor_agent import SupervisorAgent, SupervisorState


# ---------------------------------------------------------------------------
# GET /collaboration/patterns
# ---------------------------------------------------------------------------


class TestCollaborationPatterns:
    """Tests for GET /api/v1/collaboration/patterns."""

    async def test_list_collaboration_patterns(self, test_client: AsyncClient):
        """Listing collaboration patterns should return available patterns."""
        resp = await test_client.get("/api/v1/collaboration/patterns")
        assert resp.status_code == 200
        data = resp.json()
        assert "patterns" in data
        assert isinstance(data["patterns"], list)
        assert len(data["patterns"]) > 0
        # Each pattern should have id, name, description
        pattern = data["patterns"][0]
        assert "id" in pattern
        assert "name" in pattern
        assert "description" in pattern


# ---------------------------------------------------------------------------
# POST /collaboration/analyze
# ---------------------------------------------------------------------------


class TestCollaborationAnalyze:
    """Tests for POST /api/v1/collaboration/analyze."""

    async def test_analyze_endpoint_with_mock(self, test_client: AsyncClient):
        """Collaboration analyze endpoint should return a multi-agent response."""
        mock_result = {
            "final_response": "根据多方分析，该法律问题涉及民法典相关条款...",
            "intent": "legal_consultation",
            "collaboration_pattern": "sequential",
            "agents_involved": ["legal_consult", "law_retrieval"],
            "iterations": 1,
        }

        with patch(
            "app.api.v1.collaboration.get_collaborator"
        ) as mock_get_collab:
            mock_collab = AsyncMock()
            mock_collab.run = AsyncMock(return_value=mock_result)
            mock_get_collab.return_value = mock_collab

            payload = {
                "query": "劳动合同到期不续签是否需要支付经济补偿？",
            }
            resp = await test_client.post(
                "/api/v1/collaboration/analyze", json=payload
            )
            assert resp.status_code == 200
            data = resp.json()
            assert "final_response" in data
            assert "collaboration_pattern" in data
            assert "agents_involved" in data
            assert len(data["agents_involved"]) > 0


# ---------------------------------------------------------------------------
# Framework unit tests (no LLM, no external services)
# ---------------------------------------------------------------------------


class _FakeLLM:
    """Sequential-response fake for llm_service.get_llm()."""

    def __init__(self, responses: list[str]):
        self._responses = list(responses)
        self.call_count = 0

    async def ainvoke(self, messages):
        idx = min(self.call_count, len(self._responses) - 1)
        self.call_count += 1
        return AIMessage(content=self._responses[idx])


class _FakeLLMService:
    def __init__(self, llm):
        self._llm = llm

    def get_llm(self, **kwargs):
        return self._llm


@pytest.fixture
def restore_registry():
    """Snapshot AGENT_REGISTRY and restore it after the test."""
    snapshot = dict(AGENT_REGISTRY)
    yield
    AGENT_REGISTRY.clear()
    AGENT_REGISTRY.update(snapshot)


def _make_collaborator(llm_responses: list[str]) -> tuple[MultiAgentCollaborator, _FakeLLM]:
    collab = create_multi_agent_collaborator()
    fake_llm = _FakeLLM(llm_responses)
    collab._llm_service = _FakeLLMService(fake_llm)
    return collab, fake_llm


class TestAgentRegistry:
    def test_default_registry_contains_seven_agents(self):
        expected = {
            "law_retrieval",
            "legal_consult",
            "contract_review",
            "contract_draft",
            "compliance_risk",
            "litigation_support",
            "document_gen",
        }
        assert expected <= set(AGENT_REGISTRY.keys())

    def test_register_agent_replaces_entry(self, restore_registry):
        async def adapter(query, context):
            return {"final_output": "ok"}

        register_agent("law_retrieval", "replaced", adapter)
        assert AGENT_REGISTRY["law_retrieval"].description == "replaced"

    def test_all_four_patterns_defined(self):
        assert set(COLLABORATION_PATTERNS.keys()) == {
            "parallel", "sequential", "iterative", "hierarchical",
        }


class TestParallelPattern:
    async def test_executes_all_agents_and_synthesizes(self, restore_registry):
        calls = []

        async def adapter_a(query, context):
            calls.append(("a", query))
            return {"final_output": "A代理结论"}

        async def adapter_b(query, context):
            calls.append(("b", query))
            return {"final_output": "B代理结论"}

        register_agent("fake_a", "测试A", adapter_a)
        register_agent("fake_b", "测试B", adapter_b)

        collab, fake_llm = _make_collaborator(["综合分析报告（（《民法典》第1条））"])

        result = await collab.run(
            query="劳动合同与竞业限制交叉问题",
            context={
                "required_agents": ["fake_a", "fake_b"],
                "collaboration_pattern": "parallel",
            },
        )

        assert result["collaboration_pattern"] == "parallel"
        assert result["agents_involved"] == ["fake_a", "fake_b"]
        assert result["final_response"].startswith("综合分析报告（（《民法典》第1条））")
        assert "法律声明" in result["final_response"]  # disclaimer appended
        # Only the synthesis call hit the LLM: explicit context skipped classification
        assert fake_llm.call_count == 1
        assert {c[0] for c in calls} == {"a", "b"}

    async def test_agent_failure_is_isolated(self, restore_registry):
        async def failing(query, context):
            raise RuntimeError("boom")

        async def healthy(query, context):
            return {"final_output": "B代理结论"}

        register_agent("fake_a", "失败A", failing)
        register_agent("fake_b", "正常B", healthy)

        collab, _ = _make_collaborator(["综合报告"])

        result = await collab.run(
            query="复杂法律问题",
            context={
                "required_agents": ["fake_a", "fake_b"],
                "collaboration_pattern": "parallel",
            },
        )

        assert result["agents_involved"] == ["fake_b"]
        detail_a = next(d for d in result["agent_details"] if d["agent"] == "fake_a")
        assert detail_a["status"] == "error"
        assert "boom" in detail_a["error"]

    async def test_single_agent_explicit_choice_is_respected(self, restore_registry):
        async def adapter(query, context):
            return {"final_output": "唯一结论"}

        register_agent("fake_a", "测试A", adapter)
        collab, _ = _make_collaborator(["综合报告"])

        result = await collab.run(
            query="单一领域问题",
            context={
                "required_agents": ["fake_a"],
                "collaboration_pattern": "parallel",
            },
        )
        # Explicit context is a deterministic override: no silent downgrades
        assert result["collaboration_pattern"] == "parallel"


class TestSequentialPattern:
    async def test_prior_findings_flow_to_next_agent(self, restore_registry):
        seen = {}

        async def adapter_a(query, context):
            return {"final_output": "检索到的法条清单"}

        async def adapter_b(query, context):
            seen["prior"] = context.get("prior_findings", "")
            return {"final_output": "基于检索的分析"}

        register_agent("fake_a", "检索", adapter_a)
        register_agent("fake_b", "分析", adapter_b)

        collab, _ = _make_collaborator(["综合报告"])

        result = await collab.run(
            query="先检索后分析的问题",
            context={
                "required_agents": ["fake_a", "fake_b"],
                "collaboration_pattern": "sequential",
            },
        )

        assert result["collaboration_pattern"] == "sequential"
        assert "检索到的法条清单" in seen["prior"]
        assert result["agents_involved"] == ["fake_a", "fake_b"]


class TestIterativePattern:
    async def test_approved_first_pass(self, restore_registry):
        async def adapter(query, context):
            return {"final_output": "初稿内容（（《民法典》第143条））"}

        register_agent("fake_a", "主产出", adapter)

        approved = '{"quality_score": 95, "issues": [], "suggestions": [], "approved": true}'
        collab, _ = _make_collaborator([approved])

        result = await collab.run(
            query="高风险法律分析",
            context={
                "required_agents": ["fake_a"],
                "collaboration_pattern": "iterative",
                "primary_agent": "fake_a",
            },
        )

        assert result["collaboration_pattern"] == "iterative"
        assert "初稿内容" in result["final_response"]
        assert result["iterations"] == 2  # draft(1) + review(2)

    async def test_revision_loop_until_approved(self, restore_registry):
        async def adapter(query, context):
            return {"final_output": "初稿内容"}

        register_agent("fake_a", "主产出", adapter)

        not_approved = '{"quality_score": 50, "issues": ["法条引用缺失"], "suggestions": ["补充法条"], "approved": false}'
        revised = "修订后内容（（《民法典》第143条））"
        approved = '{"quality_score": 92, "issues": [], "suggestions": [], "approved": true}'

        collab, fake_llm = _make_collaborator([not_approved, revised, approved])

        result = await collab.run(
            query="高风险法律分析",
            context={
                "required_agents": ["fake_a"],
                "collaboration_pattern": "iterative",
                "primary_agent": "fake_a",
            },
        )

        assert fake_llm.call_count == 3  # review, revise, review
        assert result["iterations"] == 3
        assert "修订后内容" in result["final_response"]
        assert "92/100" in result["final_response"]


class TestHierarchicalPattern:
    async def test_explicit_subtasks_are_used(self, restore_registry):
        received = {}

        async def adapter_a(query, context):
            received["a"] = query
            return {"final_output": "A子任务结论"}

        async def adapter_b(query, context):
            received["b"] = query
            return {"final_output": "B子任务结论"}

        register_agent("fake_a", "检索", adapter_a)
        register_agent("fake_b", "合规", adapter_b)

        collab, fake_llm = _make_collaborator(["汇总报告"])

        result = await collab.run(
            query="跨境数据传输合规与合同问题",
            context={
                "required_agents": ["fake_a", "fake_b"],
                "collaboration_pattern": "hierarchical",
                "subtasks": [
                    {"agent": "fake_a", "task": "检索数据出境法规"},
                    {"agent": "fake_b", "task": "评估合同合规风险"},
                ],
            },
        )

        assert received["a"] == "检索数据出境法规"
        assert received["b"] == "评估合同合规风险"
        # Decomposition skipped: only synthesis hit the LLM
        assert fake_llm.call_count == 1
        assert result["agents_involved"] == ["fake_a", "fake_b"]

    async def test_llm_decomposition_fallback(self, restore_registry):
        async def adapter_a(query, context):
            return {"final_output": "A结论"}

        register_agent("fake_a", "检索", adapter_a)

        decompose_json = '[{"agent": "fake_a", "task": "子任务一"}]'
        collab, fake_llm = _make_collaborator([decompose_json, "汇总报告"])

        result = await collab.run(
            query="需要拆解的问题",
            context={
                "required_agents": ["fake_a"],
                "collaboration_pattern": "hierarchical",
            },
        )

        assert fake_llm.call_count == 2  # decompose + synthesize
        assert result["agents_involved"] == ["fake_a"]


class TestSupervisorComplexAnalysis:
    async def test_final_response_mapped_to_final_output(self):
        """Regression: aggregator reads final_output; collaborator returns final_response."""
        supervisor = SupervisorAgent()

        collab_result = {
            "final_response": "多代理综合报告",
            "intent": "contract_analysis_with_compliance",
            "collaboration_pattern": "parallel",
            "agents_involved": ["contract_review", "compliance_risk"],
            "agent_details": [],
            "iterations": 0,
        }

        mock_collab = AsyncMock()
        mock_collab.run = AsyncMock(return_value=collab_result)
        supervisor._collaborator = mock_collab

        state = SupervisorState(user_query="审查这份数据处理协议并评估合规风险")
        node_result = await supervisor._execute_complex_analysis_node(state)

        assert node_result["agent_result"]["final_output"] == "多代理综合报告"
        assert node_result["agent_result"]["collaboration_pattern"] == "parallel"
        mock_collab.run.assert_awaited_once_with(
            query="审查这份数据处理协议并评估合规风险",
            context={},
        )
