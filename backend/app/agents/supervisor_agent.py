"""
Supervisor Agent - Orchestrator that routes user queries to specialized agents.

Uses LangGraph's StateGraph with conditional edges for intelligent routing.
Flow: analyze_query -> route_to_agent -> execute_agent -> aggregate_results
"""
from typing import Any, Literal, Optional, TypedDict
import json
import asyncio

from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage
from langgraph.graph import StateGraph, END
from pydantic import BaseModel, Field

from app.agents.base_agent import BaseAgent, AgentState
from app.agents.legal_consult_agent import LegalConsultAgent, create_legal_consult_agent
from app.agents.contract_review_agent import ContractReviewAgent, create_contract_review_agent
from app.agents.document_gen_agent import DocumentGenAgent, create_document_gen_agent
from app.agents.law_retrieval_agent import LawRetrievalAgent, create_law_retrieval_agent
from app.agents.collaboration import MultiAgentCollaborator, create_multi_agent_collaborator
from app.prompts.legal_prompts import (
    INTENT_CLASSIFICATION_PROMPT,
    LEGAL_DISCLAIMER,
)
from app.services.llm_service import ChatLLMService, get_llm_service


# =============================================================================
# Agent Selection Types
# =============================================================================

# Agent type identifiers
AgentType = Literal[
    "legal_consult",
    "contract_review",
    "document_gen",
    "law_retrieval",
    "complex_analysis",
    "general",
]

# Intent-to-agent mapping
INTENT_AGENT_MAP: dict[str, AgentType] = {
    "legal_consultation": "legal_consult",
    "contract_review": "contract_review",
    "document_generation": "document_gen",
    "law_retrieval": "law_retrieval",
    "complex_analysis": "complex_analysis",
    "general": "general",
}


# =============================================================================
# State Definition
# =============================================================================

class SupervisorState(AgentState):
    """State for the Supervisor Agent.

    Attributes:
        user_query: The original user query.
        intent: Classified intent of the query.
        confidence: Classification confidence score.
        selected_agent: The agent type selected for handling the query.
        agent_result: Result from the specialized agent.
        aggregated_output: Final aggregated output for the user.
        history: Conversation history for multi-turn interactions.
    """

    user_query: str = Field(default="")
    intent: str = Field(default="")
    confidence: float = Field(default=0.0)
    selected_agent: str = Field(default="")
    agent_result: dict[str, Any] = Field(default_factory=dict)
    aggregated_output: str = Field(default="")
    history: list[dict[str, str]] = Field(default_factory=list)


# =============================================================================
# Supervisor Agent
# =============================================================================

class SupervisorAgent(BaseAgent[SupervisorState]):
    """Orchestrator agent that routes user queries to the appropriate specialized agent.

    Routing logic:
        - legal_question -> LegalConsultAgent
        - contract_review -> ContractReviewAgent
        - generate_document -> DocumentGenAgent
        - search_law -> LawRetrievalAgent
        - general -> general response

    Workflow:
        1. analyze_query - Classify the user's intent
        2. route_to_agent - Select the appropriate specialized agent
        3. execute_agent - Execute the selected agent
        4. aggregate_results - Format and return the final output
    """

    # Agent descriptions for routing
    AGENT_DESCRIPTIONS: dict[str, str] = {
        "legal_consult": "法律咨询：解答法律问题、提供法律建议、分析法律风险",
        "contract_review": "合同审查：审查合同条款、识别法律风险、提供修改建议",
        "document_gen": "文书生成：生成起诉状、答辩状、律师函、法律意见书等法律文书",
        "law_retrieval": "法律检索：检索法律法规条文、司法解释、相关案例",
        "complex_analysis": "综合分析：涉及多个法律领域的复杂问题，需要多代理协作",
        "general": "一般问题：处理非法律领域的一般性问题",
    }

    def __init__(self, name: str = "supervisor_agent") -> None:
        super().__init__(name=name)
        self._llm_service: Optional[ChatLLMService] = None

        # Lazy-initialized specialized agents
        self._legal_consult_agent: Optional[LegalConsultAgent] = None
        self._contract_review_agent: Optional[ContractReviewAgent] = None
        self._document_gen_agent: Optional[DocumentGenAgent] = None
        self._law_retrieval_agent: Optional[LawRetrievalAgent] = None
        self._collaborator: Optional[MultiAgentCollaborator] = None

    @property
    def llm_service(self) -> ChatLLMService:
        if self._llm_service is None:
            self._llm_service = get_llm_service()
        return self._llm_service

    @property
    def legal_consult_agent(self) -> LegalConsultAgent:
        if self._legal_consult_agent is None:
            self._legal_consult_agent = create_legal_consult_agent()
        return self._legal_consult_agent

    @property
    def contract_review_agent(self) -> ContractReviewAgent:
        if self._contract_review_agent is None:
            self._contract_review_agent = create_contract_review_agent()
        return self._contract_review_agent

    @property
    def document_gen_agent(self) -> DocumentGenAgent:
        if self._document_gen_agent is None:
            self._document_gen_agent = create_document_gen_agent()
        return self._document_gen_agent

    @property
    def law_retrieval_agent(self) -> LawRetrievalAgent:
        if self._law_retrieval_agent is None:
            self._law_retrieval_agent = create_law_retrieval_agent()
        return self._law_retrieval_agent

    @property
    def collaborator(self) -> MultiAgentCollaborator:
        if self._collaborator is None:
            self._collaborator = create_multi_agent_collaborator()
        return self._collaborator

    def _build_graph(self) -> StateGraph:
        """Build the LangGraph StateGraph for the supervisor.

        Nodes:
            - analyze_query: Classify the user's intent
            - route_to_agent: Select the specialized agent
            - execute_legal_consult: Execute legal consultation agent
            - execute_contract_review: Execute contract review agent
            - execute_document_gen: Execute document generation agent
            - execute_law_retrieval: Execute law retrieval agent
            - execute_general: Handle general/non-legal queries
            - aggregate_results: Format and return final output
        """
        builder = StateGraph(SupervisorState)

        # Add nodes
        builder.add_node("analyze_query", self._analyze_query_node)
        builder.add_node("route_to_agent", self._route_to_agent_node)
        builder.add_node("execute_legal_consult", self._execute_legal_consult_node)
        builder.add_node("execute_contract_review", self._execute_contract_review_node)
        builder.add_node("execute_document_gen", self._execute_document_gen_node)
        builder.add_node("execute_law_retrieval", self._execute_law_retrieval_node)
        builder.add_node("execute_complex_analysis", self._execute_complex_analysis_node)
        builder.add_node("execute_general", self._execute_general_node)
        builder.add_node("aggregate_results", self._aggregate_results_node)

        # Entry point
        builder.set_entry_point("analyze_query")

        # analyze_query -> route_to_agent (always)
        builder.add_edge("analyze_query", "route_to_agent")

        # Conditional edges: route_to_agent -> specialized agent based on selection
        builder.add_conditional_edges(
            "route_to_agent",
            self._select_agent_route,
            {
                "legal_consult": "execute_legal_consult",
                "contract_review": "execute_contract_review",
                "document_gen": "execute_document_gen",
                "law_retrieval": "execute_law_retrieval",
                "complex_analysis": "execute_complex_analysis",
                "general": "execute_general",
            },
        )

        # All specialized agents -> aggregate_results
        builder.add_edge("execute_legal_consult", "aggregate_results")
        builder.add_edge("execute_contract_review", "aggregate_results")
        builder.add_edge("execute_document_gen", "aggregate_results")
        builder.add_edge("execute_law_retrieval", "aggregate_results")
        builder.add_edge("execute_complex_analysis", "aggregate_results")
        builder.add_edge("execute_general", "aggregate_results")

        # aggregate_results -> END
        builder.add_edge("aggregate_results", END)

        return builder

    # -------------------------------------------------------------------------
    # Conditional routing function
    # -------------------------------------------------------------------------

    def _select_agent_route(self, state: SupervisorState) -> str:
        """Determine which specialized agent to route to based on selected_agent."""
        selected = state.selected_agent
        if selected in INTENT_AGENT_MAP.values():
            return selected
        return "general"

    # -------------------------------------------------------------------------
    # Node: analyze_query
    # -------------------------------------------------------------------------

    async def _analyze_query_node(self, state: SupervisorState) -> dict[str, Any]:
        """Classify the user's query intent."""
        user_query = state.user_query
        if not user_query:
            return {"intent": "general", "confidence": 1.0}

        llm = self.llm_service.get_llm(temperature=0.0)
        prompt = INTENT_CLASSIFICATION_PROMPT.format(query=user_query)

        try:
            response = await llm.ainvoke([
                HumanMessage(content=prompt),
            ])
            content = response.content if hasattr(response, "content") else str(response)

            # Extract JSON from response
            import re
            json_match = re.search(r'\{[\s\S]*\}', content)
            if json_match:
                parsed = json.loads(json_match.group())
                intent = parsed.get("intent", "general")
                confidence = parsed.get("confidence", 0.5)
                keywords = parsed.get("keywords", [])
                reasoning = parsed.get("brief_reasoning", "")
            else:
                intent = "general"
                confidence = 0.5
                keywords = []
                reasoning = ""
        except Exception:
            intent = "general"
            confidence = 0.5
            keywords = []
            reasoning = ""

        return {
            "intent": intent,
            "confidence": confidence,
            "context": {
                "keywords": keywords,
                "reasoning": reasoning,
            },
        }

    # -------------------------------------------------------------------------
    # Node: route_to_agent
    # -------------------------------------------------------------------------

    async def _route_to_agent_node(self, state: SupervisorState) -> dict[str, Any]:
        """Select the appropriate specialized agent based on intent."""
        intent = state.intent
        agent_type = INTENT_AGENT_MAP.get(intent, "general")

        return {
            "selected_agent": agent_type,
        }

    # -------------------------------------------------------------------------
    # Node: execute_legal_consult
    # -------------------------------------------------------------------------

    async def _execute_legal_consult_node(self, state: SupervisorState) -> dict[str, Any]:
        """Execute the legal consultation agent."""
        result = await self.legal_consult_agent.run({
            "query": state.user_query,
        })
        return {"agent_result": result}

    # -------------------------------------------------------------------------
    # Node: execute_contract_review
    # -------------------------------------------------------------------------

    async def _execute_contract_review_node(self, state: SupervisorState) -> dict[str, Any]:
        """Execute the contract review agent."""
        result = await self.contract_review_agent.run({
            "contract_text": state.user_query,
            "review_focus": "",
        })
        return {"agent_result": result}

    # -------------------------------------------------------------------------
    # Node: execute_document_gen
    # -------------------------------------------------------------------------

    async def _execute_document_gen_node(self, state: SupervisorState) -> dict[str, Any]:
        """Execute the document generation agent."""
        result = await self.document_gen_agent.run({
            "description": state.user_query,
            "document_type": "",
        })
        return {"agent_result": result}

    # -------------------------------------------------------------------------
    # Node: execute_law_retrieval
    # -------------------------------------------------------------------------

    async def _execute_law_retrieval_node(self, state: SupervisorState) -> dict[str, Any]:
        """Execute the law retrieval agent."""
        result = await self.law_retrieval_agent.run({
            "query": state.user_query,
            "category_filter": "",
        })
        return {"agent_result": result}

    # -------------------------------------------------------------------------
    # Node: execute_complex_analysis
    # -------------------------------------------------------------------------

    async def _execute_complex_analysis_node(self, state: SupervisorState) -> dict[str, Any]:
        """Execute multi-agent collaboration for complex queries.

        This node is triggered when a query involves multiple legal domains
        or requires coordination across several specialized agents.
        """
        result = await self.collaborator.run(
            query=state.user_query,
            context=state.context,
        )
        # collaborator returns final_response; aggregation node reads final_output
        result["final_output"] = result.get("final_response", "")
        return {"agent_result": result}

    # -------------------------------------------------------------------------
    # Node: execute_general
    # -------------------------------------------------------------------------

    async def _execute_general_node(self, state: SupervisorState) -> dict[str, Any]:
        """Handle general/non-legal queries."""
        llm = self.llm_service.get_llm(temperature=0.3, max_tokens=1024)

        general_prompt = f"""用户提出了以下问题，这可能不是一个法律问题：

{state.user_query}

请友好地回复用户，说明你是一个法律智能助手，主要擅长处理法律相关问题。
如果用户的问题与法律有关联，请尝试从法律角度提供帮助。
如果完全无关，请礼貌地说明你的能力范围，并引导用户提出法律相关问题。"""

        try:
            response = await llm.ainvoke([
                HumanMessage(content=general_prompt),
            ])
            reply = response.content if hasattr(response, "content") else str(response)
        except Exception:
            reply = "您好！我是法律智能助手，主要擅长处理法律咨询、合同审查、文书生成和法律检索等法律相关事务。请问有什么法律方面的问题需要我帮助吗？"

        result = {
            "final_output": reply,
            "agent_type": "general",
        }
        return {"agent_result": result}

    # -------------------------------------------------------------------------
    # Node: aggregate_results
    # -------------------------------------------------------------------------

    async def _aggregate_results_node(self, state: SupervisorState) -> dict[str, Any]:
        """Aggregate and format the final output."""
        agent_result = state.agent_result
        selected_agent = state.selected_agent

        # Get the final output from the agent result
        final_output = agent_result.get("final_output", "")

        if not final_output:
            final_output = "抱歉，处理您的请求时出现了问题。请稍后重试或重新描述您的问题。"

        # Update conversation history
        history = state.history + [
            {"role": "user", "content": state.user_query},
            {"role": "assistant", "content": final_output[:500]},
        ]

        return {
            "aggregated_output": final_output,
            "final_output": final_output,
            "history": history,
        }

    # -------------------------------------------------------------------------
    # Public run method
    # -------------------------------------------------------------------------

    async def run(self, input_data: dict[str, Any]) -> dict[str, Any]:
        """Execute the supervisor agent to route and process the user query.

        Args:
            input_data: Must contain 'user_query' key. Optional 'history'.

        Returns:
            Dictionary with aggregated_output, intent, selected_agent, agent_result.
        """
        user_query = input_data.get("user_query", "")
        if not user_query:
            return {
                "aggregated_output": "请提出您需要帮助的法律问题。",
                "intent": "",
                "selected_agent": "",
                "agent_result": {},
            }

        initial_state: dict[str, Any] = {
            "user_query": user_query,
            "intent": "",
            "confidence": 0.0,
            "selected_agent": "",
            "agent_result": {},
            "aggregated_output": "",
            "history": input_data.get("history", []),
            "messages": [HumanMessage(content=user_query)],
            "context": {},
            "final_output": "",
        }

        graph = self.compile()
        result = await graph.ainvoke(initial_state)

        return {
            "aggregated_output": result.get("aggregated_output", ""),
            "final_output": result.get("aggregated_output", ""),
            "intent": result.get("intent", ""),
            "selected_agent": result.get("selected_agent", ""),
            "agent_result": result.get("agent_result", {}),
            "history": result.get("history", []),
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

def create_supervisor_agent() -> SupervisorAgent:
    """Create and return a SupervisorAgent instance."""
    return SupervisorAgent()