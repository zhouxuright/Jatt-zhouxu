"""
OpenTelemetry集成 — 分布式追踪与LLM可观测性

为法律AI系统的多Agent工作流提供完整的追踪能力：
1. Agent执行追踪（每个Agent的输入/输出/耗时）
2. RAG管道追踪（检索→重排序→生成 全链路）
3. LLM调用追踪（token消耗/延迟/成本）
4. 数据库查询追踪
5. 外部工具调用追踪

使用方法:
    from app.services.observability import trace_agent_execution

    @trace_agent_execution("LegalConsultAgent")
    async def run(self, query: str):
        ...
"""

from __future__ import annotations

import functools
import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Generator, Optional

logger = logging.getLogger(__name__)


# ============================================================================
# Trace Context
# ============================================================================

@dataclass
class SpanData:
    """单个追踪跨度"""
    name: str
    trace_id: str = ""
    span_id: str = ""
    parent_span_id: str = ""
    start_time: float = 0.0
    end_time: float = 0.0
    status: str = "ok"  # ok / error
    attributes: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)

    @property
    def duration_ms(self) -> float:
        return (self.end_time - self.start_time) * 1000 if self.end_time else 0


@dataclass
class TraceStats:
    """追踪统计"""
    total_spans: int = 0
    total_traces: int = 0
    agent_executions: dict[str, int] = field(default_factory=dict)
    llm_calls: dict[str, int] = field(default_factory=dict)
    avg_latency_ms: dict[str, float] = field(default_factory=dict)
    error_count: int = 0
    total_tokens_used: int = 0
    total_llm_cost_usd: float = 0.0


# ============================================================================
# Observability Manager
# ============================================================================

class ObservabilityManager:
    """
    可观测性管理器 — 轻量级追踪实现。

    生产环境应接入 OpenTelemetry + Jaeger/Zipkin。
    此实现提供本地追踪 + 统计，不依赖外部服务。
    """

    def __init__(self, enabled: bool = True):
        self._enabled = enabled
        self._stats = TraceStats()
        self._recent_spans: list[SpanData] = []
        self._max_recent = 1000

    # ------------------------------------------------------------------
    # Span management
    # ------------------------------------------------------------------

    @contextmanager
    def span(self, name: str, **attributes) -> Generator[SpanData, None, None]:
        """创建一个追踪跨度"""
        if not self._enabled:
            yield SpanData(name=name)
            return

        import uuid
        span = SpanData(
            name=name,
            trace_id=uuid.uuid4().hex[:16],
            span_id=uuid.uuid4().hex[:8],
            start_time=time.time(),
            attributes=attributes,
        )

        try:
            yield span
            span.status = "ok"
        except Exception as e:
            span.status = "error"
            span.events.append({
                "name": "exception",
                "message": str(e),
                "timestamp": time.time(),
            })
            raise
        finally:
            span.end_time = time.time()
            self._record_span(span)

    def _record_span(self, span: SpanData):
        """记录完成的跨度"""
        self._stats.total_spans += 1
        self._recent_spans.append(span)
        if len(self._recent_spans) > self._max_recent:
            self._recent_spans = self._recent_spans[-self._max_recent:]

        # Update stats
        name = span.name
        if name.startswith("agent:"):
            agent_name = name.split(":")[1]
            self._stats.agent_executions[agent_name] = \
                self._stats.agent_executions.get(agent_name, 0) + 1
        elif name.startswith("llm:"):
            model = name.split(":")[1]
            self._stats.llm_calls[model] = \
                self._stats.llm_calls.get(model, 0) + 1
            tokens = span.attributes.get("tokens_used", 0)
            self._stats.total_tokens_used += tokens

        if span.status == "error":
            self._stats.error_count += 1

    # ------------------------------------------------------------------
    # LLM call tracking
    # ------------------------------------------------------------------

    @contextmanager
    def track_llm_call(self, model: str, provider: str = "", **kwargs):
        """追踪LLM调用"""
        with self.span(f"llm:{model}", provider=provider, **kwargs) as span:
            yield span

    # ------------------------------------------------------------------
    # Agent execution tracking
    # ------------------------------------------------------------------

    @contextmanager
    def track_agent(self, agent_name: str, **kwargs):
        """追踪Agent执行"""
        with self.span(f"agent:{agent_name}", **kwargs) as span:
            yield span

    # ------------------------------------------------------------------
    # RAG pipeline tracking
    # ------------------------------------------------------------------

    @contextmanager
    def track_rag_pipeline(self, query: str, **kwargs):
        """追踪RAG管道"""
        with self.span("rag:pipeline", query=query[:100], **kwargs) as span:
            yield span

    # ------------------------------------------------------------------
    # Stats & Reporting
    # ------------------------------------------------------------------

    def get_stats(self) -> dict[str, Any]:
        """获取追踪统计"""
        return {
            "total_spans": self._stats.total_spans,
            "agent_executions": self._stats.agent_executions,
            "llm_calls": self._stats.llm_calls,
            "total_tokens_used": self._stats.total_tokens_used,
            "error_count": self._stats.error_count,
            "recent_spans_count": len(self._recent_spans),
        }

    def get_recent_spans(self, limit: int = 20) -> list[dict]:
        """获取最近的追踪跨度"""
        return [
            {
                "name": s.name,
                "trace_id": s.trace_id,
                "duration_ms": round(s.duration_ms, 2),
                "status": s.status,
                "attributes": s.attributes,
            }
            for s in self._recent_spans[-limit:]
        ][::-1]

    def get_agent_stats(self) -> dict[str, Any]:
        """获取Agent执行统计"""
        agent_durations: dict[str, list[float]] = {}
        for span in self._recent_spans:
            if span.name.startswith("agent:"):
                agent = span.name.split(":")[1]
                if agent not in agent_durations:
                    agent_durations[agent] = []
                agent_durations[agent].append(span.duration_ms)

        result = {}
        for agent, durations in agent_durations.items():
            result[agent] = {
                "count": len(durations),
                "avg_ms": round(sum(durations) / len(durations), 2) if durations else 0,
                "max_ms": round(max(durations), 2) if durations else 0,
                "min_ms": round(min(durations), 2) if durations else 0,
                "error_count": sum(1 for s in self._recent_spans
                                   if s.name == f"agent:{agent}" and s.status == "error"),
            }
        return result


# ============================================================================
# Decorator for easy tracing
# ============================================================================

def trace_execution(name: str):
    """装饰器: 追踪函数执行"""
    def decorator(func):
        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            mgr = get_observability_manager()
            with mgr.span(name, function=func.__name__):
                return await func(*args, **kwargs)

        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            mgr = get_observability_manager()
            with mgr.span(name, function=func.__name__):
                return func(*args, **kwargs)

        import asyncio
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper
    return decorator


# ============================================================================
# Singleton
# ============================================================================

_observability_manager: ObservabilityManager | None = None


def get_observability_manager() -> ObservabilityManager:
    global _observability_manager
    if _observability_manager is None:
        _observability_manager = ObservabilityManager()
    return _observability_manager
