"""
Litigation Support Agent

Provides litigation support capabilities:
1. Evidence list generation — extract and organize evidence from case materials
2. Evidence chain analysis — check completeness of evidence
3. Trial preparation — generate trial outlines and strategies
4. Jurisdiction analysis — determine proper court
5. Statute of limitations — calculate deadlines
6. Litigation cost estimation — calculate court fees
7. Judgment prediction — analyze likely outcomes based on similar cases
8. Similar case comparison (P1.4) — semantic retrieval against the local
   court_cases library + statistical comparison + LLM differentiator analysis
9. Full litigation report (P1.4) — orchestrate analysis/evidence/similar
   cases/prediction into a formal written report (输出成文)
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timedelta
from typing import Any

from app.services.llm_service import get_raw_llm_service

logger = logging.getLogger(__name__)

# Backtick fence for JSON blocks in prompts (avoids Python 3.11 f-string issues)
_FENCE = "```"

# Outcome classification keywords for judgment_result text (code-side, not LLM)
_OUTCOME_KEYWORDS: list[tuple[str, str]] = [
    ("驳回", "驳回诉讼请求"),
    ("不予支持", "不予支持"),
    ("全部支持", "全部支持"),
    ("部分支持", "部分支持"),
    ("准许", "准许"),
    ("支持", "支持"),
    ("调解", "调解结案"),
    ("撤诉", "撤诉"),
]

# Amount patterns: 12,300元 / 12.3万元 / 1,200,000.00元
_AMOUNT_PATTERNS = [
    re.compile(r"([\d,，]+(?:\.\d+)?)\s*万\s*元"),
    re.compile(r"([\d,，]+(?:\.\d+)?)\s*元"),
]


def _classify_outcome(judgment_text: str) -> str:
    """Classify a judgment result into a coarse outcome bucket (code-side)."""
    if not judgment_text:
        return "未知"
    for keyword, label in _OUTCOME_KEYWORDS:
        if keyword in judgment_text:
            return label
    return "其他"


def _extract_amounts(text: str) -> list[float]:
    """Extract monetary amounts (元/万元) from free text for statistics."""
    amounts: list[float] = []
    if not text:
        return amounts
    for pattern in _AMOUNT_PATTERNS:
        for match in pattern.finditer(text):
            raw = match.group(1).replace(",", "").replace("，", "")
            try:
                value = float(raw)
            except ValueError:
                continue
            if "万" in match.group(0):
                value *= 10000
            # Plausibility filter for damages mentioned in judgments
            if 100 <= value <= 100_000_000:
                amounts.append(value)
    return amounts


class LitigationSupportAgent:
    """Provides comprehensive litigation support for legal practitioners."""

    async def analyze_case(
        self,
        case_description: str,
        evidence_list: str = "",
        claims: str = "",
    ) -> dict[str, Any]:
        """Comprehensive case analysis for litigation preparation.

        Provides: cause of action, legal basis, evidence requirements,
        strategy recommendations, and risk assessment.
        """
        llm = get_raw_llm_service()

        prompt = f"""作为诉讼律师，请对以下案件进行全面分析：

## 案情描述
{case_description}

## 现有证据
{evidence_list or '暂未提供'}

## 诉讼请求
{claims or '待确定'}

请分析以下内容：

### 1. 案件基本分析
- 案由确定
- 法律关系分析
- 争议焦点

### 2. 诉讼策略
- 诉讼请求建议（具体金额/请求计算方式）
- 请求权基础
- 举证责任分配

### 3. 证据分析
- 现有证据评估
- 缺失证据清单
- 证据收集建议

### 4. 程序性事项
- 管辖法院
- 诉讼时效
- 保全建议

### 5. 风险评估
- 胜诉概率评估
- 主要风险点
- 对方可能抗辩

以JSON格式返回：
{_FENCE}json
{{
    "cause_of_action": "案由",
    "legal_basis": ["法律依据"],
    "claims_suggested": ["建议的诉讼请求"],
    "evidence_analysis": {{
        "existing": ["现有证据评估"],
        "missing": ["缺失证据"],
        "collection_advice": ["收集建议"]
    }},
    "jurisdiction": "管辖法院",
    "statute_of_limitations": "时效分析",
    "win_probability": "胜诉概率评估",
    "risks": ["风险点"],
    "opponent_defenses": ["对方可能抗辩"],
    "strategy": "诉讼策略建议",
    "estimated_duration": "预计审理周期"
}}
{_FENCE}"""

        try:
            result = await llm.chat(
                messages=[
                    {"role": "system", "content": "你是资深诉讼律师，精通中国民事诉讼法和实务操作。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
                max_tokens=4096,
            )
            content = result.get("content", "")

            json_match = re.search(r'\{[\s\S]*\}', content)
            if json_match:
                analysis = json.loads(json_match.group())
            else:
                analysis = {"summary": content}

            return {"success": True, "analysis": analysis, "raw_content": content}

        except Exception as exc:
            return {"success": False, "error": str(exc)}

    async def generate_evidence_list(
        self,
        case_description: str,
        cause_of_action: str = "",
    ) -> dict[str, Any]:
        """Generate a structured evidence list for litigation.

        Returns evidence items organized by:
        - Identity evidence (主体证据)
        - Fact evidence (事实证据)
        - Damage evidence (损害证据)
        - Procedural evidence (程序证据)
        """
        llm = get_raw_llm_service()

        prompt = f"""请为以下案件生成完整的证据清单：

案情描述：{case_description}
案由：{cause_of_action or '待确定'}

请按以下格式生成证据清单：

{_FENCE}json
{{
    "evidence_groups": [
        {{
            "category": "证据类别（主体/事实/损害/程序/其他）",
            "items": [
                {{
                    "number": "证据编号",
                    "name": "证据名称",
                    "type": "证据类型（书证/物证/电子数据/证人证言/鉴定意见等）",
                    "purpose": "证明目的",
                    "source": "证据来源",
                    "status": "已有/需收集",
                    "urgency": "紧急/重要/一般",
                    "notes": "注意事项"
                }}
            ]
        }}
    ],
    "total_items": 0,
    "key_evidence": ["关键证据"],
    "collection_priority": ["优先收集建议"]
}}
{_FENCE}"""

        try:
            result = await llm.chat(
                messages=[
                    {"role": "system", "content": "你是诉讼证据整理专家。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.2,
                max_tokens=3000,
            )
            content = result.get("content", "")

            json_match = re.search(r'\{[\s\S]*\}', content)
            if json_match:
                return json.loads(json_match.group())
            return {"summary": content}

        except Exception as exc:
            return {"error": str(exc)}

    async def generate_trial_outline(
        self,
        case_description: str,
        party: str = "plaintiff",
        analysis: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Generate a trial preparation outline.

        Includes: opening statement, examination outline,
        cross-examination points, closing argument framework.
        """
        llm = get_raw_llm_service()

        party_label = "原告" if party == "plaintiff" else "被告"

        prompt = f"""请为以下案件生成庭审提纲：

案情：{case_description}
代理方：{party_label}

{f'前置分析结果：{json.dumps(analysis, ensure_ascii=False)[:2000]}' if analysis else ''}

请生成庭审提纲，包含：

### 1. 开庭陈述
- 案件概述（2分钟版）
- 核心观点

### 2. 举证提纲
- 举证顺序
- 每组证据的证明目的
- 可能的质证要点

### 3. 法庭辩论提纲
- 争议焦点及我方观点
- 法律适用分析
- 预判对方观点及反驳

### 4. 最后陈述
- 总结要点
- 请求事项

以JSON格式返回：
{_FENCE}json
{{
    "opening_statement": "开庭陈述要点",
    "evidence_outline": [{{"evidence": "证据", "purpose": "证明目的", "notes": "注意事项"}}],
    "debate_points": [{{"issue": "争议焦点", "our_position": "我方观点", "counter_arguments": "反驳要点"}}],
    "closing_statement": "最后陈述要点",
    "judge_questions": ["法官可能提出的问题及准备回答"],
    "tips": ["庭审注意事项"]
}}
{_FENCE}"""

        try:
            result = await llm.chat(
                messages=[
                    {"role": "system", "content": "你是资深出庭律师，擅长庭审实战。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
                max_tokens=4096,
            )
            content = result.get("content", "")

            json_match = re.search(r'\{[\s\S]*\}', content)
            if json_match:
                return json.loads(json_match.group())
            return {"summary": content}

        except Exception as exc:
            return {"error": str(exc)}

    async def predict_outcome(
        self,
        case_description: str,
        cause_of_action: str = "",
        similar_cases: list[dict] | None = None,
    ) -> dict[str, Any]:
        """Predict likely litigation outcome based on case facts and similar cases.

        Uses pattern matching from similar cases to estimate:
        - Win probability
        - Typical judgment range
        - Key factors influencing outcome

        P1.4: when similar_cases is not provided, automatically retrieve
        them from the local court_cases library (semantic search).
        """
        llm = get_raw_llm_service()

        retrieval_meta: dict[str, Any] = {}
        if not similar_cases:
            similar_cases, retrieval_meta = await self.find_similar_cases(
                case_description, cause_of_action=cause_of_action, top_k=5,
            )
            retrieval_meta["auto_retrieved"] = True

        similar_text = ""
        if similar_cases:
            parts = []
            for i, case in enumerate(similar_cases[:5], 1):
                parts.append(
                    f"案例{i}: {case.get('title', '')}\n"
                    f"  案由: {case.get('cause_of_action', '未知')} | 法院: {case.get('court_name', '未知')}\n"
                    f"  判决结果: {case.get('judgment_result', '未知')}\n"
                    f"  裁判要旨: {str(case.get('key_points', '') or case.get('summary', ''))[:300]}"
                )
            similar_text = "\n\n".join(parts)

        similar_section = f"类案参考：\n{similar_text}" if similar_text else "暂无类案数据"

        prompt = f"""基于以下案件信息和类案，预测诉讼结果：

案件描述：{case_description}
案由：{cause_of_action or '待确定'}

{similar_section}

请分析并预测：

{_FENCE}json
{{
    "win_probability": "胜诉概率（高/中/低 + 百分比估算）",
    "predicted_outcome": "预测结果",
    "damages_range": "赔偿金额范围（如适用）",
    "key_factors": ["影响判决的关键因素"],
    "favorable_points": ["有利因素"],
    "unfavorable_points": ["不利因素"],
    "risk_mitigation": ["降低风险的建议"],
    "settlement_advice": "调解/和解建议",
    "timeline": "预计审理周期",
    "disclaimer": "预测仅供参考，不构成法律意见"
}}
{_FENCE}"""

        try:
            result = await llm.chat(
                messages=[
                    {"role": "system", "content": "你是诉讼结果分析专家。注意：预测仅供参考，必须附带免责声明。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
                max_tokens=2000,
            )
            content = result.get("content", "")

            json_match = re.search(r'\{[\s\S]*\}', content)
            prediction = json.loads(json_match.group()) if json_match else {"summary": content}
            if retrieval_meta:
                prediction["similar_cases_retrieved"] = len(similar_cases or [])
                prediction["retrieval_meta"] = retrieval_meta
            return prediction

        except Exception as exc:
            return {"error": str(exc)}

    async def calculate_litigation_costs(
        self,
        claim_amount: float,
        case_type: str = "civil",
    ) -> dict[str, Any]:
        """Calculate litigation costs including court fees, lawyer fees, etc."""
        # Court fee calculation (per 《诉讼费用交纳办法》)
        if claim_amount <= 0:
            return {"error": "标的额必须为正数"}

        # 分段累进计算诉讼费
        if claim_amount <= 10000:
            court_fee = 50
        elif claim_amount <= 100000:
            court_fee = (claim_amount - 10000) * 0.025 + 50
        elif claim_amount <= 200000:
            court_fee = (claim_amount - 100000) * 0.02 + 2300
        elif claim_amount <= 500000:
            court_fee = (claim_amount - 200000) * 0.015 + 4300
        elif claim_amount <= 1000000:
            court_fee = (claim_amount - 500000) * 0.01 + 8800
        elif claim_amount <= 2000000:
            court_fee = (claim_amount - 1000000) * 0.009 + 13800
        elif claim_amount <= 5000000:
            court_fee = (claim_amount - 2000000) * 0.008 + 22800
        elif claim_amount <= 10000000:
            court_fee = (claim_amount - 5000000) * 0.007 + 46800
        else:
            court_fee = (claim_amount - 10000000) * 0.006 + 81800

        # Estimate lawyer fees (market rates)
        if claim_amount <= 100000:
            lawyer_fee_min = 5000
            lawyer_fee_max = 10000
        elif claim_amount <= 500000:
            lawyer_fee_min = 10000
            lawyer_fee_max = 30000
        elif claim_amount <= 1000000:
            lawyer_fee_min = 30000
            lawyer_fee_max = 60000
        else:
            lawyer_fee_min = claim_amount * 0.03
            lawyer_fee_max = claim_amount * 0.08

        # Other costs
        appraisal_fee = "视鉴定项目而定（通常5000-50000元）"
        travel_fee = "视案件管辖地而定"

        total_min = court_fee + lawyer_fee_min
        total_max = court_fee + lawyer_fee_max

        return {
            "claim_amount": claim_amount,
            "court_fee": round(court_fee, 2),
            "lawyer_fee_range": f"{lawyer_fee_min:,.0f} - {lawyer_fee_max:,.0f}元",
            "appraisal_fee": appraisal_fee,
            "travel_fee": travel_fee,
            "total_estimated_range": f"{total_min:,.0f} - {total_max:,.0f}元",
            "legal_basis": "《诉讼费用交纳办法》第十三条",
            "notes": [
                "以上为估算值，实际费用可能因地区、律所、案件复杂度等因素有所不同",
                "诉讼费由败诉方承担（部分胜诉部分败诉的按比例分担）",
                "律师费一般由委托人自行承担，特殊情况下可请求对方承担",
                "符合法律援助条件的可申请减免诉讼费",
            ],
        }

    # =========================================================================
    # P1.4: Similar case retrieval & comparison
    # =========================================================================

    async def find_similar_cases(
        self,
        case_description: str,
        cause_of_action: str = "",
        top_k: int = 5,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Retrieve similar cases from the local court_cases library.

        Uses Milvus semantic recall (BGE-M3) + PostgreSQL metadata completion,
        mirroring the /cases/search hybrid pipeline. Returns enriched case
        dicts plus retrieval metadata. Statistical fields (outcome bucket,
        extracted amounts) are computed code-side for reliability.
        """
        meta: dict[str, Any] = {"source": "local_court_cases", "top_k": top_k}
        cases: list[dict[str, Any]] = []

        try:
            from app.rag.milvus_service import get_milvus_rag_service

            vector_hits = await asyncio.to_thread(
                lambda: get_milvus_rag_service().search_cases(
                    case_description[:1000],
                    top_k=top_k,
                )
            )
            meta["vector_hits"] = len(vector_hits)

            if vector_hits:
                from sqlalchemy import select

                from app.core.database import async_session_factory
                from app.models.legal_knowledge import CourtCase

                hit_ids = [h["id"] for h in vector_hits if h.get("id")]
                hit_scores = {h["id"]: float(h.get("score", 0.0)) for h in vector_hits if h.get("id")}
                async with async_session_factory() as db:
                    result = await db.execute(
                        select(CourtCase).where(CourtCase.id.in_(hit_ids))
                    )
                    for case in result.scalars().all():
                        outcome = _classify_outcome(case.judgment_result or "")
                        amounts = _extract_amounts(
                            f"{case.judgment_result or ''} {case.summary or ''}"
                        )
                        cases.append({
                            "id": case.id,
                            "case_number": case.case_number,
                            "title": case.title,
                            "court_name": case.court_name,
                            "case_type": case.case_type,
                            "cause_of_action": case.cause_of_action,
                            "decision_date": case.decision_date,
                            "summary": (case.summary or "")[:500],
                            "key_points": (case.key_points or "")[:500],
                            "judgment_result": (case.judgment_result or "")[:500],
                            "referenced_laws": case.referenced_laws,
                            "relevance_score": round(hit_scores.get(case.id, 0.0), 4),
                            "outcome_bucket": outcome,
                            "amounts_extracted": amounts[:10],
                        })
                # Keep the vector ranking order
                order = {cid: i for i, cid in enumerate(hit_ids)}
                cases.sort(key=lambda c: order.get(c["id"], 999))

            if cause_of_action:
                cases = [c for c in cases if cause_of_action in (c.get("cause_of_action") or "")] or cases
        except Exception as exc:
            logger.warning("Similar case retrieval failed: %s", exc)
            meta["error"] = str(exc)

        meta["returned"] = len(cases)
        return cases, meta

    async def compare_similar_cases(
        self,
        case_description: str,
        similar_cases: list[dict[str, Any]] | None = None,
        cause_of_action: str = "",
        top_k: int = 5,
    ) -> dict[str, Any]:
        """Compare the user's case against retrieved similar cases.

        Statistics (outcome distribution, amount ranges) are computed
        code-side; the LLM only handles qualitative differentiation.
        """
        if similar_cases is None:
            similar_cases, retrieval_meta = await self.find_similar_cases(
                case_description, cause_of_action=cause_of_action, top_k=top_k,
            )
        else:
            retrieval_meta = {"source": "caller_provided"}

        if not similar_cases:
            return {
                "similar_cases": [],
                "statistics": {},
                "comparison": "未检索到可比较的类案。请补充更多案件细节后重试。",
                "retrieval_meta": retrieval_meta,
            }

        # ---- Code-side statistics (never let the LLM count) ----
        outcome_counts: dict[str, int] = {}
        all_amounts: list[float] = []
        for case in similar_cases:
            bucket = case.get("outcome_bucket") or _classify_outcome(case.get("judgment_result", ""))
            outcome_counts[bucket] = outcome_counts.get(bucket, 0) + 1
            all_amounts.extend(case.get("amounts_extracted") or [])
        all_amounts.sort()

        statistics: dict[str, Any] = {
            "total_cases": len(similar_cases),
            "outcome_distribution": outcome_counts,
        }
        if all_amounts:
            mid = len(all_amounts) // 2
            statistics["amount_stats"] = {
                "count": len(all_amounts),
                "min": all_amounts[0],
                "max": all_amounts[-1],
                "median": all_amounts[mid] if len(all_amounts) % 2 else (all_amounts[mid - 1] + all_amounts[mid]) / 2,
                "unit": "元",
            }

        # ---- LLM qualitative comparison ----
        llm = get_raw_llm_service()
        cases_text = ""
        for i, case in enumerate(similar_cases[:5], 1):
            cases_text += (
                f"类案{i}: {case.get('title', '')}\n"
                f"  案由: {case.get('cause_of_action', '未知')} | 法院: {case.get('court_name', '未知')}\n"
                f"  判决结果: {case.get('judgment_result', '未知')[:200]}\n"
                f"  裁判要旨: {str(case.get('key_points', '') or case.get('summary', ''))[:300]}\n\n"
            )

        stats_text = json.dumps(statistics, ensure_ascii=False)

        prompt = f"""请将待分析案件与以下类案进行比对分析：

## 待分析案件
{case_description}

## 类案（来自本地裁判文书库，按语义相似度排序）
{cases_text}

## 类案统计（代码统计结果，供参考，勿改数字）
{stats_text}

请输出：
{_FENCE}json
{{
    "fact_commonalities": ["与类案的事实共同点"],
    "fact_differences": ["与类案的关键差异点"],
    "outcome_analysis": "结合类案判决结果的走势分析",
    "amount_reference": "结合类案金额统计的赔偿参考（如适用）",
    "strategy_implications": ["对我方案件策略的启示"],
    "risk_factors": ["类案揭示的风险因素"]
}}
{_FENCE}"""

        # DeepSeek 偶发瞬时失败（网络抖动，错误信息为空），重试一次可显著提升成功率
        comparison: dict[str, Any] | None = None
        for attempt in range(2):
            try:
                result = await llm.chat(
                    messages=[
                        {"role": "system", "content": "你是类案比对分析专家，擅长从判例中提炼裁判规律。"},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0.3,
                    max_tokens=3000,
                )
                content = result.get("content", "")
                json_match = re.search(r'\{[\s\S]*\}', content)
                comparison = json.loads(json_match.group()) if json_match else {"summary": content}
                break
            except Exception as exc:
                logger.warning("Similar case comparison LLM call failed (attempt %d/2): %s", attempt + 1, exc)
                if attempt == 0:
                    await asyncio.sleep(2)
        if comparison is None:
            comparison = {"error": "比对分析生成失败：LLM 服务暂时不可用，请稍后重试"}

        return {
            "similar_cases": similar_cases,
            "statistics": statistics,
            "comparison": comparison,
            "retrieval_meta": retrieval_meta,
        }

    # =========================================================================
    # P1.4: Full litigation report (输出成文)
    # =========================================================================

    async def generate_litigation_report(
        self,
        case_description: str,
        evidence_list: str = "",
        claims: str = "",
        party: str = "plaintiff",
        top_k: int = 5,
    ) -> dict[str, Any]:
        """One-click full litigation analysis report.

        Pipeline (two parallel phases to stay well under the 300s nginx cap):
          Phase 1 (parallel): case analysis ‖ evidence list ‖ similar-case retrieval
          Phase 2 (parallel): similar-case comparison ‖ outcome prediction
          Phase 3: assemble a formal markdown report.

        A failure in any phase degrades gracefully — the section is marked
        生成失败 in the report instead of aborting the whole document.
        """
        started = datetime.now()

        analysis_res, evidence_res, (similar_cases, retrieval_meta) = await asyncio.gather(
            self.analyze_case(case_description, evidence_list, claims),
            self.generate_evidence_list(case_description),
            self.find_similar_cases(case_description, top_k=top_k),
        )

        comparison_res, prediction_res = await asyncio.gather(
            self.compare_similar_cases(case_description, similar_cases=similar_cases),
            self.predict_outcome(
                case_description,
                similar_cases=similar_cases or None,
            ),
        )

        elapsed = (datetime.now() - started).total_seconds()

        analysis = analysis_res.get("analysis", {}) if analysis_res.get("success") else {}
        prediction = prediction_res if "error" not in prediction_res else {}
        comparison = comparison_res.get("comparison", {}) if "similar_cases" in comparison_res else {}
        statistics = comparison_res.get("statistics", {}) if "similar_cases" in comparison_res else {}

        # ------------------ assemble markdown report ------------------
        now = datetime.now().strftime("%Y年%m月%d日")
        party_label = "原告" if party == "plaintiff" else "被告"

        def _section(title: str) -> str:
            return f"\n## {title}\n"

        report = f"# 诉讼分析报告\n\n"
        report += f"> 生成时间：{now} ｜ 代理视角：{party_label} ｜ 生成耗时：{elapsed:.0f} 秒\n\n"
        report += "> **重要提示**：本报告由 AI 生成，仅供学习参考，不构成正式法律意见。"
        report += "具体法律问题请咨询持证律师。\n"

        # 一、案件基本情况与法律分析
        report += _section("一、案件基本情况与法律分析")
        if analysis:
            report += f"\n**案由**：{analysis.get('cause_of_action', '待确定')}\n\n"
            legal_basis = analysis.get("legal_basis", [])
            if legal_basis:
                report += "**法律依据**：\n"
                for law in legal_basis[:10]:
                    report += f"- {law}\n"
            report += f"\n**争议焦点**：\n\n{analysis.get('dispute_focus', analysis.get('strategy', '待补充'))}\n"
            win_prob = analysis.get("win_probability", "")
            if win_prob:
                report += f"\n**初步胜诉评估**：{win_prob}\n"
        else:
            report += "\n*案件分析生成失败，请稍后重试。*\n"

        # 二、证据清单
        report += _section("二、证据清单")
        evidence_groups = evidence_res.get("evidence_groups", []) if isinstance(evidence_res, dict) else []
        if evidence_groups:
            for group in evidence_groups:
                report += f"\n### {group.get('category', '其他')}\n\n"
                report += "| 编号 | 证据名称 | 类型 | 证明目的 | 状态 | 优先级 |\n|---|---|---|---|---|---|\n"
                for item in group.get("items", []):
                    report += (
                        f"| {item.get('number', '')} | {item.get('name', '')} "
                        f"| {item.get('type', '')} | {item.get('purpose', '')} "
                        f"| {item.get('status', '')} | {item.get('urgency', '')} |\n"
                    )
            key_evidence = evidence_res.get("key_evidence", [])
            if key_evidence:
                report += "\n**关键证据**：\n"
                for ev in key_evidence:
                    report += f"- {ev}\n"
        else:
            report += "\n*证据清单生成失败，请稍后重试。*\n"

        # 三、类案比对分析
        report += _section("三、类案比对分析")
        if similar_cases:
            report += f"\n*类案来源：本地裁判文书库（语义检索 top {retrieval_meta.get('vector_hits', len(similar_cases))}）*\n\n"
            report += "| # | 案件 | 法院 | 案由 | 判决结果概要 | 相似度 |\n|---|---|---|---|---|---|\n"
            for i, case in enumerate(similar_cases[:5], 1):
                report += (
                    f"| {i} | {(case.get('title') or '')[:40]} | {case.get('court_name', '')} "
                    f"| {case.get('cause_of_action', '')} | {case.get('outcome_bucket', '')} "
                    f"| {case.get('relevance_score', 0):.2f} |\n"
                )
            if statistics:
                report += f"\n**结果分布**（代码统计）：{json.dumps(statistics.get('outcome_distribution', {}), ensure_ascii=False)}\n"
                amount_stats = statistics.get("amount_stats")
                if amount_stats:
                    report += (
                        f"\n**金额参考**：中位数 {amount_stats['median']:,.0f} 元 ｜ "
                        f"区间 {amount_stats['min']:,.0f} ~ {amount_stats['max']:,.0f} 元"
                        f"（样本 {amount_stats['count']} 个）\n"
                    )
            if isinstance(comparison, dict):
                commonalities = comparison.get("fact_commonalities", [])
                differences = comparison.get("fact_differences", [])
                implications = comparison.get("strategy_implications", [])
                if commonalities:
                    report += "\n**事实共同点**：\n"
                    for point in commonalities:
                        report += f"- {point}\n"
                if differences:
                    report += "\n**关键差异**：\n"
                    for point in differences:
                        report += f"- {point}\n"
                if implications:
                    report += "\n**策略启示**：\n"
                    for point in implications:
                        report += f"- {point}\n"
        else:
            report += "\n*未检索到相似类案（本地判例库覆盖有限）。*\n"

        # 四、裁判预测
        report += _section("四、裁判预测")
        if prediction:
            report += f"\n**胜诉概率**：{prediction.get('win_probability', '待评估')}\n\n"
            report += f"**预测结果**：{prediction.get('predicted_outcome', '待评估')}\n"
            damages = prediction.get("damages_range", "")
            if damages:
                report += f"\n**赔偿参考**：{damages}\n"
            favorable = prediction.get("favorable_points", [])
            if favorable:
                report += "\n**有利因素**：\n"
                for point in favorable:
                    report += f"- {point}\n"
            unfavorable = prediction.get("unfavorable_points", [])
            if unfavorable:
                report += "\n**不利因素**：\n"
                for point in unfavorable:
                    report += f"- {point}\n"
            settlement = prediction.get("settlement_advice", "")
            if settlement:
                report += f"\n**调解建议**：{settlement}\n"
        else:
            report += "\n*裁判预测生成失败，请稍后重试。*\n"

        # 五、诉讼策略与风险提示
        report += _section("五、诉讼策略与风险提示")
        if analysis:
            strategy = analysis.get("strategy", "")
            if strategy:
                report += f"\n{strategy}\n"
            risks = analysis.get("risks", [])
            if risks:
                report += "\n**主要风险**：\n"
                for risk in risks:
                    report += f"- {risk}\n"
            defenses = analysis.get("opponent_defenses", [])
            if defenses:
                report += "\n**对方可能抗辩**：\n"
                for defense in defenses:
                    report += f"- {defense}\n"
            jurisdiction = analysis.get("jurisdiction", "")
            limitations = analysis.get("statute_of_limitations", "")
            if jurisdiction or limitations:
                report += f"\n**管辖法院**：{jurisdiction or '待确定'}\n\n**诉讼时效**：{limitations or '待核实'}\n"

        report += _section("六、数据来源与声明")
        report += """
1. 类案数据来自本地裁判文书库（court_cases），检索方式为 BGE-M3 语义相似度；
2. 结果分布与金额统计由代码直接从判决文本提取，未经 LLM 改写；
3. 本报告由多模块 AI 流水线生成（案件分析 → 证据清单 → 类案比对 → 裁判预测）；
4. **免责声明**：本报告仅供学习参考，不构成法律意见或诉讼建议，不构成对裁判结果的承诺。任何法律决策请咨询持证律师。
"""

        return {
            "success": True,
            "report_markdown": report,
            "sections": {
                "analysis": analysis,
                "evidence": evidence_res if isinstance(evidence_res, dict) else {},
                "similar_cases": similar_cases,
                "comparison": comparison,
                "statistics": statistics,
                "prediction": prediction,
            },
            "retrieval_meta": retrieval_meta,
            "elapsed_seconds": round(elapsed, 1),
            "generated_at": datetime.now().isoformat(),
        }


# =============================================================================
# Singleton
# =============================================================================

_agent: LitigationSupportAgent | None = None


def get_litigation_support_agent() -> LitigationSupportAgent:
    global _agent
    if _agent is None:
        _agent = LitigationSupportAgent()
    return _agent
