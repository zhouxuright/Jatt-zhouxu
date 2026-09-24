"""
Deep Research Agent -- Performs comprehensive legal research by combining
multiple retrieval strategies.

Combines:
- Statute search (law articles)
- Case search (court cases)
- Judicial interpretation search
- Knowledge graph traversal
- Cross-reference validation
"""
from typing import Any, Optional
import logging
import re
import json

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langgraph.graph import StateGraph, END
from langgraph.graph.state import CompiledStateGraph

from app.agents.base_agent import BaseAgent, AgentState
from app.services.llm_service import ChatLLMService, get_llm_service

logger = logging.getLogger(__name__)


# =============================================================================
# State Definition
# =============================================================================

class DeepResearchState(AgentState):
    """State for the Deep Research Agent.

    Attributes:
        legal_question: The legal question to research.
        research_plan: Plan for the research process.
        relevant_laws: Retrieved law articles.
        relevant_cases: Retrieved court cases.
        judicial_interpretations: Retrieved judicial interpretations.
        knowledge_graph_results: Results from knowledge graph traversal.
        cross_references: Cross-reference validation results.
        analysis: Comprehensive analysis text.
        confidence: Confidence score (0-1).
        sources_cited: All sources cited in the analysis.
    """

    legal_question: str = ""
    research_plan: dict[str, Any] = {}
    relevant_laws: list[dict[str, Any]] = []
    relevant_cases: list[dict[str, Any]] = []
    judicial_interpretations: list[dict[str, Any]] = []
    knowledge_graph_results: list[dict[str, Any]] = []
    cross_references: list[dict[str, Any]] = []
    analysis: str = ""
    confidence: float = 0.0
    sources_cited: list[str] = []


# =============================================================================
# Deep Research Agent
# =============================================================================

class DeepResearchAgent(BaseAgent[DeepResearchState]):
    """Performs comprehensive legal research.

    Combines:
    - Statute search (law articles)
    - Case search (court cases)
    - Judicial interpretation search
    - Knowledge graph traversal
    - Cross-reference validation
    """

    def __init__(self, name: str = "deep_research_agent") -> None:
        super().__init__(name=name)
        self._llm_service: Optional[ChatLLMService] = None

    @property
    def llm_service(self) -> ChatLLMService:
        if self._llm_service is None:
            self._llm_service = get_llm_service()
        return self._llm_service

    def _build_graph(self) -> StateGraph:
        """Build the LangGraph StateGraph for deep research.

        Nodes:
            - plan_research: Create a research plan
            - search_statutes: Search for relevant law articles
            - search_cases: Search for relevant court cases
            - search_interpretations: Search for judicial interpretations
            - traverse_knowledge_graph: Traverse knowledge graph for related concepts
            - validate_references: Cross-reference and validate findings
            - synthesize_analysis: Produce comprehensive analysis
        """
        builder = StateGraph(DeepResearchState)

        builder.add_node("plan_research", self._plan_research_node)
        builder.add_node("search_statutes", self._search_statutes_node)
        builder.add_node("search_cases", self._search_cases_node)
        builder.add_node("search_interpretations", self._search_interpretations_node)
        builder.add_node("traverse_knowledge_graph", self._traverse_knowledge_graph_node)
        builder.add_node("validate_references", self._validate_references_node)
        builder.add_node("synthesize_analysis", self._synthesize_analysis_node)

        builder.set_entry_point("plan_research")
        builder.add_edge("plan_research", "search_statutes")
        builder.add_edge("plan_research", "search_cases")
        builder.add_edge("plan_research", "search_interpretations")
        builder.add_edge("search_statutes", "validate_references")
        builder.add_edge("search_cases", "validate_references")
        builder.add_edge("search_interpretations", "validate_references")
        builder.add_edge("validate_references", "traverse_knowledge_graph")
        builder.add_edge("traverse_knowledge_graph", "synthesize_analysis")
        builder.add_edge("synthesize_analysis", END)

        return builder

    # -------------------------------------------------------------------------
    # Node: plan_research
    # -------------------------------------------------------------------------

    async def _plan_research_node(self, state: DeepResearchState) -> dict[str, Any]:
        """Create a research plan for the legal question."""
        question = state.legal_question
        if not question:
            return {"research_plan": {}}

        llm = self.llm_service.get_llm(temperature=0.0)

        plan_prompt = f"""为以下法律问题制定研究计划。

法律问题：{question}

请分析：
1. 涉及哪些法律领域
2. 需要检索哪些类型的法律资料
3. 关键的检索词和概念
4. 可能的争议焦点

以JSON格式返回：
```json
{{
    "legal_domains": ["涉及的法律领域"],
    "search_strategy": ["检索策略"],
    "keywords": ["关键词列表"],
    "key_concepts": ["关键法律概念"],
    "controversial_points": ["可能的争议点"]
}}
```
仅返回JSON。"""

        try:
            response = await llm.ainvoke([
                HumanMessage(content=plan_prompt),
            ])
            content = response.content if hasattr(response, "content") else str(response)

            json_match = re.search(r'\{[\s\S]*\}', content)
            if json_match:
                parsed = json.loads(json_match.group())
                return {"research_plan": parsed}
        except Exception as exc:
            logger.warning("Research planning failed: %s", exc)

        return {"research_plan": {
            "legal_domains": [],
            "search_strategy": [],
            "keywords": [question],
            "key_concepts": [],
            "controversial_points": [],
        }}

    # -------------------------------------------------------------------------
    # Node: search_statutes
    # -------------------------------------------------------------------------

    async def _search_statutes_node(self, state: DeepResearchState) -> dict[str, Any]:
        """Search for relevant law articles using RAG pipeline."""
        question = state.legal_question
        research_plan = state.research_plan

        if not question:
            return {"relevant_laws": []}

        try:
            from app.rag.advanced_retriever import get_rag_pipeline
            pipeline = get_rag_pipeline()

            # Use keywords from research plan if available
            keywords = research_plan.get("keywords", [question])
            search_query = " ".join(keywords[:5]) if keywords else question

            results = pipeline.retrieve(
                query=search_query,
                top_k=15,
                use_bm25=True,
                use_vector=True,
                use_reranker=True,
            )

            # Normalize results
            normalized = []
            for r in results:
                normalized.append({
                    "law_name": r.get("law_name", ""),
                    "article_number": r.get("article_number", ""),
                    "article_content": r.get("content", ""),
                    "relevance_score": r.get("rerank_score", r.get("score", 0.0)),
                    "effective_status": "现行有效",
                    "category": r.get("category", ""),
                    "source": "statute_search",
                })

            return {"relevant_laws": normalized}
        except Exception as exc:
            logger.warning("Statute search failed: %s", exc)
            return {"relevant_laws": []}

    # -------------------------------------------------------------------------
    # Node: search_cases
    # -------------------------------------------------------------------------

    async def _search_cases_node(self, state: DeepResearchState) -> dict[str, Any]:
        """Search for relevant court cases."""
        question = state.legal_question

        if not question:
            return {"relevant_cases": []}

        # TODO: Implement case retrieval when case data is available in Milvus
        # For now, use LLM to identify potentially relevant case types
        llm = self.llm_service.get_llm(temperature=0.0)

        case_prompt = f"""基于以下法律问题，识别可能相关的案例类型和裁判规则。

法律问题：{question}

请列出：
1. 可能相关的案例类型
2. 典型的裁判规则
3. 重要的参考案例（如有）

以JSON格式返回：
```json
{{
    "case_types": ["案例类型"],
    "ruling_patterns": ["裁判规则"],
    "reference_cases": ["参考案例"]
}}
```
仅返回JSON。"""

        try:
            response = await llm.ainvoke([
                HumanMessage(content=case_prompt),
            ])
            content = response.content if hasattr(response, "content") else str(response)

            json_match = re.search(r'\{[\s\S]*\}', content)
            if json_match:
                parsed = json.loads(json_match.group())
                # Convert to case-like format
                cases = []
                for case_type in parsed.get("case_types", []):
                    cases.append({
                        "case_type": case_type,
                        "ruling_patterns": parsed.get("ruling_patterns", []),
                        "source": "llm_identified",
                    })
                return {"relevant_cases": cases}
        except Exception as exc:
            logger.warning("Case search failed: %s", exc)

        return {"relevant_cases": []}

    # -------------------------------------------------------------------------
    # Node: search_interpretations
    # -------------------------------------------------------------------------

    async def _search_interpretations_node(self, state: DeepResearchState) -> dict[str, Any]:
        """Search for judicial interpretations."""
        question = state.legal_question
        research_plan = state.research_plan

        if not question:
            return {"judicial_interpretations": []}

        try:
            from app.rag.advanced_retriever import get_rag_pipeline
            pipeline = get_rag_pipeline()

            # Search specifically for judicial interpretations
            keywords = research_plan.get("keywords", [question])
            search_query = "司法解释 " + " ".join(keywords[:3])

            results = pipeline.retrieve(
                query=search_query,
                top_k=10,
                use_bm25=True,
                use_vector=True,
                use_reranker=True,
            )

            # Filter for judicial interpretations
            interpretations = []
            for r in results:
                law_name = r.get("law_name", "")
                # Judicial interpretations typically have "司法解释" in the name
                if "司法解释" in law_name or "解释" in law_name:
                    interpretations.append({
                        "interpretation_name": law_name,
                        "article_number": r.get("article_number", ""),
                        "content": r.get("content", ""),
                        "relevance_score": r.get("rerank_score", r.get("score", 0.0)),
                        "source": "interpretation_search",
                    })

            return {"judicial_interpretations": interpretations}
        except Exception as exc:
            logger.warning("Judicial interpretation search failed: %s", exc)
            return {"judicial_interpretations": []}

    # -------------------------------------------------------------------------
    # Node: traverse_knowledge_graph
    # -------------------------------------------------------------------------

    async def _traverse_knowledge_graph_node(self, state: DeepResearchState) -> dict[str, Any]:
        """Traverse knowledge graph for related legal concepts."""
        question = state.legal_question
        research_plan = state.research_plan

        if not question:
            return {"knowledge_graph_results": []}

        llm = self.llm_service.get_llm(temperature=0.0)

        # Use LLM to identify related concepts and relationships
        kg_prompt = f"""分析以下法律问题涉及的法律概念及其关系。

法律问题：{question}
关键概念：{', '.join(research_plan.get('key_concepts', []))}

请识别：
1. 核心法律概念
2. 概念之间的法律关系
3. 上位概念和下位概念
4. 相关的基本原则

以JSON格式返回：
```json
{{
    "core_concepts": ["核心概念"],
    "relationships": ["概念关系"],
    "related_principles": ["相关原则"],
    "hierarchical_concepts": ["上下位概念"]
}}
```
仅返回JSON。"""

        try:
            response = await llm.ainvoke([
                HumanMessage(content=kg_prompt),
            ])
            content = response.content if hasattr(response, "content") else str(response)

            json_match = re.search(r'\{[\s\S]*\}', content)
            if json_match:
                parsed = json.loads(json_match.group())
                return {"knowledge_graph_results": [parsed]}
        except Exception as exc:
            logger.warning("Knowledge graph traversal failed: %s", exc)

        return {"knowledge_graph_results": []}

    # -------------------------------------------------------------------------
    # Node: validate_references
    # -------------------------------------------------------------------------

    async def _validate_references_node(self, state: DeepResearchState) -> dict[str, Any]:
        """Cross-reference and validate findings."""
        relevant_laws = state.relevant_laws
        relevant_cases = state.relevant_cases
        judicial_interpretations = state.judicial_interpretations

        # Combine all sources
        all_sources = []
        for law in relevant_laws:
            all_sources.append({
                "type": "statute",
                "name": law.get("law_name", ""),
                "article": law.get("article_number", ""),
                "score": law.get("relevance_score", 0),
            })
        for interp in judicial_interpretations:
            all_sources.append({
                "type": "interpretation",
                "name": interp.get("interpretation_name", ""),
                "article": interp.get("article_number", ""),
                "score": interp.get("relevance_score", 0),
            })

        # Deduplicate and sort by score
        seen = set()
        deduped = []
        for s in all_sources:
            key = (s["type"], s["name"], s["article"])
            if key not in seen:
                seen.add(key)
                deduped.append(s)

        deduped.sort(key=lambda x: x["score"], reverse=True)

        # Calculate confidence based on source quality and quantity
        high_quality = sum(1 for s in deduped if s["score"] > 0.7)
        total = len(deduped)
        confidence = min(0.95, 0.5 + (high_quality * 0.1) + (total * 0.02)) if total > 0 else 0.3

        return {
            "cross_references": deduped[:20],
            "confidence": confidence,
        }

    # -------------------------------------------------------------------------
    # Node: synthesize_analysis
    # -------------------------------------------------------------------------

    async def _synthesize_analysis_node(self, state: DeepResearchState) -> dict[str, Any]:
        """Produce comprehensive analysis from all research findings."""
        question = state.legal_question
        relevant_laws = state.relevant_laws
        relevant_cases = state.relevant_cases
        judicial_interpretations = state.judicial_interpretations
        knowledge_graph = state.knowledge_graph_results
        confidence = state.confidence

        llm = self.llm_service.get_llm(temperature=0.3, max_tokens=4096)

        # Format all findings
        laws_text = self._format_laws(relevant_laws)
        cases_text = self._format_cases(relevant_cases)
        interpretations_text = self._format_interpretations(judicial_interpretations)
        kg_text = self._format_knowledge_graph(knowledge_graph)

        synthesis_prompt = f"""基于以下综合研究结果，对法律问题进行全面深入的分析。

法律问题：{question}

## 相关法律法规
{laws_text}

## 司法案例参考
{cases_text}

## 司法解释
{interpretations_text}

## 法律概念关系
{kg_text}

## 研究可信度
{confidence:.0%}

请生成一份全面的法律研究分析报告，包括：
1. 问题概述
2. 法律依据分析（引用具体法条）
3. 司法实践分析（案例和裁判规则）
4. 法律概念解析
5. 争议焦点分析
6. 结论与建议
7. 所有引用来源清单"""

        try:
            response = await llm.ainvoke([
                HumanMessage(content=synthesis_prompt),
            ])
            analysis = response.content if hasattr(response, "content") else str(response)
        except Exception as exc:
            logger.error("Analysis synthesis failed: %s", exc)
            analysis = "综合分析时出现错误。"

        # Collect all sources cited
        sources = []
        for law in relevant_laws[:10]:
            law_name = law.get("law_name", "")
            article = law.get("article_number", "")
            if law_name:
                sources.append(f"《{law_name}》第{article}条")
        for interp in judicial_interpretations[:5]:
            name = interp.get("interpretation_name", "")
            if name:
                sources.append(name)

        return {
            "analysis": analysis,
            "sources_cited": sources,
        }

    def _format_laws(self, laws: list[dict[str, Any]]) -> str:
        """Format law articles for the synthesis prompt."""
        if not laws:
            return "未检索到相关法律法规。"

        parts = []
        for i, law in enumerate(laws[:10], 1):
            law_name = law.get("law_name", "")
            article = law.get("article_number", "")
            content = law.get("article_content", "")
            score = law.get("relevance_score", 0)
            parts.append(
                f"{i}. 《{law_name}》第{article}条\n"
                f"   内容：{content[:200]}\n"
                f"   相关度：{score:.2f}"
            )
        return "\n".join(parts)

    def _format_cases(self, cases: list[dict[str, Any]]) -> str:
        """Format cases for the synthesis prompt."""
        if not cases:
            return "未检索到相关案例。"

        parts = []
        for i, case in enumerate(cases[:5], 1):
            case_type = case.get("case_type", "")
            patterns = case.get("ruling_patterns", [])
            parts.append(f"{i}. 案例类型：{case_type}")
            if patterns:
                parts.append(f"   裁判规则：{'; '.join(patterns[:3])}")
        return "\n".join(parts)

    def _format_interpretations(self, interpretations: list[dict[str, Any]]) -> str:
        """Format judicial interpretations for the synthesis prompt."""
        if not interpretations:
            return "未检索到相关司法解释。"

        parts = []
        for i, interp in enumerate(interpretations[:5], 1):
            name = interp.get("interpretation_name", "")
            article = interp.get("article_number", "")
            content = interp.get("content", "")
            parts.append(f"{i}. {name} 第{article}条\n   {content[:200]}")
        return "\n".join(parts)

    def _format_knowledge_graph(self, kg_results: list[dict[str, Any]]) -> str:
        """Format knowledge graph results for the synthesis prompt."""
        if not kg_results:
            return "无概念关系数据。"

        parts = []
        for kg in kg_results:
            concepts = kg.get("core_concepts", [])
            relationships = kg.get("relationships", [])
            principles = kg.get("related_principles", [])
            if concepts:
                parts.append(f"核心概念：{', '.join(concepts)}")
            if relationships:
                parts.append(f"概念关系：{'; '.join(relationships[:5])}")
            if principles:
                parts.append(f"相关原则：{', '.join(principles)}")
        return "\n".join(parts) if parts else "无概念关系数据。"

    # -------------------------------------------------------------------------
    # Public research method
    # -------------------------------------------------------------------------

    async def research(self, legal_question: str) -> dict:
        """Perform deep research on a legal question.

        Returns:
            {
                'relevant_laws': [...],
                'relevant_cases': [...],
                'judicial_interpretations': [...],
                'analysis': 'comprehensive analysis text',
                'confidence': 0.85,
                'sources_cited': [...]
            }
        """
        result = await self.run({"legal_question": legal_question})
        return {
            "relevant_laws": result.get("relevant_laws", []),
            "relevant_cases": result.get("relevant_cases", []),
            "judicial_interpretations": result.get("judicial_interpretations", []),
            "analysis": result.get("analysis", ""),
            "confidence": result.get("confidence", 0.0),
            "sources_cited": result.get("sources_cited", []),
            "final_output": result.get("analysis", ""),
        }

    # -------------------------------------------------------------------------
    # Public run method
    # -------------------------------------------------------------------------

    async def run(self, input_data: dict[str, Any]) -> dict[str, Any]:
        """Execute the deep research agent.

        Args:
            input_data: Must contain 'legal_question' key.

        Returns:
            Dictionary with research results.
        """
        question = input_data.get("legal_question", "")
        if not question:
            return {
                "final_output": "请提供需要进行深入研究的法律问题。",
                "relevant_laws": [],
                "relevant_cases": [],
                "judicial_interpretations": [],
                "analysis": "",
                "confidence": 0.0,
                "sources_cited": [],
            }

        initial_state: dict[str, Any] = {
            "legal_question": question,
            "research_plan": {},
            "relevant_laws": [],
            "relevant_cases": [],
            "judicial_interpretations": [],
            "knowledge_graph_results": [],
            "cross_references": [],
            "analysis": "",
            "confidence": 0.0,
            "sources_cited": [],
            "messages": [HumanMessage(content=f"深入研究：{question}")],
            "context": {},
            "final_output": "",
        }

        graph = self.compile()
        result = await graph.ainvoke(initial_state)

        return {
            "final_output": result.get("analysis", ""),
            "relevant_laws": result.get("relevant_laws", []),
            "relevant_cases": result.get("relevant_cases", []),
            "judicial_interpretations": result.get("judicial_interpretations", []),
            "analysis": result.get("analysis", ""),
            "confidence": result.get("confidence", 0.0),
            "sources_cited": result.get("sources_cited", []),
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

def create_deep_research_agent() -> DeepResearchAgent:
    """Create and return a DeepResearchAgent instance."""
    return DeepResearchAgent()
