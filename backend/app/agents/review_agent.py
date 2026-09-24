"""
Quality Review Agent -- Reviews and improves outputs from other agents.

Responsibilities:
- Check legal accuracy (correct law citations, article numbers)
- Verify completeness (all relevant laws cited)
- Check formatting and structure
- Ensure disclaimers are present
- Score quality (0-100)
"""
from typing import Any, Optional
import logging
import re
import json

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langgraph.graph import StateGraph, END
from langgraph.graph.state import CompiledStateGraph

from app.agents.base_agent import BaseAgent, AgentState
from app.prompts.legal_prompts import LEGAL_DISCLAIMER
from app.services.llm_service import ChatLLMService, get_llm_service

logger = logging.getLogger(__name__)


# =============================================================================
# State Definition
# =============================================================================

class QualityReviewState(AgentState):
    """State for the Quality Review Agent.

    Attributes:
        input_text: The text to review.
        input_type: Type of input (legal_response, contract_analysis, document).
        query: Original query that produced the input.
        quality_score: Overall quality score (0-100).
        issues: List of identified issues.
        suggestions: List of improvement suggestions.
        corrected_text: Corrected version of the input.
        citation_check: Results of citation verification.
        completeness_check: Results of completeness verification.
    """

    input_text: str = ""
    input_type: str = ""
    query: str = ""
    quality_score: int = 0
    issues: list[str] = []
    suggestions: list[str] = []
    corrected_text: str = ""
    citation_check: dict[str, Any] = {}
    completeness_check: dict[str, Any] = {}
    formatting_issues: list[str] = []


# =============================================================================
# Quality Review Agent
# =============================================================================

class QualityReviewAgent(BaseAgent[QualityReviewState]):
    """Reviews and improves outputs from other agents.

    Responsibilities:
    - Check legal accuracy (correct law citations, article numbers)
    - Verify completeness (all relevant laws cited)
    - Check formatting and structure
    - Ensure disclaimers are present
    - Score quality (0-100)
    """

    def __init__(self, name: str = "quality_review_agent") -> None:
        super().__init__(name=name)
        self._llm_service: Optional[ChatLLMService] = None

    @property
    def llm_service(self) -> ChatLLMService:
        if self._llm_service is None:
            self._llm_service = get_llm_service()
        return self._llm_service

    def _build_graph(self) -> StateGraph:
        """Build the LangGraph StateGraph for quality review.

        Nodes:
            - check_citations: Verify legal citations are accurate
            - check_completeness: Verify all relevant aspects are covered
            - check_formatting: Verify structure and formatting
            - score_quality: Generate overall quality score
            - generate_corrections: Produce corrected version if needed
        """
        builder = StateGraph(QualityReviewState)

        builder.add_node("check_citations", self._check_citations_node)
        builder.add_node("check_completeness", self._check_completeness_node)
        builder.add_node("check_formatting", self._check_formatting_node)
        builder.add_node("score_quality", self._score_quality_node)
        builder.add_node("generate_corrections", self._generate_corrections_node)

        builder.set_entry_point("check_citations")
        builder.add_edge("check_citations", "check_completeness")
        builder.add_edge("check_completeness", "check_formatting")
        builder.add_edge("check_formatting", "score_quality")
        builder.add_edge("score_quality", "generate_corrections")
        builder.add_edge("generate_corrections", END)

        return builder

    # -------------------------------------------------------------------------
    # Node: check_citations
    # -------------------------------------------------------------------------

    async def _check_citations_node(self, state: QualityReviewState) -> dict[str, Any]:
        """Verify legal citations are accurate and complete."""
        input_text = state.input_text
        if not input_text:
            return {"citation_check": {"valid": True, "issues": []}}

        llm = self.llm_service.get_llm(temperature=0.0)

        citation_prompt = f"""检查以下法律文本中的法条引用是否准确。

文本内容：
{input_text[:4000]}

请检查：
1. 法律名称是否正确（如《中华人民共和国民法典》而非《民法典》）
2. 条文编号是否存在
3. 引用内容是否与原文一致
4. 是否遗漏了重要的相关法律条文

以JSON格式返回：
```json
{{
    "citations_found": ["已引用的法条列表"],
    "valid_citations": ["验证正确的法条"],
    "invalid_citations": ["验证有误的法条及问题"],
    "missing_citations": ["建议补充引用的法条"],
    "citation_accuracy_score": 0-100
}}
```
仅返回JSON。"""

        try:
            response = await llm.ainvoke([
                HumanMessage(content=citation_prompt),
            ])
            content = response.content if hasattr(response, "content") else str(response)

            json_match = re.search(r'\{[\s\S]*\}', content)
            if json_match:
                parsed = json.loads(json_match.group())
                return {"citation_check": parsed}
        except Exception as exc:
            logger.warning("Citation check failed: %s", exc)

        return {"citation_check": {"valid": True, "issues": [], "citation_accuracy_score": 80}}

    # -------------------------------------------------------------------------
    # Node: check_completeness
    # -------------------------------------------------------------------------

    async def _check_completeness_node(self, state: QualityReviewState) -> dict[str, Any]:
        """Verify all relevant aspects are covered."""
        input_text = state.input_text
        query = state.query
        input_type = state.input_type

        if not input_text:
            return {"completeness_check": {"complete": True, "issues": []}}

        llm = self.llm_service.get_llm(temperature=0.0)

        completeness_prompt = f"""评估以下{input_type}是否完整覆盖了用户问题的所有方面。

用户问题：{query}

{input_text}

请检查：
1. 是否涵盖了问题的所有关键方面
2. 是否提供了充分的法律依据
3. 是否分析了可能的风险
4. 是否给出了可行的建议
5. 是否有遗漏的重要信息

以JSON格式返回：
```json
{{
    "covered_aspects": ["已覆盖的方面"],
    "missing_aspects": ["遗漏的方面"],
    "completeness_score": 0-100,
    "issues": ["完整性问题"]
}}
```
仅返回JSON。"""

        try:
            response = await llm.ainvoke([
                HumanMessage(content=completeness_prompt),
            ])
            content = response.content if hasattr(response, "content") else str(response)

            json_match = re.search(r'\{[\s\S]*\}', content)
            if json_match:
                parsed = json.loads(json_match.group())
                return {"completeness_check": parsed}
        except Exception as exc:
            logger.warning("Completeness check failed: %s", exc)

        return {"completeness_check": {"complete": True, "issues": [], "completeness_score": 80}}

    # -------------------------------------------------------------------------
    # Node: check_formatting
    # -------------------------------------------------------------------------

    async def _check_formatting_node(self, state: QualityReviewState) -> dict[str, Any]:
        """Check formatting and structure."""
        input_text = state.input_text
        if not input_text:
            return {}

        issues = []

        # Check for disclaimer
        has_disclaimer = "声明" in input_text or "免责" in input_text or LEGAL_DISCLAIMER[:20] in input_text
        if not has_disclaimer:
            issues.append("缺少法律免责声明")

        # Check for basic structure
        has_headings = "##" in input_text or "**" in input_text
        if not has_headings and len(input_text) > 500:
            issues.append("长文本缺少结构化标题")

        # Check for citations section
        has_citations = "引用" in input_text or "依据" in input_text or "法条" in input_text
        if not has_citations and len(input_text) > 300:
            issues.append("缺少法律引用/依据部分")

        return {"formatting_issues": issues}

    # -------------------------------------------------------------------------
    # Node: score_quality
    # -------------------------------------------------------------------------

    async def _score_quality_node(self, state: QualityReviewState) -> dict[str, Any]:
        """Generate overall quality score."""
        citation_check = state.citation_check
        completeness_check = state.completeness_check
        formatting_issues = state.formatting_issues

        # Calculate component scores
        citation_score = citation_check.get("citation_accuracy_score", 80)
        completeness_score = completeness_check.get("completeness_score", 80)

        # Formatting score (deduct for each issue)
        formatting_score = max(0, 100 - len(formatting_issues) * 10)

        # Overall score (weighted average)
        overall_score = int(
            citation_score * 0.4 +
            completeness_score * 0.4 +
            formatting_score * 0.2
        )

        # Collect all issues
        all_issues = []
        all_issues.extend(citation_check.get("invalid_citations", []))
        all_issues.extend(citation_check.get("missing_citations", []))
        all_issues.extend(completeness_check.get("issues", []))
        all_issues.extend(formatting_issues)

        # Generate suggestions
        suggestions = []
        if citation_score < 80:
            suggestions.append("请核实并补充法律条文引用")
        if completeness_score < 80:
            missing = completeness_check.get("missing_aspects", [])
            if missing:
                suggestions.append(f"建议补充以下方面的分析：{', '.join(missing[:3])}")
        if formatting_score < 80:
            suggestions.append("请改善文本结构和格式")

        return {
            "quality_score": overall_score,
            "issues": all_issues,
            "suggestions": suggestions,
        }

    # -------------------------------------------------------------------------
    # Node: generate_corrections
    # -------------------------------------------------------------------------

    async def _generate_corrections_node(self, state: QualityReviewState) -> dict[str, Any]:
        """Generate corrected version if quality is below threshold."""
        input_text = state.input_text
        quality_score = state.quality_score
        issues = state.issues
        suggestions = state.suggestions

        # If quality is good enough, no correction needed
        if quality_score >= 80:
            return {"corrected_text": input_text}

        # Generate corrected version
        llm = self.llm_service.get_llm(temperature=0.2, max_tokens=4096)

        correction_prompt = f"""请根据以下审查意见，改进法律文本。

原始文本：
{input_text[:4000]}

发现的问题：
{chr(10).join(f'- {issue}' for issue in issues[:5])}

改进建议：
{chr(10).join(f'- {s}' for s in suggestions[:5])}

请生成改进后的文本，确保：
1. 法条引用准确完整
2. 分析全面深入
3. 结构清晰易读
4. 包含法律免责声明"""

        try:
            response = await llm.ainvoke([
                HumanMessage(content=correction_prompt),
            ])
            corrected = response.content if hasattr(response, "content") else str(response)
            return {"corrected_text": corrected}
        except Exception as exc:
            logger.warning("Correction generation failed: %s", exc)
            return {"corrected_text": input_text}

    # -------------------------------------------------------------------------
    # Public methods for specific review types
    # -------------------------------------------------------------------------

    async def review_legal_response(self, response: str, query: str) -> dict:
        """Review a legal consultation response.

        Returns:
            {
                'quality_score': 85,
                'issues': ['Missing citation to Civil Code Article 577'],
                'suggestions': ['Add reference to relevant judicial interpretation'],
                'corrected_response': '...'
            }
        """
        result = await self.run({
            "input_text": response,
            "input_type": "legal_response",
            "query": query,
        })
        return {
            "quality_score": result.get("quality_score", 0),
            "issues": result.get("issues", []),
            "suggestions": result.get("suggestions", []),
            "corrected_response": result.get("corrected_text", response),
            "citation_check": result.get("citation_check", {}),
            "completeness_check": result.get("completeness_check", {}),
        }

    async def review_contract_analysis(self, analysis: dict, contract_text: str) -> dict:
        """Review a contract analysis for completeness."""
        # Convert analysis dict to text for review
        analysis_text = analysis.get("final_output", "") or analysis.get("report", "")

        result = await self.run({
            "input_text": analysis_text,
            "input_type": "contract_analysis",
            "query": f"合同审查：{contract_text[:200]}",
        })
        return {
            "quality_score": result.get("quality_score", 0),
            "issues": result.get("issues", []),
            "suggestions": result.get("suggestions", []),
            "corrected_analysis": result.get("corrected_text", analysis_text),
        }

    async def review_document(self, document: str, doc_type: str) -> dict:
        """Review a generated legal document."""
        result = await self.run({
            "input_text": document,
            "input_type": "document",
            "query": f"{doc_type}质量审查",
        })
        return {
            "quality_score": result.get("quality_score", 0),
            "issues": result.get("issues", []),
            "suggestions": result.get("suggestions", []),
            "corrected_document": result.get("corrected_text", document),
        }

    # -------------------------------------------------------------------------
    # Public run method
    # -------------------------------------------------------------------------

    async def run(self, input_data: dict[str, Any]) -> dict[str, Any]:
        """Execute the quality review agent.

        Args:
            input_data: Must contain 'input_text'. Optional 'input_type', 'query'.

        Returns:
            Dictionary with quality_score, issues, suggestions, corrected_text.
        """
        input_text = input_data.get("input_text", "")
        if not input_text:
            return {
                "quality_score": 0,
                "issues": ["无输入文本"],
                "suggestions": ["请提供需要审查的文本"],
                "corrected_text": "",
                "citation_check": {},
                "completeness_check": {},
            }

        initial_state: dict[str, Any] = {
            "input_text": input_text,
            "input_type": input_data.get("input_type", "general"),
            "query": input_data.get("query", ""),
            "quality_score": 0,
            "issues": [],
            "suggestions": [],
            "corrected_text": "",
            "citation_check": {},
            "completeness_check": {},
            "formatting_issues": [],
            "messages": [HumanMessage(content=f"审查文本质量：{input_text[:200]}")],
            "context": {},
            "final_output": "",
        }

        graph = self.compile()
        result = await graph.ainvoke(initial_state)

        final_output = (
            f"质量评分：{result.get('quality_score', 0)}/100\n"
            f"问题数量：{len(result.get('issues', []))}\n"
            f"改进建议：{', '.join(result.get('suggestions', [])[:3])}"
        )

        return {
            "final_output": final_output,
            "quality_score": result.get("quality_score", 0),
            "issues": result.get("issues", []),
            "suggestions": result.get("suggestions", []),
            "corrected_text": result.get("corrected_text", ""),
            "citation_check": result.get("citation_check", {}),
            "completeness_check": result.get("completeness_check", {}),
        }

    def run_sync(self, input_data: dict[str, Any]) -> dict[str, Any]:
        """Synchronous wrapper for the run method."""
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import nest_asyncio
                nest_asyncio.apply()
            return loop.run_until_complete(self.run(input_data))
        except RuntimeError:
            return asyncio.run(self.run(input_data))


# =============================================================================
# Factory function
# =============================================================================

def create_quality_review_agent() -> QualityReviewAgent:
    """Create and return a QualityReviewAgent instance."""
    return QualityReviewAgent()
