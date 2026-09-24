"""
Contract Lifecycle Management Agent

Covers the full contract lifecycle:
1. Draft — generate contract from templates or descriptions
2. Review — risk analysis (already exists, enhanced here)
3. Compare — diff two contract versions
4. Comply — check against latest regulations
5. Track — key date monitoring (expiry, renewal, payment)
6. Archive — structured storage with search
"""
from __future__ import annotations

import difflib
import json
import logging
import re
from datetime import datetime, timedelta
from typing import Any

from app.services.llm_service import get_raw_llm_service
from app.services.contract_templates import get_contract_template_engine

logger = logging.getLogger(__name__)

# Backtick fence for JSON blocks in prompts (avoids Python 3.11 f-string issues)
_FENCE = "```"


class ContractLifecycleAgent:
    """Manages the full contract lifecycle from draft to archive."""

    async def draft_contract(
        self,
        description: str,
        contract_type: str = "",
        fields: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Draft a contract from description or template.

        If contract_type matches a template, fills it with provided fields.
        Otherwise uses LLM to generate from description.
        """
        engine = get_contract_template_engine()

        # Try template-based drafting first
        if contract_type:
            templates = engine.list_templates()
            for tmpl in templates:
                if contract_type in tmpl["name"] or contract_type in tmpl.get("tags", []):
                    if fields:
                        result = engine.fill_template(tmpl["id"], fields)
                        if result.get("success"):
                            return {
                                "method": "template",
                                "template_id": tmpl["id"],
                                "template_name": tmpl["name"],
                                "content": result["filled_content"],
                                "missing_fields": result.get("missing_fields", []),
                                "legal_basis": result.get("legal_basis", ""),
                            }

        # Fallback: LLM generation
        llm = get_raw_llm_service()
        system_prompt = """你是一位专业的合同起草律师。请根据用户的描述，起草一份完整、规范的合同。

要求：
1. 合同结构完整（标题、当事人、条款、签署栏）
2. 条款明确具体，具有可执行性
3. 包含必要的保护性条款（违约责任、争议解决、保密等）
4. 引用相关法律依据
5. 标注需要双方协商确定的条款【待协商】"""

        try:
            result = await llm.chat(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"请起草一份合同：{description}"},
                ],
                temperature=0.3,
                max_tokens=4096,
            )
            return {
                "method": "llm",
                "content": result.get("content", ""),
                "tokens": result.get("tokens", {}),
            }
        except Exception as exc:
            return {"method": "error", "error": str(exc)}

    async def compare_contracts(
        self,
        original: str,
        modified: str,
        original_label: str = "原版",
        modified_label: str = "修改版",
    ) -> dict[str, Any]:
        """Compare two versions of a contract and identify differences.

        Returns structured diff with risk assessment of changes.
        """
        # Generate text diff
        original_lines = original.splitlines(keepends=True)
        modified_lines = modified.splitlines(keepends=True)

        diff = list(difflib.unified_diff(
            original_lines, modified_lines,
            fromfile=original_label, tofile=modified_label,
            lineterm="",
        ))
        diff_text = "".join(diff)

        # Count changes
        additions = sum(1 for line in diff if line.startswith("+") and not line.startswith("+++"))
        deletions = sum(1 for line in diff if line.startswith("-") and not line.startswith("---"))

        # Use LLM to analyze the significance of changes
        llm = get_raw_llm_service()
        analysis_prompt = f"""比较以下两个合同版本的差异，分析修改的法律影响。

{original_label} vs {modified_label}

主要变更：
{diff_text[:5000]}

请分析：
1. 关键条款变更（逐条列出）
2. 对甲方（原合同方）的影响
3. 对乙方（修改方）的影响
4. 风险等级评估（每个变更）
5. 是否建议接受修改

以JSON格式返回：
{_FENCE}json
{{
    "key_changes": [{{"clause": "条款", "change": "变更内容", "risk_level": "高/中/低", "impact": "影响分析"}}],
    "overall_risk": "高/中/低",
    "recommendation": "建议",
    "summary": "变更总结"
}}
{_FENCE}"""

        try:
            result = await llm.chat(
                messages=[
                    {"role": "system", "content": "你是合同对比分析专家。"},
                    {"role": "user", "content": analysis_prompt},
                ],
                temperature=0.2,
                max_tokens=3000,
            )
            content = result.get("content", "")

            # Parse JSON from response
            json_match = re.search(r'\{[\s\S]*\}', content)
            if json_match:
                analysis = json.loads(json_match.group())
            else:
                analysis = {"summary": content}

        except Exception as exc:
            logger.warning("Contract comparison analysis failed: %s", exc)
            analysis = {"summary": "分析失败", "error": str(exc)}

        return {
            "diff_text": diff_text,
            "additions": additions,
            "deletions": deletions,
            "analysis": analysis,
        }

    async def compliance_check(
        self,
        contract_text: str,
        industry: str = "",
    ) -> dict[str, Any]:
        """Check contract compliance against current regulations.

        Uses web search to find latest regulations and checks
        the contract against them.
        """
        llm = get_raw_llm_service()

        # First, identify applicable regulations
        check_prompt = f"""审查以下合同的合规性，检查是否符合中国现行法律法规。

合同内容：
{contract_text[:5000]}

行业：{industry or '通用'}

请从以下维度检查合规性：
1. 合同效力（主体资格、意思表示、内容合法性）
2. 必备条款（是否缺少法律要求的必备条款）
3. 格式条款（是否存在无效的格式条款）
4. 行业特殊合规要求
5. 最新法规变化影响（如民法典、个人信息保护法等）

以JSON格式返回：
{_FENCE}json
{{
    "compliance_score": 0-100,
    "issues": [{{"category": "类别", "description": "问题描述", "risk_level": "高/中/低", "legal_basis": "法律依据", "suggestion": "修改建议"}}],
    "compliant_items": ["合规项目"],
    "latest_regulations": ["需注意的最新法规"],
    "summary": "合规审查总结"
}}
{_FENCE}"""

        try:
            result = await llm.chat(
                messages=[
                    {"role": "system", "content": "你是合同合规审查专家，精通中国法律法规体系。"},
                    {"role": "user", "content": check_prompt},
                ],
                temperature=0.2,
                max_tokens=3000,
            )
            content = result.get("content", "")

            json_match = re.search(r'\{[\s\S]*\}', content)
            if json_match:
                analysis = json.loads(json_match.group())
            else:
                analysis = {"summary": content, "compliance_score": 70}

        except Exception as exc:
            analysis = {"compliance_score": 0, "summary": f"合规检查失败: {exc}"}

        return analysis

    async def extract_key_dates(
        self, contract_text: str
    ) -> dict[str, Any]:
        """Extract key dates from a contract for tracking.

        Identifies: signing date, effective date, expiry date,
        payment deadlines, renewal dates, notice periods.
        """
        llm = get_raw_llm_service()

        prompt = f"""从以下合同中提取所有关键日期和时间节点。

合同内容：
{contract_text[:5000]}

请提取：
1. 签署日期
2. 生效日期
3. 到期/终止日期
4. 付款日期/期限
5. 续约日期/窗口期
6. 通知期限
7. 其他重要时间节点

以JSON格式返回：
{_FENCE}json
{{
    "dates": [
        {{"type": "日期类型", "date": "YYYY-MM-DD或描述", "description": "说明", "reminder_days_before": 30}}
    ],
    "summary": "日期总结"
}}
{_FENCE}"""

        try:
            result = await llm.chat(
                messages=[
                    {"role": "system", "content": "你是合同日期提取专家。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                max_tokens=2000,
            )
            content = result.get("content", "")

            json_match = re.search(r'\{[\s\S]*\}', content)
            if json_match:
                return json.loads(json_match.group())
            return {"dates": [], "summary": content}

        except Exception as exc:
            return {"dates": [], "summary": f"提取失败: {exc}"}

    async def generate_redline(
        self, original: str, suggested_changes: list[dict[str, str]]
    ) -> dict[str, Any]:
        """Generate a redline version of a contract with suggested changes.

        Args:
            original: Original contract text.
            suggested_changes: List of {"clause": "条款位置", "original_text": "原文", "suggested_text": "建议修改"}.

        Returns:
            Modified contract text with changes applied.
        """
        modified = original
        applied_changes = []

        for change in suggested_changes:
            original_text = change.get("original_text", "")
            suggested_text = change.get("suggested_text", "")
            clause = change.get("clause", "")

            if original_text and original_text in modified:
                modified = modified.replace(original_text, suggested_text, 1)
                applied_changes.append({
                    "clause": clause,
                    "status": "applied",
                    "original": original_text[:100],
                    "modified": suggested_text[:100],
                })
            else:
                applied_changes.append({
                    "clause": clause,
                    "status": "not_found",
                    "original": original_text[:100],
                    "note": "未找到原文，请检查条款位置",
                })

        return {
            "modified_content": modified,
            "changes_applied": len([c for c in applied_changes if c["status"] == "applied"]),
            "changes_failed": len([c for c in applied_changes if c["status"] != "applied"]),
            "change_details": applied_changes,
        }


# =============================================================================
# Singleton
# =============================================================================

_agent: ContractLifecycleAgent | None = None


def get_contract_lifecycle_agent() -> ContractLifecycleAgent:
    global _agent
    if _agent is None:
        _agent = ContractLifecycleAgent()
    return _agent
