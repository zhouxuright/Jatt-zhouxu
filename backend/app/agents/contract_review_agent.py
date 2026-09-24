"""
Contract Review Agent - Analyzes contracts for legal risks using LangGraph.

Flow: parse_contract -> extract_clauses -> identify_risks -> generate_report
"""
from typing import Any, Optional, TypedDict
import asyncio
import json
import logging
import re

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langgraph.graph import StateGraph, END
from pydantic import BaseModel, Field

from app.agents.base_agent import BaseAgent, AgentState
from app.prompts.legal_prompts import (
    CONTRACT_REVIEW_SYSTEM_PROMPT,
    CONTRACT_REVIEW_TEMPLATE,
    RISK_ANALYSIS_PROMPT,
    LEGAL_DISCLAIMER,
)
from app.services.llm_service import ChatLLMService, get_llm_service

logger = logging.getLogger(__name__)


# =============================================================================
# Risk Categories
# =============================================================================

RISK_CATEGORIES: list[str] = [
    "违约责任",
    "知识产权",
    "保密条款",
    "竞业限制",
    "管辖权",
    "付款条款",
    "终止条款",
    "免责条款",
]


class RiskItem(BaseModel):
    """A single risk item identified during contract review.

    Attributes:
        clause_text: The original clause text that contains the risk.
        risk_category: The category of risk (e.g., 违约责任, 知识产权).
        risk_level: Risk severity level: high, medium, or low.
        risk_description: Detailed description of the identified risk.
        legal_basis: Relevant legal provisions that apply.
        suggestion: Recommended modification or mitigation.
        alternative: Alternative approach if modification is not possible.
    """

    clause_text: str = Field(default="")
    risk_category: str = Field(default="")
    risk_level: str = Field(default="low")  # high, medium, low
    risk_description: str = Field(default="")
    legal_basis: str = Field(default="")
    suggestion: str = Field(default="")
    alternative: str = Field(default="")


# =============================================================================
# State Definition
# =============================================================================

class ContractReviewState(AgentState):
    """State for the Contract Review Agent.

    Attributes:
        contract_text: The full contract text to review.
        review_focus: Specific areas to focus on during review.
        parsed_clauses: Clauses extracted from the contract.
        risk_items: Identified risk items.
        risk_score: Overall risk score (0-100).
        risk_level: Overall risk level (high/medium/low).
        report: The generated review report.
        missing_clauses: Important clauses missing from the contract.
    """

    contract_text: str = Field(default="")
    review_focus: str = Field(default="")
    parsed_clauses: list[dict[str, Any]] = Field(default_factory=list)
    risk_items: list[dict[str, Any]] = Field(default_factory=list)
    risk_score: int = Field(default=0)
    risk_level: str = Field(default="low")
    report: str = Field(default="")
    missing_clauses: list[str] = Field(default_factory=list)


# =============================================================================
# Contract Review Agent
# =============================================================================

class ContractReviewAgent(BaseAgent[ContractReviewState]):
    """Agent for automated contract review and risk analysis.

    Workflow:
        1. parse_contract - Parse and structure the contract text
        2. extract_clauses - Extract individual clauses by category
        3. identify_risks - Analyze each clause for legal risks
        4. generate_report - Produce a comprehensive review report
    """

    def __init__(self, name: str = "contract_review_agent") -> None:
        super().__init__(name=name)
        self._llm_service: Optional[ChatLLMService] = None

    @property
    def llm_service(self) -> ChatLLMService:
        if self._llm_service is None:
            self._llm_service = get_llm_service()
        return self._llm_service

    def _build_graph(self) -> StateGraph:
        """Build the LangGraph StateGraph for contract review.

        Nodes:
            - parse_contract: Parse the contract text into structured format
            - extract_clauses: Extract clauses by category
            - identify_risks: Analyze each clause for risks
            - generate_report: Generate the final review report
        """
        builder = StateGraph(ContractReviewState)

        builder.add_node("parse_contract", self._parse_contract_node)
        builder.add_node("extract_clauses", self._extract_clauses_node)
        builder.add_node("identify_risks", self._identify_risks_node)
        builder.add_node("generate_report", self._generate_report_node)

        builder.set_entry_point("parse_contract")
        builder.add_edge("parse_contract", "extract_clauses")
        builder.add_edge("extract_clauses", "identify_risks")
        builder.add_edge("identify_risks", "generate_report")
        builder.add_edge("generate_report", END)

        return builder

    # -------------------------------------------------------------------------
    # Node: parse_contract
    # -------------------------------------------------------------------------

    async def _parse_contract_node(self, state: ContractReviewState) -> dict[str, Any]:
        """Parse the contract text and extract basic information."""
        contract_text = state.contract_text
        if not contract_text:
            return {"parsed_clauses": []}

        llm = self.llm_service.get_llm(temperature=0.0)
        parse_prompt = f"""请解析以下合同文本，提取合同基本信息。

合同文本：
{contract_text[:8000]}

请以JSON格式返回：
```json
{{
    "contract_type": "合同类型",
    "parties": ["甲方：...", "乙方：..."],
    "contract_amount": "合同金额",
    "contract_term": "合同期限",
    "signing_date": "签署日期",
    "clause_count": 条款数量
}}
```
仅返回JSON，不要附加说明。"""

        try:
            response = await llm.ainvoke([
                SystemMessage(content=CONTRACT_REVIEW_SYSTEM_PROMPT),
                HumanMessage(content=parse_prompt),
            ])
            content = response.content if hasattr(response, "content") else str(response)
            # Extract JSON from response
            import re
            json_match = re.search(r'\{[\s\S]*\}', content)
            if json_match:
                parsed = json.loads(json_match.group())
                return {"parsed_clauses": [parsed]}
        except Exception:
            pass

        return {"parsed_clauses": [{"contract_type": "未知", "parties": []}]}

    # -------------------------------------------------------------------------
    # Node: extract_clauses
    # -------------------------------------------------------------------------

    async def _extract_clauses_node(self, state: ContractReviewState) -> dict[str, Any]:
        """Extract individual clauses from the contract by risk category."""
        contract_text = state.contract_text
        if not contract_text:
            return {"parsed_clauses": []}

        llm = self.llm_service.get_llm(temperature=0.0)
        categories_str = "、".join(RISK_CATEGORIES)

        extract_prompt = f"""请从以下合同文本中提取与各风险类别相关的条款。

合同文本：
{contract_text[:8000]}

风险类别：{categories_str}

请以JSON格式返回，每个类别包含提取到的条款原文：
```json
[
    {{
        "category": "风险类别",
        "clause_text": "条款原文",
        "clause_location": "条款位置（如第X条）",
        "is_present": true/false
    }}
]
```
仅返回JSON数组，不要附加说明。"""

        try:
            response = await llm.ainvoke([
                SystemMessage(content=CONTRACT_REVIEW_SYSTEM_PROMPT),
                HumanMessage(content=extract_prompt),
            ])
            content = response.content if hasattr(response, "content") else str(response)
            import re
            json_match = re.search(r'\[[\s\S]*\]', content)
            if json_match:
                clauses = json.loads(json_match.group())
                return {"parsed_clauses": clauses}
        except Exception:
            pass

        return {"parsed_clauses": []}

    # -------------------------------------------------------------------------
    # Node: identify_risks
    # -------------------------------------------------------------------------

    async def _identify_risks_node(self, state: ContractReviewState) -> dict[str, Any]:
        """Analyze each extracted clause for legal risks.

        Category analyses run concurrently via asyncio.gather so total wall time
        approaches a single LLM call instead of summing all of them.
        """
        parsed_clauses = state.parsed_clauses
        if not parsed_clauses:
            return {"risk_items": [], "risk_score": 0, "risk_level": "low", "missing_clauses": []}

        llm = self.llm_service.get_llm(temperature=0.2, max_tokens=4096)
        missing_clauses: list[str] = []

        # Pre-fetch relevant law articles from RAG for the whole contract
        # so we can cite real legal basis for identified risks
        contract_text = state.contract_text
        related_laws = await self._retrieve_related_laws(contract_text)

        # Collect categories that have a present clause needing analysis;
        # categories without one are recorded as missing clauses
        pending: list[tuple[str, str]] = []
        for category in RISK_CATEGORIES:
            matching = [
                c for c in parsed_clauses
                if c.get("category", "") == category
            ]

            if not matching or not matching[0].get("is_present", False):
                missing_clauses.append(category)
                continue

            clause_text = matching[0].get("clause_text", "")
            if clause_text:
                pending.append((category, clause_text))

        async def analyze(category: str, clause_text: str) -> dict[str, Any] | None:
            risk_prompt = RISK_ANALYSIS_PROMPT.format(clause_text=clause_text)
            try:
                response = await llm.ainvoke([
                    SystemMessage(content=CONTRACT_REVIEW_SYSTEM_PROMPT),
                    HumanMessage(content=risk_prompt),
                ])
            except Exception as exc:
                logger.warning("Risk analysis failed for category %s: %s", category, exc)
                return None

            content = response.content if hasattr(response, "content") else str(response)

            # 提示词要求按 **风险等级/风险描述/法律依据/修改建议/替代方案** 分字段输出；
            # 这里做结构化解析，避免把整篇 markdown 塞进 risk_description、
            # 且让前端「修改建议」列真正有内容。
            parsed = self._parse_risk_analysis(content)

            risk_level = "low"
            level_raw = parsed.get("risk_level_raw", "")
            if level_raw.startswith("高") or "高风险" in content or "风险等级：高" in content:
                risk_level = "high"
            elif level_raw.startswith("中") or "中风险" in content or "风险等级：中" in content:
                risk_level = "medium"

            return {
                "clause_text": clause_text,
                "risk_category": category,
                "risk_level": risk_level,
                "risk_description": parsed.get("risk_description") or content,
                "legal_basis": parsed.get("legal_basis")
                or self._find_legal_basis_for_category(category, clause_text, related_laws),
                "suggestion": parsed.get("suggestion", ""),
                "alternative": parsed.get("alternative", ""),
            }

        results = await asyncio.gather(
            *(analyze(category, clause_text) for category, clause_text in pending)
        )
        risk_items: list[dict[str, Any]] = [r for r in results if r is not None]

        # Calculate overall risk score
        risk_score = self._calculate_risk_score(risk_items)
        risk_level = self._determine_risk_level(risk_score)

        return {
            "risk_items": risk_items,
            "risk_score": risk_score,
            "risk_level": risk_level,
            "missing_clauses": missing_clauses,
        }

    @staticmethod
    def _parse_risk_analysis(content: str) -> dict[str, str]:
        """把 LLM 的风险分析文本解析成结构化字段。

        期望（见 ``RISK_ANALYSIS_PROMPT``）::

            - **风险等级**：高/中/低
            - **风险描述**：...
            - **法律依据**：...
            - **修改建议**：...
            - **替代方案**：...

        模型输出格式并不稳定（可能不带加粗、可能用「风险等级:」半角冒号），
        因此按「字段名 + 冒号」做行级扫描，并把冒号后到下一个字段名之间的内容
        归入该字段。任何字段都解析不到时返回空 dict，由调用方回退到整段文本，
        确保不丢信息。
        """
        field_map = {
            "风险等级": "risk_level_raw",
            "风险描述": "risk_description",
            "法律依据": "legal_basis",
            "修改建议": "suggestion",
            "替代方案": "alternative",
        }
        names = "|".join(field_map)
        header_re = re.compile(
            rf"^[\s\-*>#]*\**\s*(?:【\s*)?({names})(?:\s*】)?\s*\**\s*[：:]\s*(.*)$"
        )

        buckets: dict[str, list[str]] = {}
        current: str | None = None
        for raw_line in content.splitlines():
            m = header_re.match(raw_line.strip())
            if m:
                current = field_map[m.group(1)]
                buckets.setdefault(current, [])
                rest = m.group(2).strip()
                if rest:
                    buckets[current].append(rest)
            elif current:
                buckets[current].append(raw_line.strip())

        parsed: dict[str, str] = {}
        for key, lines in buckets.items():
            text = "\n".join(x for x in lines if x).strip()
            if text:
                parsed[key] = text
        return parsed

    async def _retrieve_related_laws(self, contract_text: str) -> list[dict[str, Any]]:
        """Retrieve relevant law articles from RAG for the contract text."""
        try:
            from app.rag.advanced_retriever import get_rag_pipeline
            pipeline = get_rag_pipeline()
            # Use first 2000 chars of contract as query to find relevant laws
            query_text = contract_text[:2000] if contract_text else ""
            if not query_text:
                return []
            results = pipeline.retrieve(
                query=query_text,
                top_k=10,
                use_bm25=True,
                use_vector=True,
                use_reranker=True,
            )
            return results
        except Exception as exc:
            logger.warning("Failed to retrieve related laws for contract review: %s", exc)
            return []

    def _find_legal_basis_for_category(
        self,
        category: str,
        clause_text: str,
        related_laws: list[dict[str, Any]],
    ) -> str:
        """Find the most relevant legal basis from RAG results for a risk category.

        Maps risk categories to law categories/keywords and finds the best match.
        """
        # Map risk categories to law keywords
        category_keywords: dict[str, list[str]] = {
            "违约责任": ["违约", "合同", "民法典", "履行", "赔偿"],
            "知识产权": ["知识产权", "著作权", "专利", "商标"],
            "保密条款": ["保密", "商业秘密", "保密义务"],
            "竞业限制": ["竞业", "竞业限制", "劳动合同", "劳动法"],
            "管辖权": ["管辖", "仲裁", "诉讼", "争议解决"],
            "付款条款": ["付款", "支付", "价款", "报酬", "合同"],
            "终止条款": ["解除", "终止", "合同", "民法典"],
            "免责条款": ["免责", "不可抗力", "责任", "赔偿"],
        }

        keywords = category_keywords.get(category, [])
        if not keywords or not related_laws:
            return ""

        # Find the best matching law article
        best_match = None
        best_score = 0

        for law in related_laws:
            law_content = law.get("content", "")
            law_name = law.get("law_name", "")
            article_num = law.get("article_number", "")
            tags = law.get("tags", "")
            combined = f"{law_name} {article_num} {law_content} {tags}"

            match_count = sum(1 for kw in keywords if kw in combined)
            if match_count > best_score:
                best_score = match_count
                best_match = law

        if best_match and best_score > 0:
            law_name = best_match.get("law_name", "")
            article_num = best_match.get("article_number", "")
            return f"《{law_name}》{article_num}"

        return ""

    def _calculate_risk_score(self, risk_items: list[dict[str, Any]]) -> int:
        """Calculate overall risk score from individual risk items."""
        if not risk_items:
            return 0

        weights = {"high": 10, "medium": 5, "low": 2}
        total = sum(weights.get(item.get("risk_level", "low"), 2) for item in risk_items)
        max_score = len(risk_items) * 10
        return min(int((total / max_score) * 100) if max_score > 0 else 0, 100)

    def _determine_risk_level(self, score: int) -> str:
        """Determine risk level from score."""
        if score >= 70:
            return "high"
        elif score >= 40:
            return "medium"
        return "low"

    # -------------------------------------------------------------------------
    # Node: generate_report
    # -------------------------------------------------------------------------

    async def _generate_report_node(self, state: ContractReviewState) -> dict[str, Any]:
        """Generate the final contract review report."""
        contract_text = state.contract_text
        risk_items = state.risk_items
        risk_score = state.risk_score
        risk_level = state.risk_level
        missing_clauses = state.missing_clauses
        review_focus = state.review_focus or "全面审查"

        # Build risk table
        risk_table = self._build_risk_table(risk_items)

        # Build missing clauses section
        missing_section = ""
        if missing_clauses:
            missing_section = "\n".join(
                f"- {cat}" for cat in missing_clauses
            )

        llm = self.llm_service.get_llm(temperature=0.3, max_tokens=4096)
        report_prompt = f"""请基于以下合同审查分析结果，生成一份完整的审查报告。

## 合同文本
{contract_text[:5000]}

## 审查焦点
{review_focus}

## 风险分析结果
综合风险等级：{risk_level}
综合风险评分：{risk_score}/100

## 识别的风险条款
{risk_table}

## 缺失的重要条款
{missing_section if missing_section else '无缺失条款'}

{CONTRACT_REVIEW_TEMPLATE.format(
    contract_text=contract_text,
    review_focus=review_focus,
)}

请按照上述模板格式生成完整的审查报告。"""

        try:
            response = await llm.ainvoke([
                SystemMessage(content=CONTRACT_REVIEW_SYSTEM_PROMPT),
                HumanMessage(content=report_prompt),
            ])
            report = response.content if hasattr(response, "content") else str(response)
        except Exception as e:
            report = f"生成审查报告时出现错误：{str(e)}"

        final_output = report + LEGAL_DISCLAIMER

        return {
            "report": report,
            "final_output": final_output,
        }

    def _build_risk_table(self, risk_items: list[dict[str, Any]]) -> str:
        """Build a formatted risk table from risk items."""
        if not risk_items:
            return "（未识别到风险条款）"

        rows: list[str] = []
        for i, item in enumerate(risk_items, 1):
            level_icon = {"high": "高", "medium": "中", "low": "低"}.get(
                item.get("risk_level", "low"), "低"
            )
            rows.append(
                f"| {i} | {item.get('clause_text', '')[:50]}... | "
                f"{item.get('risk_category', '')} | {level_icon} | "
                f"{item.get('risk_description', '')[:100]}... | "
                f"{item.get('suggestion', '')[:80]}... |"
            )

        header = "| 序号 | 条款原文 | 风险类别 | 风险等级 | 风险描述 | 修改建议 |\n"
        separator = "|------|----------|----------|----------|----------|----------|\n"
        return header + separator + "\n".join(rows)

    # -------------------------------------------------------------------------
    # Public run method
    # -------------------------------------------------------------------------

    async def run(self, input_data: dict[str, Any]) -> dict[str, Any]:
        """Execute the contract review agent.

        Args:
            input_data: Must contain 'contract_text' key. Optional 'review_focus'.

        Returns:
            Dictionary with review results including risk_items, risk_score, report.
        """
        contract_text = input_data.get("contract_text", "")
        if not contract_text:
            return {
                "final_output": "请提供需要审查的合同文本。",
                "risk_items": [],
                "risk_score": 0,
                "risk_level": "low",
                "report": "",
                "missing_clauses": [],
            }

        initial_state: dict[str, Any] = {
            "contract_text": contract_text,
            "review_focus": input_data.get("review_focus", ""),
            "parsed_clauses": [],
            "risk_items": [],
            "risk_score": 0,
            "risk_level": "low",
            "report": "",
            "missing_clauses": [],
            "messages": [HumanMessage(content=f"请审查以下合同：\n{contract_text[:500]}...")],
            "context": {},
            "final_output": "",
        }

        graph = self.compile()
        result = await graph.ainvoke(initial_state)

        return {
            "final_output": result.get("final_output", ""),
            "risk_items": result.get("risk_items", []),
            "risk_score": result.get("risk_score", 0),
            "risk_level": result.get("risk_level", "low"),
            "report": result.get("report", ""),
            "missing_clauses": result.get("missing_clauses", []),
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

def create_contract_review_agent() -> ContractReviewAgent:
    """Create and return a ContractReviewAgent instance."""
    return ContractReviewAgent()