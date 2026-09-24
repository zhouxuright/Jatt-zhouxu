"""
Multi-Agent Collaboration Framework -- Orchestrates complex legal tasks
requiring multiple specialized agents working together.

Collaboration patterns (all fully implemented):
1. Parallel Fan-out: required agents run concurrently via asyncio.gather
2. Sequential Pipeline: agents run in order, each receiving prior results
3. Iterative Refinement: draft -> review -> revise loop until approved
4. Hierarchical Delegation: decompose into subtasks, fan out, aggregate

All specialized agents (legal retrieval, consultation, contract review,
contract drafting, compliance risk, litigation support, document
generation) are registered in AGENT_REGISTRY and invoked through a
uniform adapter signature so the orchestrator stays agent-agnostic.
"""
from typing import Any, Awaitable, Callable, Optional, TypedDict
import logging
import asyncio
import json
import re
import time

from langchain_core.messages import HumanMessage
from langgraph.graph import StateGraph, END
from langgraph.graph.state import CompiledStateGraph

from app.agents.legal_consult_agent import create_legal_consult_agent
from app.agents.contract_review_agent import create_contract_review_agent
from app.agents.document_gen_agent import create_document_gen_agent
from app.agents.law_retrieval_agent import create_law_retrieval_agent
from app.services.llm_service import ChatLLMService, get_llm_service

logger = logging.getLogger(__name__)


# =============================================================================
# Collaboration State
# =============================================================================

class CollaborationState(TypedDict, total=False):
    """State for multi-agent collaboration."""

    query: str
    intent: str
    context: dict
    messages: list[dict]
    agent_results: dict[str, Any]
    agent_details: list[dict[str, Any]]  # per-agent observability records
    subtasks: list[dict[str, Any]]  # hierarchical pattern: {agent, task}
    draft_output: str  # iterative pattern: current draft text
    review_feedback: dict[str, Any]  # iterative pattern: last review result
    final_response: str
    iteration: int
    max_iterations: int


# =============================================================================
# Agent Registry
# =============================================================================

# Uniform adapter contract: (query, context) -> result dict with a text field
AgentAdapter = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


class AgentSpec:
    """Registry entry binding an agent id to an adapter and metadata."""

    def __init__(self, agent_id: str, description: str, adapter: AgentAdapter) -> None:
        self.agent_id = agent_id
        self.description = description
        self.adapter = adapter


def _text_of(result: dict[str, Any]) -> str:
    """Extract the primary text from any agent's result dict."""
    for key in ("final_output", "report", "analysis", "content", "final_response"):
        value = result.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return json.dumps(result, ensure_ascii=False)[:2000] if result else ""


AGENT_REGISTRY: dict[str, AgentSpec] = {}


def register_agent(agent_id: str, description: str, adapter: AgentAdapter) -> None:
    """Register (or replace) an agent in the collaboration registry."""
    AGENT_REGISTRY[agent_id] = AgentSpec(agent_id, description, adapter)


# --- Default registry population ---------------------------------------------
# Instances are created lazily inside the adapters: the LLM service and model
# warmup should not happen at import time.

def _make_law_retrieval_adapter() -> AgentAdapter:
    async def adapter(query: str, context: dict[str, Any]) -> dict[str, Any]:
        return await create_law_retrieval_agent().run({
            "query": query,
            "category_filter": context.get("category_filter", ""),
        })
    return adapter


def _make_legal_consult_adapter() -> AgentAdapter:
    async def adapter(query: str, context: dict[str, Any]) -> dict[str, Any]:
        prior = context.get("prior_findings", "")
        return await create_legal_consult_agent().run({
            "query": f"{query}\n\n（参考已有检索结论：{prior}）" if prior else query,
        })
    return adapter


def _make_contract_review_adapter() -> AgentAdapter:
    async def adapter(query: str, context: dict[str, Any]) -> dict[str, Any]:
        return await create_contract_review_agent().run({
            "contract_text": query,
            "review_focus": context.get("review_focus", ""),
        })
    return adapter


def _make_contract_draft_adapter() -> AgentAdapter:
    async def adapter(query: str, context: dict[str, Any]) -> dict[str, Any]:
        from app.agents.contract_lifecycle_agent import ContractLifecycleAgent
        result = await ContractLifecycleAgent().draft_contract(
            description=query,
            contract_type=context.get("contract_type", ""),
        )
        result.setdefault("final_output", result.get("content", ""))
        return result
    return adapter


def _make_compliance_risk_adapter() -> AgentAdapter:
    async def adapter(query: str, context: dict[str, Any]) -> dict[str, Any]:
        from app.agents.compliance_risk_agent import ComplianceRiskAgent
        result = await ComplianceRiskAgent().risk_assessment(
            business_description=query,
            industry=context.get("industry", ""),
            compliance_domains=context.get("compliance_domains"),
        )
        result.setdefault("final_output", result.get("report", result.get("analysis", "")))
        return result
    return adapter


def _make_litigation_adapter() -> AgentAdapter:
    async def adapter(query: str, context: dict[str, Any]) -> dict[str, Any]:
        from app.agents.litigation_support_agent import LitigationSupportAgent
        result = await LitigationSupportAgent().analyze_case(
            case_description=query,
            evidence_list=context.get("evidence_list", ""),
            claims=context.get("claims", ""),
        )
        result.setdefault("final_output", result.get("analysis", result.get("report", "")))
        return result
    return adapter


def _make_document_gen_adapter() -> AgentAdapter:
    async def adapter(query: str, context: dict[str, Any]) -> dict[str, Any]:
        return await create_document_gen_agent().run({
            "description": query,
            "document_type": context.get("document_type", ""),
        })
    return adapter


def _populate_default_registry() -> None:
    register_agent(
        "law_retrieval",
        "法律检索：检索法律法规条文、司法解释与相关案例",
        _make_law_retrieval_adapter(),
    )
    register_agent(
        "legal_consult",
        "法律咨询：基于检索结论解答法律问题、提供分析意见",
        _make_legal_consult_adapter(),
    )
    register_agent(
        "contract_review",
        "合同审查：逐条识别合同风险、评定等级、给出修改建议",
        _make_contract_review_adapter(),
    )
    register_agent(
        "contract_draft",
        "合同起草：依据业务描述起草合同文本（模板优先，LLM 兜底）",
        _make_contract_draft_adapter(),
    )
    register_agent(
        "compliance_risk",
        "合规风险：评估业务合规风险、映射法规、给出整改建议",
        _make_compliance_risk_adapter(),
    )
    register_agent(
        "litigation_support",
        "诉讼支持：案由分析、诉讼策略、证据要求与风险评估",
        _make_litigation_adapter(),
    )
    register_agent(
        "document_gen",
        "文书生成：生成起诉状、答辩状、律师函、法律意见书等",
        _make_document_gen_adapter(),
    )


_populate_default_registry()


def available_agents_description() -> str:
    """Render the registry as prompt text for the classifier."""
    return "\n".join(
        f"- {spec.agent_id}: {spec.description}"
        for spec in AGENT_REGISTRY.values()
    )


# =============================================================================
# Collaboration Patterns
# =============================================================================

COLLABORATION_PATTERNS = {
    "parallel": {
        "name": "Parallel Fan-out",
        "description": "Multiple agents work concurrently via asyncio.gather, results are merged",
        "use_case": "Multi-domain legal research requiring diverse perspectives",
    },
    "sequential": {
        "name": "Sequential Pipeline",
        "description": "Agents execute in sequence, each building on previous results",
        "use_case": "Retrieval-then-analysis chains where later agents need earlier findings",
    },
    "iterative": {
        "name": "Iterative Refinement",
        "description": "Draft, quality review, and revise in a loop until approved",
        "use_case": "High-stakes output requiring quality assurance before delivery",
    },
    "hierarchical": {
        "name": "Hierarchical Delegation",
        "description": "Decompose the task into agent-tagged subtasks, run them concurrently, aggregate",
        "use_case": "Complex matters needing decomposition across legal domains",
    },
}

_VALID_PATTERNS = set(COLLABORATION_PATTERNS.keys())
_DEFAULT_PATTERN = "parallel"


# =============================================================================
# Multi-Agent Collaborator
# =============================================================================

class MultiAgentCollaborator:
    """Orchestrates complex legal tasks across the registered agents.

    The LangGraph structure:

        classify -> route(pattern)
          parallel      -> parallel_execute -> synthesize -> END
          sequential    -> sequential_execute -> synthesize -> END
          iterative     -> iterative_draft -> iterative_review -> (approved) synthesize -> END
          ^                                                     |
          +--------------------- iterative_revise <------------+
          hierarchical  -> hierarchical_decompose -> parallel_execute -> synthesize -> END
    """

    def __init__(self) -> None:
        self._llm_service: Optional[ChatLLMService] = None
        self._graph: Optional[CompiledStateGraph] = None

    @property
    def llm_service(self) -> ChatLLMService:
        if self._llm_service is None:
            self._llm_service = get_llm_service()
        return self._llm_service

    # -------------------------------------------------------------------------
    # Graph construction
    # -------------------------------------------------------------------------

    def _build_graph(self) -> StateGraph:
        workflow = StateGraph(CollaborationState)

        workflow.add_node("classify", self._classify_task)
        workflow.add_node("hierarchical_decompose", self._hierarchical_decompose)
        workflow.add_node("parallel_execute", self._parallel_execute)
        workflow.add_node("sequential_execute", self._sequential_execute)
        workflow.add_node("iterative_draft", self._iterative_draft)
        workflow.add_node("iterative_review", self._iterative_review)
        workflow.add_node("iterative_revise", self._iterative_revise)
        workflow.add_node("synthesize", self._synthesize_results)

        workflow.set_entry_point("classify")
        workflow.add_conditional_edges(
            "classify",
            self._route_by_pattern,
            {
                "parallel": "parallel_execute",
                "sequential": "sequential_execute",
                "iterative": "iterative_draft",
                "hierarchical": "hierarchical_decompose",
            },
        )

        workflow.add_edge("hierarchical_decompose", "parallel_execute")
        workflow.add_edge("parallel_execute", "synthesize")
        workflow.add_edge("sequential_execute", "synthesize")

        workflow.add_edge("iterative_draft", "iterative_review")
        workflow.add_conditional_edges(
            "iterative_review",
            self._review_gate,
            {
                "approved": "synthesize",
                "revise": "iterative_revise",
            },
        )
        workflow.add_edge("iterative_revise", "iterative_review")

        workflow.add_edge("synthesize", END)

        return workflow

    # -------------------------------------------------------------------------
    # Agent execution helpers
    # -------------------------------------------------------------------------

    async def _execute_agent(
        self,
        agent_id: str,
        task: str,
        context: dict[str, Any],
        results: dict[str, Any],
        details: list[dict[str, Any]],
    ) -> None:
        """Run one registered agent with failure isolation and timing."""
        spec = AGENT_REGISTRY.get(agent_id)
        if spec is None:
            details.append({"agent": agent_id, "status": "skipped", "error": "unknown agent"})
            return

        started = time.perf_counter()
        try:
            result = await spec.adapter(task, context)
            results[agent_id] = result
            details.append({
                "agent": agent_id,
                "status": "ok",
                "elapsed_ms": int((time.perf_counter() - started) * 1000),
                "preview": _text_of(result)[:200],
            })
        except Exception as exc:
            logger.warning("Agent %s failed: %s", agent_id, exc)
            details.append({
                "agent": agent_id,
                "status": "error",
                "elapsed_ms": int((time.perf_counter() - started) * 1000),
                "error": str(exc)[:300],
            })

    # -------------------------------------------------------------------------
    # Node: classify
    # -------------------------------------------------------------------------

    async def _classify_task(self, state: CollaborationState) -> dict[str, Any]:
        """Classify the task: pick agents from the registry and a pattern.

        Caller-provided context (required_agents + collaboration_pattern)
        takes precedence and skips LLM classification entirely.
        """
        query = state.get("query", "")
        context = state.get("context", {}) or {}

        explicit_agents = [
            a for a in (context.get("required_agents") or []) if a in AGENT_REGISTRY
        ]
        explicit_pattern = context.get("collaboration_pattern", "")
        if explicit_agents and explicit_pattern in _VALID_PATTERNS:
            # Explicit context is a deterministic override: use it as-is
            return {
                "intent": context.get("intent", "complex_analysis"),
                "context": {
                    **context,
                    "required_agents": explicit_agents[:4],
                    "collaboration_pattern": explicit_pattern,
                    "primary_agent": context.get("primary_agent") or explicit_agents[0],
                },
            }

        if not query:
            return {
                "intent": "unknown",
                "context": {
                    **context,
                    "required_agents": ["law_retrieval"],
                    "collaboration_pattern": _DEFAULT_PATTERN,
                },
            }

        llm = self.llm_service.get_llm(temperature=0.0)

        classification_prompt = f"""分析以下法律任务，判断需要哪些专业代理协作完成。

任务：{query}

可用代理：
{available_agents_description()}

协作模式说明：
- parallel（并行）: 多领域同时调研，结果合并
- sequential（串行）: 后一个代理依赖前一个代理的结论（如先检索后分析）
- iterative（迭代精修）: 产出初稿后质量评审、修订，直至通过（高风险输出适用）
- hierarchical（层级分解）: 任务需拆解为多个子任务分派给不同代理

请以JSON格式返回：
```json
{{
    "intent": "任务意图（如：contract_analysis_with_compliance）",
    "required_agents": ["从可用代理中选择的agent id列表，1-4个"],
    "collaboration_pattern": "parallel/sequential/iterative/hierarchical",
    "primary_agent": "iterative模式下的主产出代理id"
}}
```
仅返回JSON，不要附加说明。"""

        try:
            response = await llm.ainvoke([HumanMessage(content=classification_prompt)])
            content = response.content if hasattr(response, "content") else str(response)
            match = re.search(r'\{[\s\S]*\}', content)
            if match:
                parsed = json.loads(match.group())
                agents = [
                    a for a in parsed.get("required_agents", [])
                    if a in AGENT_REGISTRY
                ]
                if not agents:
                    agents = ["law_retrieval"]
                pattern = parsed.get("collaboration_pattern", _DEFAULT_PATTERN)
                if pattern not in _VALID_PATTERNS:
                    pattern = _DEFAULT_PATTERN
                # A single agent gains nothing from orchestration overhead
                if len(agents) == 1 and pattern in ("parallel", "hierarchical"):
                    pattern = "sequential"
                return {
                    "intent": parsed.get("intent", "complex_analysis"),
                    "context": {
                        **context,
                        "required_agents": agents[:4],
                        "collaboration_pattern": pattern,
                        "primary_agent": parsed.get("primary_agent") or agents[0],
                    },
                }
        except Exception as exc:
            logger.warning("Task classification failed: %s", exc)

        return {
            "intent": "complex_analysis",
            "context": {
                **context,
                "required_agents": ["law_retrieval", "legal_consult"],
                "collaboration_pattern": _DEFAULT_PATTERN,
            },
        }

    def _route_by_pattern(self, state: CollaborationState) -> str:
        """Route to the execution branch selected by the classifier."""
        pattern = state.get("context", {}).get("collaboration_pattern", _DEFAULT_PATTERN)
        if pattern in _VALID_PATTERNS:
            return pattern
        return _DEFAULT_PATTERN

    # -------------------------------------------------------------------------
    # Node: hierarchical_decompose
    # -------------------------------------------------------------------------

    async def _hierarchical_decompose(self, state: CollaborationState) -> dict[str, Any]:
        """Decompose the query into agent-tagged subtasks.

        Caller-provided context["subtasks"] (list of {agent, task}) skips
        LLM decomposition.
        """
        query = state.get("query", "")
        context = state.get("context", {}) or {}

        explicit_subtasks = [
            s for s in (context.get("subtasks") or [])
            if isinstance(s, dict) and s.get("agent") in AGENT_REGISTRY and s.get("task")
        ]
        if explicit_subtasks:
            return {
                "subtasks": explicit_subtasks,
                "context": {
                    **context,
                    "required_agents": [s["agent"] for s in explicit_subtasks],
                },
            }

        llm = self.llm_service.get_llm(temperature=0.0)
        decompose_prompt = f"""将以下法律任务拆解为可独立执行的子任务，并分派给合适的代理。

任务：{query}

可用代理：
{available_agents_description()}

请以JSON格式返回：
```json
[
    {{"agent": "agent id", "task": "该代理负责的具体子任务描述"}}
]
```
2-4个子任务，仅返回JSON数组。"""

        subtasks: list[dict[str, Any]] = []
        try:
            response = await llm.ainvoke([HumanMessage(content=decompose_prompt)])
            content = response.content if hasattr(response, "content") else str(response)
            match = re.search(r'\[[\s\S]*\]', content)
            if match:
                raw = json.loads(match.group())
                subtasks = [
                    {"agent": item["agent"], "task": item["task"]}
                    for item in raw
                    if isinstance(item, dict)
                    and item.get("agent") in AGENT_REGISTRY
                    and item.get("task")
                ]
        except Exception as exc:
            logger.warning("Hierarchical decomposition failed: %s", exc)

        if not subtasks:
            subtasks = [
                {"agent": agent_id, "task": query}
                for agent_id in context.get("required_agents", ["law_retrieval"])
                if agent_id in AGENT_REGISTRY
            ]

        return {
            "subtasks": subtasks,
            "context": {
                **context,
                "required_agents": [s["agent"] for s in subtasks],
            },
        }

    # -------------------------------------------------------------------------
    # Node: parallel_execute
    # -------------------------------------------------------------------------

    async def _parallel_execute(self, state: CollaborationState) -> dict[str, Any]:
        """Run all agents (or subtasks) concurrently via asyncio.gather."""
        query = state.get("query", "")
        context = state.get("context", {}) or {}
        agent_results: dict[str, Any] = dict(state.get("agent_results", {}))
        agent_details: list[dict[str, Any]] = list(state.get("agent_details", []))
        subtasks = state.get("subtasks", [])

        if subtasks:
            jobs = [
                self._execute_agent(s["agent"], s["task"], context, agent_results, agent_details)
                for s in subtasks
            ]
        else:
            jobs = [
                self._execute_agent(agent_id, query, context, agent_results, agent_details)
                for agent_id in context.get("required_agents", [])
                if agent_id in AGENT_REGISTRY
            ]

        if jobs:
            await asyncio.gather(*jobs)

        return {"agent_results": agent_results, "agent_details": agent_details}

    # -------------------------------------------------------------------------
    # Node: sequential_execute
    # -------------------------------------------------------------------------

    async def _sequential_execute(self, state: CollaborationState) -> dict[str, Any]:
        """Run agents in order, feeding prior findings into the next agent."""
        query = state.get("query", "")
        context = dict(state.get("context", {}) or {})
        agent_results: dict[str, Any] = dict(state.get("agent_results", {}))
        agent_details: list[dict[str, Any]] = list(state.get("agent_details", []))

        for agent_id in context.get("required_agents", []):
            if agent_id not in AGENT_REGISTRY:
                continue
            await self._execute_agent(agent_id, query, context, agent_results, agent_details)
            latest = agent_results.get(agent_id)
            if latest:
                context["prior_findings"] = _text_of(latest)[:2000]

        return {"agent_results": agent_results, "agent_details": agent_details, "context": context}

    # -------------------------------------------------------------------------
    # Iterative refinement nodes
    # -------------------------------------------------------------------------

    async def _iterative_draft(self, state: CollaborationState) -> dict[str, Any]:
        """Produce the initial draft with the primary agent."""
        query = state.get("query", "")
        context = state.get("context", {}) or {}
        agent_results: dict[str, Any] = dict(state.get("agent_results", {}))
        agent_details: list[dict[str, Any]] = list(state.get("agent_details", []))

        primary = context.get("primary_agent") or context.get("required_agents", ["law_retrieval"])[0]
        await self._execute_agent(primary, query, context, agent_results, agent_details)

        draft = _text_of(agent_results.get(primary, {}))
        return {
            "agent_results": agent_results,
            "agent_details": agent_details,
            "draft_output": draft,
            "iteration": 1,
        }

    async def _iterative_review(self, state: CollaborationState) -> dict[str, Any]:
        """Quality-review the current draft; feedback drives the revise gate."""
        query = state.get("query", "")
        draft = state.get("draft_output", "")
        iteration = state.get("iteration", 0)

        if not draft:
            return {"review_feedback": {"quality_score": 0, "approved": False, "issues": ["空初稿"]}}

        llm = self.llm_service.get_llm(temperature=0.2)
        review_prompt = f"""审查以下法律分析初稿的质量（第 {iteration} 轮）。

原始问题：{query}

初稿内容：
{draft[:4000]}

请评估法律准确性（法条引用）、完整性、逻辑性与实用性，以JSON返回：
```json
{{
    "quality_score": 0-100,
    "issues": ["发现的问题"],
    "suggestions": ["改进建议"],
    "approved": true/false
}}
```
仅返回JSON。"""

        feedback: dict[str, Any]
        try:
            response = await llm.ainvoke([HumanMessage(content=review_prompt)])
            content = response.content if hasattr(response, "content") else str(response)
            match = re.search(r'\{[\s\S]*\}', content)
            if match:
                feedback = json.loads(match.group())
            else:
                feedback = {"quality_score": 70, "approved": True, "issues": [], "suggestions": []}
        except Exception as exc:
            logger.warning("Iterative review failed: %s", exc)
            feedback = {"quality_score": 70, "approved": True, "issues": [], "suggestions": []}

        return {
            "review_feedback": feedback,
            "iteration": iteration + 1,
        }

    def _review_gate(self, state: CollaborationState) -> str:
        """Decide whether the draft is approved or needs another revision."""
        feedback = state.get("review_feedback", {})
        if feedback.get("approved") is True:
            return "approved"
        if feedback.get("quality_score", 0) >= 80:
            return "approved"
        if state.get("iteration", 0) >= state.get("max_iterations", 3):
            return "approved"
        return "revise"

    async def _iterative_revise(self, state: CollaborationState) -> dict[str, Any]:
        """Revise the draft using the accumulated review feedback."""
        query = state.get("query", "")
        draft = state.get("draft_output", "")
        feedback = state.get("review_feedback", {})
        context = state.get("context", {}) or {}

        llm = self.llm_service.get_llm(temperature=0.3, max_tokens=4096)
        revise_prompt = f"""请根据评审意见修订以下法律分析初稿。

原始问题：{query}

当前初稿：
{draft[:5000]}

评审意见（质量分 {feedback.get('quality_score', 'N/A')}）：
- 问题：{'；'.join(feedback.get('issues', [])) or '无'}
- 建议：{'；'.join(feedback.get('suggestions', [])) or '无'}

请输出修订后的完整文本，保持法条引用格式（（《X法》第X条））。"""

        try:
            response = await llm.ainvoke([HumanMessage(content=revise_prompt)])
            revised = response.content if hasattr(response, "content") else str(response)
        except Exception as exc:
            logger.warning("Iterative revision failed: %s", exc)
            revised = draft

        return {"draft_output": revised}

    # -------------------------------------------------------------------------
    # Node: synthesize
    # -------------------------------------------------------------------------

    async def _synthesize_results(self, state: CollaborationState) -> dict[str, Any]:
        """Synthesize agent outputs (or the refined draft) into the final report."""
        query = state.get("query", "")
        agent_results = state.get("agent_results", {})
        context = state.get("context", {}) or {}
        pattern = context.get("collaboration_pattern", _DEFAULT_PATTERN)

        # Iterative pattern: the draft already carries the full refinement history
        if pattern == "iterative":
            from app.prompts.legal_prompts import LEGAL_DISCLAIMER
            draft = state.get("draft_output", "")
            feedback = state.get("review_feedback", {})
            final = draft + (
                f"\n\n---\n质量评审：第 {state.get('iteration', 0)} 轮，评分 "
                f"{feedback.get('quality_score', 'N/A')}/100"
            ) + LEGAL_DISCLAIMER
            return {"final_response": final}

        llm = self.llm_service.get_llm(temperature=0.3, max_tokens=4096)

        results_summary = self._format_agent_results(agent_results)
        subtasks = state.get("subtasks", [])
        subtask_section = ""
        if subtasks:
            subtask_section = "\n\n子任务分派：\n" + "\n".join(
                f"- {s['agent']}: {s['task']}" for s in subtasks
            )

        synthesis_prompt = f"""基于以下多个专业代理的分析结果，综合生成一份完整的法律分析报告。

原始问题：{query}{subtask_section}

各代理分析结果：
{results_summary}

请综合以上信息，生成一份结构清晰、逻辑严谨、引用准确的法律分析报告。报告应：
1. 准确引用相关法律条文，格式为（（《X法》第X条））；若某结论无检索到的法条依据，须明确说明"未检索到直接相关的法律条文"，禁止编造法条
2. 全面分析法律问题
3. 提供可行的实务建议
4. 标注不确定性和风险点"""

        try:
            response = await llm.ainvoke([HumanMessage(content=synthesis_prompt)])
            final_response = response.content if hasattr(response, "content") else str(response)
        except Exception as exc:
            logger.error("Synthesis failed: %s", exc)
            final_response = "综合分析时出现错误，请稍后重试。"

        from app.prompts.legal_prompts import LEGAL_DISCLAIMER
        final_response += LEGAL_DISCLAIMER

        return {"final_response": final_response}

    def _format_agent_results(self, agent_results: dict[str, Any]) -> str:
        """Format agent results for synthesis."""
        parts = []
        for agent_name, result in agent_results.items():
            content = _text_of(result)
            if content:
                parts.append(f"### {agent_name} 的分析：\n{content[:2000]}")
        return "\n\n".join(parts) if parts else "无分析结果"

    # -------------------------------------------------------------------------
    # Public run method
    # -------------------------------------------------------------------------

    async def run(self, query: str, context: dict = None) -> dict:
        """Execute multi-agent collaboration.

        Args:
            query: The complex legal query requiring multiple agents
            context: Optional initial context; may include collaboration_pattern
                     or required_agents to override LLM classification

        Returns:
            Dictionary with final_response and collaboration metadata
        """
        if self._graph is None:
            self._graph = self._build_graph().compile()

        initial_state: CollaborationState = {
            "query": query,
            "intent": "",
            "context": context or {},
            "messages": [],
            "agent_results": {},
            "agent_details": [],
            "subtasks": [],
            "draft_output": "",
            "review_feedback": {},
            "final_response": "",
            "iteration": 0,
            "max_iterations": 3,
        }

        try:
            result = await self._graph.ainvoke(initial_state)

            return {
                "final_response": result.get("final_response", ""),
                "intent": result.get("intent", ""),
                "collaboration_pattern": result.get("context", {}).get("collaboration_pattern", ""),
                "agents_involved": [
                    d["agent"] for d in result.get("agent_details", []) if d.get("status") == "ok"
                ],
                "agent_details": result.get("agent_details", []),
                "iterations": result.get("iteration", 0),
            }
        except Exception as exc:
            logger.error("Collaboration execution failed: %s", exc)
            return {
                "final_response": f"协作执行失败：{str(exc)}",
                "intent": "",
                "collaboration_pattern": "",
                "agents_involved": [],
                "agent_details": [],
                "iterations": 0,
            }


# =============================================================================
# Factory function
# =============================================================================

def create_multi_agent_collaborator() -> MultiAgentCollaborator:
    """Create and return a MultiAgentCollaborator instance."""
    return MultiAgentCollaborator()
