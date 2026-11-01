"""
Deep Thinking (Reasoning) Agent -- Multi-step legal reasoning with
Chain-of-Thought visualization and IRAC framework.

Supports:
1. DeepSeek-R1 reasoning model integration
2. Structured IRAC (Issue-Rule-Application-Conclusion) analysis
3. Chain-of-Thought step-by-step visualization
4. Self-verification and confidence scoring
5. Streaming reasoning output for real-time CoT display

This module upgrades the system from "answering questions" to
"reasoning through complex legal problems" — a key commercial
differentiator for legal AI products.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Optional

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langgraph.graph import StateGraph, END

from app.agents.base_agent import BaseAgent, AgentState
from app.core.config import settings
from app.services.llm_service import get_raw_llm_service, get_llm_service, LLMService

logger = logging.getLogger(__name__)


# =============================================================================
# Reasoning State
# =============================================================================

@dataclass
class ReasoningStep:
    """A single step in the reasoning chain."""
    step_id: int
    step_type: str  # "issue" | "rule" | "application" | "conclusion" | "verification"
    title: str
    content: str
    confidence: float = 0.0
    sources: list[str] = field(default_factory=list)


@dataclass
class ReasoningResult:
    """Complete reasoning result with CoT trace."""
    query: str
    thinking_process: str  # Full thinking/reasoning text
    reasoning_steps: list[ReasoningStep] = field(default_factory=list)
    final_answer: str = ""
    confidence: float = 0.0
    # Heterogeneous by design: the LLM returns IRAC phase *text* plus a
    # ``confidence`` float and a ``sources`` list.  See the note on
    # ``DeepThinkResponse.irac_analysis``.
    irac_analysis: dict[str, Any] = field(default_factory=dict)
    verification_notes: list[str] = field(default_factory=list)
    elapsed_seconds: float = 0.0
    model_used: str = ""
    tokens_used: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "thinking_process": self.thinking_process,
            "reasoning_steps": [
                {
                    "step_id": s.step_id,
                    "step_type": s.step_type,
                    "title": s.title,
                    "content": s.content,
                    "confidence": s.confidence,
                    "sources": s.sources,
                }
                for s in self.reasoning_steps
            ],
            "final_answer": self.final_answer,
            "confidence": self.confidence,
            "irac_analysis": self.irac_analysis,
            "verification_notes": self.verification_notes,
            "elapsed_seconds": self.elapsed_seconds,
            "model_used": self.model_used,
            "tokens_used": self.tokens_used,
            "metadata": self.metadata,
        }


# =============================================================================
# Deep Thinking Agent
# =============================================================================

class DeepThinkingAgent:
    """Agent for complex legal reasoning with visible chain-of-thought.

    Uses a multi-phase approach:
    1. Problem Decomposition — break the question into sub-issues
    2. IRAC Analysis — structured legal reasoning
    3. Self-Verification — check consistency and completeness
    4. Answer Synthesis — produce final answer with confidence score
    """

    def __init__(self, model_override: str | None = None) -> None:
        self._llm_service = get_raw_llm_service()
        self._model_override = model_override

    async def think(
        self,
        query: str,
        context: dict[str, Any] | None = None,
        rag_context: str = "",
        use_reasoning_model: bool = True,
        depth: str = "deep",
    ) -> ReasoningResult:
        """Execute deep thinking on a legal question.

        Args:
            query: The legal question to reason about.
            context: Optional context dict (conversation history, etc.).
            rag_context: Retrieved legal knowledge for grounding.
            use_reasoning_model: Whether to use a reasoning-capable model.
            depth: "standard" | "deep" | "expert". Controls how many sub-issues
                are analysed and whether the self-verification pass runs, so the
                setting the UI exposes actually changes the work performed.

        Returns:
            ReasoningResult with full chain-of-thought trace.
        """
        start_time = time.time()
        max_sub_issues, run_verification = self._depth_policy(depth)
        context = context or {}

        # Phase 1: Problem Decomposition
        decomposition = await self._decompose_problem(query, rag_context)

        # Phase 2: IRAC Analysis for each sub-issue — the sub-issues are
        # independent, so they run concurrently (expert depth goes from 8
        # sequential LLM calls to roughly one call of wall-clock latency).
        irac_results = await self._irac_analysis_batch(
            query,
            decomposition.get("sub_issues", [])[:max_sub_issues],
            rag_context,
        )

        # Phase 3: Self-Verification (skipped at "standard" depth)
        if run_verification:
            verification = await self._self_verify(
                query, decomposition, irac_results, rag_context
            )
        else:
            verification = {
                "passed": True,
                "overall_confidence": 0.6,
                "issues": [],
                "skipped": True,
                "note": "标准分析深度不执行自我验证",
            }

        # Phase 4: Synthesize final answer
        final_answer = await self._synthesize_answer(
            query, decomposition, irac_results, verification, rag_context
        )

        elapsed = time.time() - start_time

        # Build reasoning steps
        steps: list[ReasoningStep] = []
        step_id = 1

        # Decomposition step
        steps.append(ReasoningStep(
            step_id=step_id,
            step_type="issue",
            title="问题分解",
            content=decomposition.get("analysis", ""),
            confidence=decomposition.get("confidence", 0.7),
        ))
        step_id += 1

        # IRAC steps
        for i, irac in enumerate(irac_results):
            for phase_name, phase_content in irac.items():
                if phase_name in ("issue", "rule", "application", "conclusion"):
                    steps.append(ReasoningStep(
                        step_id=step_id,
                        step_type=phase_name,
                        title=f"子问题{i+1} - {phase_name.capitalize()}",
                        content=str(phase_content),
                        confidence=irac.get("confidence", 0.7),
                    ))
                    step_id += 1

        # Verification step
        steps.append(ReasoningStep(
            step_id=step_id,
            step_type="verification",
            title="自我验证",
            content="\n".join(verification.get("notes", [])),
            confidence=verification.get("overall_confidence", 0.7),
        ))

        # Calculate overall confidence
        confidences = [s.confidence for s in steps if s.confidence > 0]
        avg_confidence = sum(confidences) / len(confidences) if confidences else 0.5

        return ReasoningResult(
            query=query,
            thinking_process=decomposition.get("analysis", ""),
            reasoning_steps=steps,
            final_answer=final_answer.get("content", ""),
            confidence=avg_confidence,
            irac_analysis=irac_results[0] if irac_results else {},
            verification_notes=verification.get("notes", []),
            elapsed_seconds=elapsed,
            model_used=self._model_override or settings.DEEPSEEK_MODEL,
            tokens_used=final_answer.get("tokens", {}).get("total", 0),
            metadata={
                "sub_issues_count": len(decomposition.get("sub_issues", [])),
                "irac_analyses_count": len(irac_results),
                "verification_passed": verification.get("passed", False),
            },
        )

    _LAW_REF_RE = re.compile(
        r"《([^》]{1,40})》(?:\s*(第[一二三四五六七八九十百千零〇0-9]+条"
        r"(?:第[一二三四五六七八九十百千零〇0-9]+款)?"
        r"(?:第[一二三四五六七八九十百千零〇0-9]+项)?))?"
    )

    @classmethod
    def _extract_law_citations(cls, text: str) -> list[dict[str, str]]:
        """Extract 《法律名》第X条 references from LLM prose.

        The streaming contract the UI consumes expects
        ``{"text": ..., "url": ...}`` items for the IRAC "Rule" panel and
        ``{"title", "detail", "url", "type"}`` items for the citation list.
        Both are derived from this single extraction pass.
        """
        citations: list[dict[str, str]] = []
        seen: set[str] = set()
        for match in cls._LAW_REF_RE.finditer(text or ""):
            law_name = match.group(1).strip()
            article = (match.group(2) or "").strip()
            label = f"《{law_name}》{article}" if article else f"《{law_name}》"
            if label in seen:
                continue
            seen.add(label)
            citations.append({"text": label, "url": ""})
        return citations[:12]

    def _depth_policy(self, depth: str) -> tuple[int, bool]:
        """Map the UI's 分析深度 setting to real work limits.

        Returns (max_sub_issues, run_self_verification). Before this existed the
        dropdown only changed a label on screen.
        """
        policies = {
            "standard": (2, False),
            "deep": (4, True),
            "expert": (8, True),
        }
        return policies.get((depth or "deep").lower(), policies["deep"])

    async def think_stream(
        self,
        query: str,
        context: dict[str, Any] | None = None,
        rag_context: str = "",
        depth: str = "deep",
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Stream the deep thinking process with real-time updates.

        Yields progress events as the reasoning unfolds, allowing
        the frontend to display the chain-of-thought in real-time.

        Args:
            depth: "standard" | "deep" | "expert" — see :meth:`_depth_policy`.
        """
        context = context or {}
        start_time = time.time()
        max_sub_issues, run_verification = self._depth_policy(depth)

        yield {
            "type": "thinking_start",
            "query": query,
            "phase": "decomposition",
            "depth": depth,
            "message": "正在分析问题...",
        }

        # Phase 1: Problem Decomposition
        decomposition = await self._decompose_problem(query, rag_context)
        yield {
            "type": "phase_complete",
            "phase": "decomposition",
            "data": decomposition,
            "message": f"问题分解完成，识别出 {len(decomposition.get('sub_issues', []))} 个子问题",
        }

        # Reasoning step for the decomposition phase (drives the CoT cards).
        reasoning_steps_count = 1
        yield {
            "type": "reasoning_step",
            "phase": "decomposition",
            "data": {
                "step_type": "issue",
                "title": "问题分解",
                "content": decomposition.get("analysis", ""),
                "confidence": decomposition.get("confidence", 0.7),
                "sources": decomposition.get("legal_domains", []),
            },
        }

        # Phase 2: IRAC Analysis (stream each sub-issue)
        irac_results = []
        emitted_citations: list[dict[str, str]] = []
        emitted_labels: set[str] = set()
        sub_issues = decomposition.get("sub_issues", [])[:max_sub_issues]
        # Analyse every sub-issue concurrently up front, then stream the results
        # in the original 1..N order: SSE consumers, citation de-duplication and
        # the final ``irac_payload`` all rely on that ordering.
        irac_batch = await self._irac_analysis_batch(query, sub_issues, rag_context)
        for i, sub_issue in enumerate(sub_issues):
            yield {
                "type": "phase_start",
                "phase": "irac",
                "sub_issue_index": i,
                "message": f"正在分析子问题 {i+1}/{len(sub_issues)}: {sub_issue[:50]}...",
            }

            irac = irac_batch[i]
            irac_results.append(irac)
            confidence = irac.get("confidence", 0.7)
            citations = self._extract_law_citations(str(irac.get("rule", "")))

            # Contract consumed by DeepThink.vue: flat IRAC fields + citations.
            yield {
                "type": "irac_update",
                "phase": "irac",
                "sub_issue_index": i,
                "data": {
                    "sub_issue": sub_issue,
                    "issue": irac.get("issue", ""),
                    "rule": irac.get("rule", ""),
                    "application": irac.get("application", ""),
                    "conclusion": irac.get("conclusion", ""),
                    "citations": citations,
                    "confidence": confidence,
                },
            }

            for step_type, label in (
                ("issue", "争点识别"),
                ("rule", "适用规则"),
                ("application", "规则适用"),
                ("conclusion", "得出结论"),
            ):
                yield {
                    "type": "reasoning_step",
                    "phase": "irac",
                    "sub_issue_index": i,
                    "data": {
                        "step_type": step_type,
                        "title": f"子问题 {i+1} · {label}",
                        "content": str(irac.get(step_type, "")),
                        "confidence": confidence,
                        "sources": [],
                    },
                }
                reasoning_steps_count += 1

            for cite in citations:
                if cite["text"] in emitted_labels:
                    continue
                emitted_labels.add(cite["text"])
                entry = {
                    "title": cite["text"],
                    "detail": f"子问题 {i+1} 适用规则引用",
                    "url": cite["url"],
                    "type": "法律",
                }
                emitted_citations.append(entry)
                yield {
                    "type": "source_citation",
                    "phase": "irac",
                    "sub_issue_index": i,
                    "data": entry,
                }

            yield {
                "type": "confidence_update",
                "phase": "irac",
                "sub_issue_index": i,
                "confidence": confidence,
            }

            # Kept for backward compatibility with non-UI SSE consumers.
            yield {
                "type": "irac_complete",
                "sub_issue_index": i,
                "sub_issue": sub_issue,
                "data": irac,
                "message": f"子问题 {i+1} IRAC分析完成",
            }

        # Phase 3: Self-Verification (skipped at "standard" depth)
        if run_verification:
            yield {
                "type": "phase_start",
                "phase": "verification",
                "message": "正在进行自我验证...",
            }

            verification = await self._self_verify(
                query, decomposition, irac_results, rag_context
            )
            yield {
                "type": "phase_complete",
                "phase": "verification",
                "data": verification,
                "message": "自我验证完成",
            }
        else:
            verification = {
                "passed": True,
                "overall_confidence": 0.6,
                "issues": [],
                "skipped": True,
                "note": "标准分析深度不执行自我验证",
            }
            yield {
                "type": "phase_complete",
                "phase": "verification",
                "data": verification,
                "message": "标准深度：跳过自我验证",
            }

        # Phase 4: Final Answer (streamed token by token)
        yield {
            "type": "confidence_update",
            "phase": "verification",
            "confidence": verification.get("overall_confidence", 0.7),
        }
        yield {
            "type": "reasoning_step",
            "phase": "verification",
            "data": {
                "step_type": "verification",
                "title": "自我验证",
                "content": "\n".join(
                    list(verification.get("notes", []))
                    + list(verification.get("issues_found", []))
                ),
                "confidence": verification.get("overall_confidence", 0.7),
                "sources": [],
            },
        }
        reasoning_steps_count += 1

        yield {
            "type": "phase_start",
            "phase": "synthesis",
            "message": "正在生成最终回答...",
        }

        final_content = ""
        async for token in self._stream_synthesis(
            query, decomposition, irac_results, verification, rag_context
        ):
            final_content += token
            yield {
                "type": "token",
                "content": token,
            }

        # Flatten multi-sub-issue IRAC into the single object the UI renders.
        if not irac_results:
            irac_payload: dict[str, str] = {}
        elif len(irac_results) == 1:
            irac_payload = {
                key: str(irac_results[0].get(key, ""))
                for key in ("issue", "rule", "application", "conclusion")
            }
        else:
            irac_payload = {
                key: "\n\n".join(
                    f"[子问题 {i + 1}] {str(irac.get(key, ''))}"
                    for i, irac in enumerate(irac_results)
                )
                for key in ("issue", "rule", "application", "conclusion")
            }

        elapsed = time.time() - start_time
        yield {
            "type": "thinking_complete",
            "final_answer": final_content,
            "elapsed_seconds": round(elapsed, 2),
            "confidence": verification.get("overall_confidence", 0.7),
            "reasoning_steps_count": reasoning_steps_count,
            "sub_issues_count": len(sub_issues),
            "verification_passed": verification.get("passed", False),
            "irac": irac_payload,
            "source_citations": emitted_citations,
        }

    # -------------------------------------------------------------------------
    # Phase 1: Problem Decomposition
    # -------------------------------------------------------------------------

    async def _decompose_problem(
        self, query: str, rag_context: str
    ) -> dict[str, Any]:
        """Break down the legal question into sub-issues."""
        system_prompt = """你是一位法律问题分析专家，擅长将复杂的法律问题分解为可分析的子问题。

请将用户的法律问题分解为若干个子问题，每个子问题应该是独立可分析的。

输出JSON格式：
```json
{
    "analysis": "问题总体分析",
    "sub_issues": ["子问题1", "子问题2", ...],
    "legal_domains": ["涉及的法律领域"],
    "complexity": "simple/medium/complex",
    "confidence": 0.0-1.0
}
```
仅返回JSON。"""

        user_prompt = f"请分析以下法律问题：\n\n{query}"
        if rag_context:
            user_prompt += f"\n\n相关法律知识：\n{rag_context}"

        try:
            result = await self._llm_service.chat(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.2,
                max_tokens=2000,
                model=self._model_override,
            )
            content = result.get("content", "")
            json_match = re.search(r'\{[\s\S]*\}', content)
            if json_match:
                return json.loads(json_match.group())
        except Exception as exc:
            logger.warning("Problem decomposition failed: %s", exc)

        return {
            "analysis": query,
            "sub_issues": [query],
            "legal_domains": [],
            "complexity": "medium",
            "confidence": 0.5,
        }

    # -------------------------------------------------------------------------
    # Phase 2: IRAC Analysis
    # -------------------------------------------------------------------------

    @staticmethod
    def _irac_fallback(sub_issue: str) -> dict[str, Any]:
        """Low-confidence placeholder so one failed sub-issue cannot abort a run."""
        return {
            "issue": sub_issue,
            "rule": "需要进一步检索相关法律条文",
            "application": "待分析",
            "conclusion": "待得出",
            "confidence": 0.3,
            "reasoning_quality": "low",
        }

    async def _irac_analysis_batch(
        self, original_query: str, sub_issues: list[str], rag_context: str
    ) -> list[dict[str, str]]:
        """Analyse every sub-issue concurrently, keeping the input ordering.

        ``asyncio.gather`` returns results positionally, so the caller can still
        emit events and build the final IRAC payload in 1..N order.
        """
        if not sub_issues:
            return []

        gathered = await asyncio.gather(
            *(
                self._irac_analysis(original_query, sub_issue, rag_context)
                for sub_issue in sub_issues
            ),
            return_exceptions=True,
        )

        results: list[dict[str, str]] = []
        for index, sub_issue in enumerate(sub_issues):
            result = gathered[index]
            if isinstance(result, dict):
                results.append(result)
                continue
            logger.warning(
                "IRAC analysis crashed for sub-issue %d: %s", index + 1, result
            )
            results.append(self._irac_fallback(sub_issue))
        return results

    async def _irac_analysis(
        self, original_query: str, sub_issue: str, rag_context: str
    ) -> dict[str, str]:
        """Perform IRAC analysis on a sub-issue.

        IRAC = Issue, Rule, Application, Conclusion
        """
        system_prompt = """你是一位法律推理专家。请使用IRAC方法（Issue-Rule-Application-Conclusion）对以下法律子问题进行结构化分析。

IRAC方法：
1. **Issue（争点）**：明确法律问题是什么
2. **Rule（规则）**：找到适用的法律规则和条文
3. **Application（适用）**：将法律规则适用于具体事实
4. **Conclusion（结论）**：得出结论

输出JSON格式：
```json
{
    "issue": "明确的法律问题",
    "rule": "适用的法律规则和条文（引用具体法条）",
    "application": "将规则适用于本案事实的分析过程",
    "conclusion": "法律结论",
    "confidence": 0.0-1.0,
    "reasoning_quality": "分析质量自评（high/medium/low）"
}
```
仅返回JSON。"""

        user_prompt = f"原始问题：{original_query}\n\n子问题：{sub_issue}"
        if rag_context:
            user_prompt += f"\n\n相关法律知识：\n{rag_context}"

        try:
            result = await self._llm_service.chat(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.2,
                max_tokens=3000,
                model=self._model_override,
            )
            content = result.get("content", "")
            json_match = re.search(r'\{[\s\S]*\}', content)
            if json_match:
                return json.loads(json_match.group())
        except Exception as exc:
            logger.warning("IRAC analysis failed for '%s': %s", sub_issue[:30], exc)

        return self._irac_fallback(sub_issue)

    # -------------------------------------------------------------------------
    # Phase 3: Self-Verification
    # -------------------------------------------------------------------------

    async def _self_verify(
        self,
        query: str,
        decomposition: dict[str, Any],
        irac_results: list[dict[str, str]],
        rag_context: str,
    ) -> dict[str, Any]:
        """Verify the reasoning for consistency and completeness."""
        system_prompt = """你是一位法律推理审查专家。请审查以下法律推理过程的准确性和完整性。

审查维度：
1. 法条引用是否正确
2. 推理逻辑是否严密
3. 是否遗漏重要法律问题
4. 结论是否有充分依据
5. 是否存在矛盾之处

输出JSON格式：
```json
{
    "passed": true/false,
    "overall_confidence": 0.0-1.0,
    "notes": ["审查意见1", "审查意见2"],
    "issues_found": ["发现的问题"],
    "suggestions": ["改进建议"]
}
```
仅返回JSON。"""

        irac_summary = json.dumps(irac_results, ensure_ascii=False, indent=2)
        user_prompt = f"""原始问题：{query}

问题分解：{json.dumps(decomposition, ensure_ascii=False)}

IRAC分析结果：
{irac_summary[:3000]}
"""
        if rag_context:
            user_prompt += f"\n相关法律知识：\n{rag_context[:1000]}"

        try:
            result = await self._llm_service.chat(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.1,
                max_tokens=2000,
                model=self._model_override,
            )
            content = result.get("content", "")
            json_match = re.search(r'\{[\s\S]*\}', content)
            if json_match:
                return json.loads(json_match.group())
        except Exception as exc:
            logger.warning("Self-verification failed: %s", exc)

        return {
            "passed": True,
            "overall_confidence": 0.6,
            "notes": ["验证过程出现异常，建议人工复核"],
            "issues_found": [],
            "suggestions": [],
        }

    # -------------------------------------------------------------------------
    # Phase 4: Answer Synthesis
    # -------------------------------------------------------------------------

    async def _synthesize_answer(
        self,
        query: str,
        decomposition: dict[str, Any],
        irac_results: list[dict[str, str]],
        verification: dict[str, Any],
        rag_context: str,
    ) -> dict[str, Any]:
        """Synthesize the final answer from all reasoning phases."""
        system_prompt = """你是一位专业的法律分析专家。请基于以下多步推理结果，综合生成一份结构清晰、逻辑严谨、引用准确的法律分析报告。

要求：
1. 使用Markdown格式
2. 准确引用相关法律条文
3. 全面覆盖所有子问题
4. 明确标注不确定性和风险点
5. 提供可操作的实务建议
6. 结尾附上免责声明"""

        irac_text = "\n\n".join([
            f"### 子问题 {i+1}: {irac.get('issue', '')}\n"
            f"**规则**: {irac.get('rule', '')}\n"
            f"**适用**: {irac.get('application', '')}\n"
            f"**结论**: {irac.get('conclusion', '')}"
            for i, irac in enumerate(irac_results)
        ])

        verification_text = "\n".join(verification.get("notes", []))

        user_prompt = f"""原始问题：{query}

## 问题分解
{decomposition.get('analysis', '')}

## IRAC分析结果
{irac_text}

## 验证意见
{verification_text}

请综合以上分析生成最终法律分析报告。"""

        if rag_context:
            user_prompt += f"\n\n## 相关法律知识\n{rag_context}"

        try:
            result = await self._llm_service.chat(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.3,
                max_tokens=4096,
                model=self._model_override,
            )
            return {
                "content": result.get("content", ""),
                "tokens": result.get("tokens", {}),
            }
        except Exception as exc:
            logger.error("Answer synthesis failed: %s", exc)
            return {"content": "综合分析时出现错误，请稍后重试。", "tokens": {}}

    async def _stream_synthesis(
        self,
        query: str,
        decomposition: dict[str, Any],
        irac_results: list[dict[str, str]],
        verification: dict[str, Any],
        rag_context: str,
    ) -> AsyncGenerator[str, None]:
        """Stream the synthesis phase token by token."""
        system_prompt = """你是一位专业的法律分析专家。请基于以下多步推理结果，综合生成一份结构清晰、逻辑严谨的法律分析报告。

要求：使用Markdown格式，准确引用法条，全面覆盖所有子问题，提供可操作建议。结尾附免责声明。"""

        irac_text = "\n\n".join([
            f"子问题 {i+1}: {irac.get('issue', '')}\n规则: {irac.get('rule', '')}\n适用: {irac.get('application', '')}\n结论: {irac.get('conclusion', '')}"
            for i, irac in enumerate(irac_results)
        ])

        user_prompt = f"原始问题：{query}\n\n问题分解：{decomposition.get('analysis', '')}\n\nIRAC分析：\n{irac_text}\n\n验证意见：{chr(10).join(verification.get('notes', []))}"
        if rag_context:
            user_prompt += f"\n\n相关法律知识：\n{rag_context}"

        try:
            async for chunk in self._llm_service.chat_stream(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.3,
                max_tokens=4096,
            ):
                yield chunk
        except Exception as exc:
            logger.error("Streaming synthesis failed: %s", exc)
            yield "综合分析时出现错误，请稍后重试。"


# =============================================================================
# Deep Thinking Agent State (for LangGraph integration)
# =============================================================================

class DeepThinkingState(AgentState):
    """State for the deep thinking agent in LangGraph."""
    query: str = ""
    rag_context: str = ""
    use_reasoning_model: bool = True
    reasoning_result: dict[str, Any] = {}


class DeepThinkingLangGraphAgent(BaseAgent[DeepThinkingState]):
    """LangGraph-wrapped deep thinking agent for integration with supervisor."""

    def __init__(self) -> None:
        super().__init__(name="deep_thinking")
        self._thinking_agent = DeepThinkingAgent()

    def _build_graph(self) -> StateGraph:
        builder = StateGraph(DeepThinkingState)

        builder.add_node("decompose", self._decompose_node)
        builder.add_node("irac_analysis", self._irac_node)
        builder.add_node("verify", self._verify_node)
        builder.add_node("synthesize", self._synthesize_node)

        builder.set_entry_point("decompose")
        builder.add_edge("decompose", "irac_analysis")
        builder.add_edge("irac_analysis", "verify")
        builder.add_edge("verify", "synthesize")
        builder.add_edge("synthesize", END)

        return builder

    async def _decompose_node(self, state: DeepThinkingState) -> dict:
        result = await self._thinking_agent._decompose_problem(
            state.query, state.rag_context
        )
        return {"context": {"decomposition": result}}

    async def _irac_node(self, state: DeepThinkingState) -> dict:
        decomposition = state.context.get("decomposition", {})
        sub_issues = decomposition.get("sub_issues", [state.query])
        irac_results = []
        for sub_issue in sub_issues:
            irac = await self._thinking_agent._irac_analysis(
                state.query, sub_issue, state.rag_context
            )
            irac_results.append(irac)
        return {"context": {**state.context, "irac_results": irac_results}}

    async def _verify_node(self, state: DeepThinkingState) -> dict:
        decomposition = state.context.get("decomposition", {})
        irac_results = state.context.get("irac_results", [])
        verification = await self._thinking_agent._self_verify(
            state.query, decomposition, irac_results, state.rag_context
        )
        return {"context": {**state.context, "verification": verification}}

    async def _synthesize_node(self, state: DeepThinkingState) -> dict:
        decomposition = state.context.get("decomposition", {})
        irac_results = state.context.get("irac_results", [])
        verification = state.context.get("verification", {})
        result = await self._thinking_agent._synthesize_answer(
            state.query, decomposition, irac_results, verification, state.rag_context
        )
        content = result.get("content", "")
        return {
            "final_output": content,
            "reasoning_result": {
                "context": state.context,
                "final_answer": content,
            },
        }

    async def run(self, input_data: dict[str, Any]) -> dict[str, Any]:
        query = input_data.get("query", "")
        rag_context = input_data.get("rag_context", "")

        initial_state: dict[str, Any] = {
            "query": query,
            "rag_context": rag_context,
            "use_reasoning_model": True,
            "reasoning_result": {},
            "messages": [HumanMessage(content=query)],
            "context": {},
            "final_output": "",
        }

        graph = self.compile()
        result = await graph.ainvoke(initial_state)

        return {
            "final_output": result.get("final_output", ""),
            "reasoning_result": result.get("reasoning_result", {}),
        }


# =============================================================================
# Factory functions
# =============================================================================

def create_deep_thinking_agent(model_override: str | None = None) -> DeepThinkingAgent:
    """Create a deep thinking agent instance."""
    return DeepThinkingAgent(model_override=model_override)


def create_deep_thinking_langgraph_agent() -> DeepThinkingLangGraphAgent:
    """Create a LangGraph-wrapped deep thinking agent."""
    return DeepThinkingLangGraphAgent()
