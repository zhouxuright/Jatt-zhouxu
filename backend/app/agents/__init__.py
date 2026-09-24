"""Agent registry -- exports all agent classes for convenient import."""

from app.agents.base_agent import BaseAgent, AgentState
from app.agents.supervisor_agent import SupervisorAgent, create_supervisor_agent
from app.agents.legal_consult_agent import LegalConsultAgent, create_legal_consult_agent
from app.agents.contract_review_agent import ContractReviewAgent, create_contract_review_agent
from app.agents.document_gen_agent import DocumentGenAgent, create_document_gen_agent
from app.agents.law_retrieval_agent import LawRetrievalAgent, create_law_retrieval_agent
from app.agents.colloquial_rewrite_agent import ColloquialRewriteAgent, get_colloquial_rewrite_agent
from app.agents.research_agent import DeepResearchAgent, create_deep_research_agent
from app.agents.review_agent import QualityReviewAgent, create_quality_review_agent
from app.agents.collaboration import MultiAgentCollaborator, create_multi_agent_collaborator
from app.agents.deep_thinking_agent import (
    DeepThinkingAgent,
    DeepThinkingLangGraphAgent,
    create_deep_thinking_agent,
    create_deep_thinking_langgraph_agent,
)

__all__ = [
    "BaseAgent",
    "AgentState",
    "SupervisorAgent",
    "create_supervisor_agent",
    "LegalConsultAgent",
    "create_legal_consult_agent",
    "ContractReviewAgent",
    "create_contract_review_agent",
    "DocumentGenAgent",
    "create_document_gen_agent",
    "LawRetrievalAgent",
    "create_law_retrieval_agent",
    "ColloquialRewriteAgent",
    "get_colloquial_rewrite_agent",
    "DeepResearchAgent",
    "create_deep_research_agent",
    "QualityReviewAgent",
    "create_quality_review_agent",
    "MultiAgentCollaborator",
    "create_multi_agent_collaborator",
    "DeepThinkingAgent",
    "DeepThinkingLangGraphAgent",
    "create_deep_thinking_agent",
    "create_deep_thinking_langgraph_agent",
]
