"""Colloquial Rewrite Agent -- 口语/方言/日常表述 → 专业法律表述.

Converts everyday language (including dialect-flavoured phrasing) into precise
legal terminology so downstream agents (intent routing, RAG retrieval, document
generation) work with professional formulations.

LangGraph workflow:
    detect_language_style -> rewrite_to_legal -> extract_legal_elements

Output dict:
    normalized_text: 专业法律表述
    is_colloquial:   是否检测到口语化/方言表述
    legal_domain:    法律领域（劳动/婚姻/合同/刑事/侵权/其他）
    key_issues:      争议焦点列表
    legal_terms:     对应的专业法律术语
    confidence:      转换置信度 0-1
"""
import asyncio
import json
import logging
from typing import Any, Optional

from langgraph.graph import StateGraph, END
from pydantic import BaseModel, Field

from app.agents.base_agent import BaseAgent, AgentState
from app.services.llm_service import get_raw_llm_service

logger = logging.getLogger(__name__)


# =============================================================================
# Prompt
# =============================================================================

COLLOQUIAL_REWRITE_PROMPT = """你是一名资深法律工作者，擅长将当事人的日常口语（可能带方言色彩）转写为规范的法律表述。

用户的原始陈述：
{original_text}

请完成三件事并以JSON返回：
1. rewrite：将原始陈述改写为专业、准确、无口语的法律表述。要求：
   - 将日常用语替换为规范法律术语（如"干活没给钱"→"用人单位拖欠劳动报酬"；"把人打了"→"故意伤害他人身体"）
   - 保留全部关键事实（时间、地点、金额、人物关系、行为），不得虚构或遗漏
   - 语句符合法律文书的书面规范
2. domain：判断法律领域，从以下选择一个：劳动争议、婚姻家庭、合同纠纷、侵权责任、刑事、公司股权、房产纠纷、知识产权、行政、其他
3. issues：列出该陈述涉及的争议焦点（1-5条，短语形式）
4. terms：列出原始口语对应的专业法律术语映射（如 {{"没给钱": "拖欠劳动报酬"}}，0-8条）
5. colloquial：布尔值，原始陈述是否含有明显口语化/方言表述
6. confidence：0到1之间的小数，表示你对转换准确性的置信度

仅返回JSON，不要返回其他内容：
{{"rewrite": "...", "domain": "...", "issues": ["..."], "terms": {{"...": "..."}}, "colloquial": true, "confidence": 0.9}}"""


# =============================================================================
# State
# =============================================================================

class RewriteState(AgentState):
    """State for the colloquial rewrite workflow."""

    original_text: str = Field(default="")
    llm_json: dict[str, Any] = Field(default_factory=dict)
    normalized_text: str = Field(default="")
    is_colloquial: bool = Field(default=False)
    legal_domain: str = Field(default="")
    key_issues: list[str] = Field(default_factory=list)
    legal_terms: dict[str, str] = Field(default_factory=dict)
    confidence: float = Field(default=0.0)


# =============================================================================
# Agent
# =============================================================================

class ColloquialRewriteAgent(BaseAgent[RewriteState]):
    """将口语化/方言法律咨询转写为专业法律表述的智能体。"""

    def __init__(self, name: str = "colloquial_rewrite_agent") -> None:
        super().__init__(name=name)
        self._llm_service = None

    # ------------------------------------------------------------------
    # LangGraph workflow
    # ------------------------------------------------------------------
    def _build_graph(self) -> StateGraph:
        builder = StateGraph(RewriteState)
        builder.add_node("detect_language_style", self._node_detect)
        builder.add_node("rewrite_to_legal", self._node_rewrite)
        builder.add_node("extract_legal_elements", self._node_extract)

        builder.set_entry_point("detect_language_style")
        builder.add_edge("detect_language_style", "rewrite_to_legal")
        builder.add_edge("rewrite_to_legal", "extract_legal_elements")
        builder.add_edge("extract_legal_elements", END)
        return builder

    async def _call_llm(self, user_content: str, temperature: float = 0.2) -> str:
        if self._llm_service is None:
            self._llm_service = get_raw_llm_service()
        result = await self._llm_service.chat(
            messages=[{"role": "user", "content": user_content}],
            temperature=temperature,
            max_tokens=1500,
        )
        return result.get("content", "")

    async def _node_detect(self, state: RewriteState) -> RewriteState:
        """Detect colloquial style; short-circuit formal input via heuristics."""
        text = state.original_text.strip()
        # Heuristic quick-path: formal legal text is long and term-dense.
        formal_markers = (
            "依据", "根据《", "之规定", "当事人", "原告", "被告", "诉请", "判令",
            "应当承担", "违反了", "请求法院", "特此", "申请人",
        )
        if len(text) >= 120 and any(m in text for m in formal_markers):
            state.llm_json = {"colloquial": False}
        return state

    async def _node_rewrite(self, state: RewriteState) -> RewriteState:
        """Rewrite colloquial text into professional legal language."""
        if state.llm_json and state.llm_json.get("colloquial") is False:
            state.normalized_text = state.original_text
            state.is_colloquial = False
            state.confidence = 1.0
            return state
        try:
            raw = await self._call_llm(
                COLLOQUIAL_REWRITE_PROMPT.format(original_text=state.original_text)
            )
            state.llm_json = _parse_llm_json(raw) or {}
        except Exception as exc:
            logger.warning("Colloquial rewrite LLM call failed: %s", exc)
            state.llm_json = {}

        if not state.llm_json.get("rewrite"):
            # Fallback: keep original text untouched
            state.normalized_text = state.original_text
            state.is_colloquial = False
            state.confidence = 0.0
        else:
            state.normalized_text = str(state.llm_json["rewrite"]).strip()
            state.is_colloquial = bool(state.llm_json.get("colloquial", True))
            state.confidence = _to_float(state.llm_json.get("confidence"), 0.8)
        return state

    async def _node_extract(self, state: RewriteState) -> RewriteState:
        """Extract legal domain, issues and term mappings from LLM output."""
        data = state.llm_json or {}
        state.legal_domain = str(data.get("domain", "") or "")
        issues = data.get("issues") or []
        if isinstance(issues, list):
            state.key_issues = [str(i).strip() for i in issues if str(i).strip()][:5]
        terms = data.get("terms") or {}
        if isinstance(terms, dict):
            state.legal_terms = {
                str(k): str(v) for k, v in list(terms.items())[:8]
                if k and v
            }
        return state

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def run(self, input_data: dict[str, Any]) -> dict[str, Any]:
        """Sync entry point (matches BaseAgent contract)."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            # Called from async context -- delegate to async run
            raise RuntimeError("Use run_async() inside a running event loop")
        return asyncio.run(self.run_async(input_data))

    async def run_async(self, input_data: dict[str, Any]) -> dict[str, Any]:
        """Async execution of the rewrite workflow."""
        text = str(input_data.get("text", "")).strip()
        if not text:
            return {
                "normalized_text": "",
                "is_colloquial": False,
                "legal_domain": "",
                "key_issues": [],
                "legal_terms": {},
                "confidence": 0.0,
            }

        graph = self.compile()
        state = RewriteState(original_text=text[:2000])
        try:
            final = await graph.ainvoke(state)
        except Exception as exc:
            logger.warning("Rewrite graph failed: %s (fallback to original text)", exc)
            return {
                "normalized_text": text,
                "is_colloquial": False,
                "legal_domain": "",
                "key_issues": [],
                "legal_terms": {},
                "confidence": 0.0,
            }

        # LangGraph ainvoke returns a plain dict, not the pydantic state model
        if isinstance(final, dict):
            def _get(key, default):
                value = final.get(key, default)
                return default if value is None else value

            normalized = _get("normalized_text", "") or text
            return {
                "normalized_text": normalized,
                "is_colloquial": bool(_get("is_colloquial", False)),
                "legal_domain": str(_get("legal_domain", "")),
                "key_issues": list(_get("key_issues", []) or []),
                "legal_terms": dict(_get("legal_terms", {}) or {}),
                "confidence": _to_float(_get("confidence", 0.0), 0.0),
            }

        normalized = final.normalized_text or text
        return {
            "normalized_text": normalized,
            "is_colloquial": final.is_colloquial,
            "legal_domain": final.legal_domain,
            "key_issues": final.key_issues,
            "legal_terms": final.legal_terms,
            "confidence": final.confidence,
        }


_agent: Optional[ColloquialRewriteAgent] = None


def get_colloquial_rewrite_agent() -> ColloquialRewriteAgent:
    global _agent
    if _agent is None:
        _agent = ColloquialRewriteAgent()
    return _agent


# =============================================================================
# Helpers
# =============================================================================

def _parse_llm_json(raw: str) -> Optional[dict[str, Any]]:
    """Parse the LLM's JSON payload tolerantly (handles ```json fences)."""
    if not raw:
        return None
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None


def _to_float(value: Any, default: float) -> float:
    try:
        f = float(value)
        return min(max(f, 0.0), 1.0)
    except (TypeError, ValueError):
        return default
