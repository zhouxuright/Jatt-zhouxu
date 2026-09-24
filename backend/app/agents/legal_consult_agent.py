"""
Legal Consultation Agent - Provides legal advice and analysis using LangGraph.

Flow: classify_intent -> retrieve_knowledge -> generate_response -> format_output
"""
from typing import Any, Optional, TypedDict
import logging

from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage
from langgraph.graph import StateGraph, END
from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel, Field

from app.agents.base_agent import BaseAgent, AgentState
from app.prompts.legal_prompts import (
    LEGAL_CONSULT_SYSTEM_PROMPT,
    INTENT_CLASSIFICATION_PROMPT,
    LEGAL_CONSULT_TEMPLATE,
    LEGAL_DISCLAIMER,
    get_prompt,
)
from app.services.llm_service import ChatLLMService, get_llm_service

logger = logging.getLogger(__name__)


# =============================================================================
# State Definition
# =============================================================================

class LegalConsultState(AgentState):
    """State for the Legal Consultation Agent.

    Attributes:
        query: The user's original legal question.
        intent: Classified intent of the query.
        retrieved_docs: Documents retrieved from the knowledge base.
        response: Generated legal analysis response.
        citations: Legal citations referenced in the response.
        analysis_instructions: Custom instructions for analysis based on intent.
    """

    query: str = Field(default="")
    normalized_query: str = Field(default="")
    is_colloquial: bool = Field(default=False)
    intent: str = Field(default="")
    retrieved_docs: list[dict[str, Any]] = Field(default_factory=list)
    response: str = Field(default="")
    citations: list[str] = Field(default_factory=list)
    analysis_instructions: str = Field(default="")


# =============================================================================
# Legal Consultation Agent
# =============================================================================

class LegalConsultAgent(BaseAgent[LegalConsultState]):
    """Agent for legal consultation and advice.

    Workflow:
        1. classify_intent - Classify the user's legal query intent
        2. retrieve_knowledge - Retrieve relevant laws, cases, and concepts
        3. generate_response - Generate a comprehensive legal analysis
        4. format_output - Format the final output with citations and disclaimer
    """

    # Intent descriptions for analysis instructions
    INTENT_ANALYSIS_MAP: dict[str, str] = {
        "legal_consultation": "请从法律依据、法律关系、权利义务、实务建议四个维度进行全面分析。",
        "case_analysis": "请从案件事实、法律适用、争议焦点、裁判规则四个维度进行分析。",
        "law_interpretation": "请从立法背景、条文含义、适用范围、司法实践四个维度进行解读。",
        "rights_protection": "请从权利基础、侵权/违约认定、救济途径、维权策略四个维度进行分析。",
        "risk_assessment": "请从风险识别、法律后果、防范措施、应急预案四个维度进行评估。",
    }

    def __init__(self, name: str = "legal_consult_agent") -> None:
        super().__init__(name=name)
        self._llm_service: Optional[ChatLLMService] = None

    @property
    def llm_service(self) -> ChatLLMService:
        if self._llm_service is None:
            self._llm_service = get_llm_service()
        return self._llm_service

    def _build_graph(self) -> StateGraph:
        """Build the LangGraph StateGraph for legal consultation.

        Nodes:
            - classify_intent: Classify the user's query intent
            - retrieve_knowledge: Retrieve relevant legal knowledge
            - generate_response: Generate legal analysis response
            - format_output: Format the final output with citations and disclaimer
        """
        builder = StateGraph(LegalConsultState)

        # Add nodes
        builder.add_node("normalize_query", self._normalize_query_node)
        builder.add_node("classify_intent", self._classify_intent_node)
        builder.add_node("retrieve_knowledge", self._retrieve_knowledge_node)
        builder.add_node("generate_response", self._generate_response_node)
        builder.add_node("format_output", self._format_output_node)

        # Define edges
        builder.set_entry_point("normalize_query")
        builder.add_edge("normalize_query", "classify_intent")
        builder.add_edge("classify_intent", "retrieve_knowledge")
        builder.add_edge("retrieve_knowledge", "generate_response")
        builder.add_edge("generate_response", "format_output")
        builder.add_edge("format_output", END)

        return builder

    # -------------------------------------------------------------------------
    # Node: normalize_query
    # -------------------------------------------------------------------------

    async def _normalize_query_node(self, state: LegalConsultState) -> dict[str, Any]:
        """Convert colloquial/dialect input into professional legal terminology.

        Routes the raw query through the ColloquialRewriteAgent so downstream
        intent classification and RAG retrieval operate on a canonical legal
        formulation. Degrades gracefully: on any failure the original query is
        used unchanged.
        """
        query = (state.query or "").strip()
        if not query:
            return {"normalized_query": "", "is_colloquial": False}

        from app.agents.colloquial_rewrite_agent import get_colloquial_rewrite_agent

        try:
            agent = get_colloquial_rewrite_agent()
            result = await agent.run_async({"text": query})
            normalized = str(result.get("normalized_text") or "").strip()
            is_colloquial = bool(result.get("is_colloquial", False))
        except Exception as exc:
            logger.warning("Colloquial rewrite failed, using original query: %s", exc)
            normalized, is_colloquial = "", False

        if normalized and normalized != query:
            logger.info("Query normalized: %r -> %r", query[:60], normalized[:60])
            return {"normalized_query": normalized, "is_colloquial": is_colloquial}
        return {"normalized_query": "", "is_colloquial": is_colloquial}

    # -------------------------------------------------------------------------
    # Node: classify_intent
    # -------------------------------------------------------------------------

    async def _classify_intent_node(self, state: LegalConsultState) -> dict[str, Any]:
        """Classify the user's legal query intent."""
        query = state.normalized_query or state.query
        if not query:
            return {"intent": "unknown", "analysis_instructions": ""}

        llm = self.llm_service.get_llm(temperature=0.0)
        prompt = INTENT_CLASSIFICATION_PROMPT.format(query=query)

        try:
            response = await llm.ainvoke([
                SystemMessage(content=LEGAL_CONSULT_SYSTEM_PROMPT),
                HumanMessage(content=prompt),
            ])
            content = response.content if hasattr(response, "content") else str(response)

            # Parse intent from response
            import json
            parsed = json.loads(content) if isinstance(content, str) else content
            intent = parsed.get("intent", "legal_consultation")
        except Exception:
            intent = "legal_consultation"

        analysis_instructions = self.INTENT_ANALYSIS_MAP.get(
            intent,
            "请从法律依据、法律关系、实务建议三个维度进行全面分析。",
        )

        return {
            "intent": intent,
            "analysis_instructions": analysis_instructions,
        }

    # -------------------------------------------------------------------------
    # Node: retrieve_knowledge
    # -------------------------------------------------------------------------

    async def _retrieve_knowledge_node(self, state: LegalConsultState) -> dict[str, Any]:
        """Retrieve relevant legal knowledge including laws, cases, and concepts."""
        query = state.normalized_query or state.query
        intent = state.intent

        docs: list[dict[str, Any]] = []

        # Search laws
        law_results = await self._search_law_articles(query)
        docs.extend(law_results)

        # Search cases
        case_results = await self._search_cases(query)
        docs.extend(case_results)

        return {"retrieved_docs": docs}

    async def _search_law_articles(self, query: str) -> list[dict[str, Any]]:
        """Search for relevant law articles using the real RAG pipeline.

        Uses BM25 keyword search + Milvus vector search + RRF fusion +
        cross-encoder reranking to retrieve real legal articles from the
        knowledge base (not LLM-hallucinated results).
        """
        from app.rag.advanced_retriever import get_rag_pipeline

        try:
            pipeline = get_rag_pipeline()
            results = pipeline.retrieve(
                query=query,
                top_k=8,
                use_bm25=True,
                use_vector=True,
                use_reranker=True,
            )
            # Normalize result keys to match the format expected by downstream code
            normalized: list[dict[str, Any]] = []
            for r in results:
                normalized.append({
                    "law_name": r.get("law_name", ""),
                    "article_number": r.get("article_number", ""),
                    "article_content": r.get("content", ""),
                    "content": r.get("content", ""),
                    "relevance_score": r.get("rerank_score", r.get("score", 0.0)),
                    "effective_status": "现行有效",
                    "category": r.get("category", ""),
                    "tags": r.get("tags", ""),
                    "source": r.get("source", "rag"),
                })
            return normalized
        except Exception as exc:
            logger.warning("RAG pipeline failed for law article search: %s", exc)
            return []

    async def _search_cases(self, query: str) -> list[dict[str, Any]]:
        """Search for relevant legal cases using true vector semantic retrieval.

        Queries the Milvus ``legal_cases`` collection with the same BGE-M3
        embedding model used for law articles, returning court cases ranked by
        cosine similarity. Results are normalized into the ``case_name`` /
        ``key_ruling`` shape expected by the downstream formatter.
        """
        from app.rag.milvus_service import get_milvus_rag_service

        try:
            service = get_milvus_rag_service()
            results = service.search_cases(query, top_k=5)
        except Exception as exc:
            logger.warning("Milvus case search failed: %s", exc)
            return []

        normalized: list[dict[str, Any]] = []
        for r in results:
            summary = r.get("summary") or ""
            normalized.append({
                "case_name": r.get("title", ""),
                "case_number": r.get("case_number", ""),
                "court_name": r.get("court_name", ""),
                "case_type": r.get("case_type", ""),
                "cause_of_action": r.get("cause_of_action", ""),
                "decision_date": r.get("decision_date", ""),
                "key_ruling": summary[:400],
                "summary": summary,
                "relevance_score": round(float(r.get("score", 0.0)), 4),
                "source": r.get("source", "milvus_semantic"),
            })
        logger.info("Case semantic search returned %d results for query '%s'",
                    len(normalized), query[:50])
        return normalized

    async def _explain_concept(self, concept: str) -> str:
        """Explain a legal concept."""
        llm = self.llm_service.get_llm(temperature=0.0)
        explain_prompt = f"请用通俗易懂的语言解释以下法律概念：{concept}"

        try:
            response = await llm.ainvoke([
                SystemMessage(content=LEGAL_CONSULT_SYSTEM_PROMPT),
                HumanMessage(content=explain_prompt),
            ])
            return response.content if hasattr(response, "content") else str(response)
        except Exception:
            return f"无法解释概念：{concept}"

    # -------------------------------------------------------------------------
    # Node: generate_response
    # -------------------------------------------------------------------------

    async def _generate_response_node(self, state: LegalConsultState) -> dict[str, Any]:
        """Generate a comprehensive legal analysis response."""
        query = state.query
        intent = state.intent
        retrieved_docs = state.retrieved_docs
        analysis_instructions = state.analysis_instructions

        # Format retrieved knowledge
        retrieved_knowledge = self._format_retrieved_knowledge(retrieved_docs)

        llm = self.llm_service.get_llm(temperature=0.3, max_tokens=4096)
        prompt = LEGAL_CONSULT_TEMPLATE.format(
            query=query,
            intent=intent,
            confidence="0.85",
            retrieved_knowledge=retrieved_knowledge,
            analysis_instructions=analysis_instructions,
        )

        try:
            response = await llm.ainvoke([
                SystemMessage(content=LEGAL_CONSULT_SYSTEM_PROMPT),
                HumanMessage(content=prompt),
            ])
            response_text = response.content if hasattr(response, "content") else str(response)
        except Exception as e:
            response_text = f"抱歉，生成回答时出现错误：{str(e)}"

        # Extract citations
        citations = self._extract_citations(retrieved_docs)

        return {
            "response": response_text,
            "citations": citations,
        }

    def _format_retrieved_knowledge(
        self, docs: list[dict[str, Any]]
    ) -> str:
        """Format retrieved documents into a readable string."""
        if not docs:
            return "（未检索到相关法律知识）"

        parts: list[str] = []
        for i, doc in enumerate(docs, 1):
            if "law_name" in doc:
                parts.append(
                    f"{i}. 《{doc.get('law_name', '')}》"
                    f"第{doc.get('article_number', '')}条："
                    f"{doc.get('article_content', '')}"
                    f" [相关度：{doc.get('relevance_score', 'N/A')}]"
                    f" [效力：{doc.get('effective_status', '未知')}]"
                )
            elif "case_name" in doc:
                parts.append(
                    f"{i}. 案例：{doc.get('case_name', '')}"
                    f"（{doc.get('case_number', '')}）"
                    f" - 裁判要旨：{doc.get('key_ruling', '')}"
                    f" [相关度：{doc.get('relevance_score', 'N/A')}]"
                )

        return "\n".join(parts) if parts else "（未检索到相关法律知识）"

    def _extract_citations(
        self, docs: list[dict[str, Any]]
    ) -> list[str]:
        """Extract formal citations from retrieved documents."""
        citations: list[str] = []
        for doc in docs:
            if "law_name" in doc and "article_number" in doc:
                citations.append(
                    f"《{doc['law_name']}》第{doc['article_number']}条"
                )
            elif "case_name" in doc and "case_number" in doc:
                citations.append(
                    f"{doc['case_name']}（{doc['case_number']}）"
                )
        return citations

    # -------------------------------------------------------------------------
    # Node: format_output
    # -------------------------------------------------------------------------

    async def _format_output_node(self, state: LegalConsultState) -> dict[str, Any]:
        """Format the final output with citations and legal disclaimer."""
        response = state.response
        citations = state.citations

        # Surface colloquial→professional normalization to the user when applied
        normalization_note = ""
        if state.is_colloquial and state.normalized_query:
            normalization_note = (
                f"> 已将您的口语表述转换为专业法律表述：**{state.normalized_query}**\n\n"
            )

        # Build citation section
        citation_section = ""
        if citations:
            citation_section = "\n\n---\n**引用法律依据及案例：**\n"
            for i, citation in enumerate(citations, 1):
                citation_section += f"\n{i}. {citation}"

        # Append legal disclaimer
        final_output = normalization_note + response + citation_section + LEGAL_DISCLAIMER

        return {"final_output": final_output}

    # -------------------------------------------------------------------------
    # Public run method
    # -------------------------------------------------------------------------

    async def run(self, input_data: dict[str, Any]) -> dict[str, Any]:
        """Execute the legal consultation agent.

        Args:
            input_data: Must contain 'query' key with the user's legal question.

        Returns:
            Dictionary with 'final_output', 'response', 'intent', 'citations', and 'retrieved_docs'.
        """
        query = input_data.get("query", "")
        if not query:
            return {
                "final_output": "请提供需要咨询的法律问题。",
                "response": "",
                "intent": "",
                "citations": [],
                "retrieved_docs": [],
            }

        initial_state: dict[str, Any] = {
            "query": query,
            "normalized_query": "",
            "is_colloquial": False,
            "intent": "",
            "retrieved_docs": [],
            "response": "",
            "citations": [],
            "analysis_instructions": "",
            "messages": [HumanMessage(content=query)],
            "context": {},
            "final_output": "",
        }

        graph = self.compile()
        result = await graph.ainvoke(initial_state)

        return {
            "final_output": result.get("final_output", ""),
            "response": result.get("response", ""),
            "intent": result.get("intent", ""),
            "citations": result.get("citations", []),
            "retrieved_docs": result.get("retrieved_docs", []),
            "normalized_query": result.get("normalized_query", ""),
            "is_colloquial": result.get("is_colloquial", False),
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

def create_legal_consult_agent() -> LegalConsultAgent:
    """Create and return a LegalConsultAgent instance."""
    return LegalConsultAgent()