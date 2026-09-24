"""
Base Agent class for the Legal Intelligent Assistance System.

Provides abstract foundation for all specialized agents using LangGraph StateGraph.
"""
from abc import ABC, abstractmethod
from typing import Any, Generic, Optional, TypeVar

from langgraph.graph import StateGraph, END
from langgraph.graph.state import CompiledStateGraph
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage
from pydantic import BaseModel, Field


# =============================================================================
# Common State Definitions
# =============================================================================

class AgentState(BaseModel):
    """Common state shared across all agents.

    Attributes:
        messages: Conversation message history.
        context: Additional context information for the agent.
        final_output: The final output produced by the agent run.
    """

    messages: list[BaseMessage] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)
    final_output: str = Field(default="")

    model_config = {"arbitrary_types_allowed": True}


StateT = TypeVar("StateT", bound=AgentState)


# =============================================================================
# Base Agent
# =============================================================================

class BaseAgent(ABC, Generic[StateT]):
    """Abstract base class for all legal AI agents.

    Each specialized agent must:
    1. Define its own State subclass extending AgentState.
    2. Implement _build_graph() to define nodes and edges.
    3. Implement run() to execute the agent on input.

    The agent uses LangGraph's StateGraph for workflow orchestration.
    """

    def __init__(self, name: str = "base_agent") -> None:
        """Initialize the base agent.

        Args:
            name: Human-readable name for this agent instance.
        """
        self.name: str = name
        self._graph: Optional[CompiledStateGraph] = None

    @abstractmethod
    def _build_graph(self) -> StateGraph:
        """Build the LangGraph StateGraph for this agent.

        Subclasses must implement this method to define:
        - State schema (using TypedDict or Pydantic model)
        - Processing nodes
        - Edges and conditional routing
        - Entry and exit points

        Returns:
            A configured StateGraph instance (not yet compiled).
        """
        ...

    def compile(self) -> CompiledStateGraph:
        """Compile the agent's StateGraph for execution.

        Returns:
            A compiled LangGraph state graph ready for invocation.
        """
        if self._graph is None:
            builder = self._build_graph()
            self._graph = builder.compile()
        return self._graph

    @abstractmethod
    def run(self, input_data: dict[str, Any]) -> dict[str, Any]:
        """Execute the agent on the given input.

        Args:
            input_data: Dictionary containing the agent's input parameters.

        Returns:
            Dictionary containing the agent's output results.
        """
        ...

    def _add_messages_to_state(
        self,
        state: dict[str, Any],
        messages: list[BaseMessage],
    ) -> dict[str, Any]:
        """Add messages to the agent state.

        Args:
            state: Current agent state dictionary.
            messages: Messages to add.

        Returns:
            Updated state dictionary.
        """
        existing = state.get("messages", [])
        state["messages"] = existing + messages
        return state

    def _add_context(self, state: dict[str, Any], key: str, value: Any) -> dict[str, Any]:
        """Add a context entry to the agent state.

        Args:
            state: Current agent state dictionary.
            key: Context key.
            value: Context value.

        Returns:
            Updated state dictionary.
        """
        context = state.get("context", {})
        context[key] = value
        state["context"] = context
        return state

    def _get_context(self, state: dict[str, Any], key: str, default: Any = None) -> Any:
        """Get a context entry from the agent state.

        Args:
            state: Current agent state dictionary.
            key: Context key.
            default: Default value if key not found.

        Returns:
            The context value or default.
        """
        return state.get("context", {}).get(key, default)

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}(name='{self.name}')>"