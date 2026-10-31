"""Unified LLM service with multi-provider support and automatic fallback.

Supports DeepSeek (primary), OpenAI GPT-4, Qwen, and GLM with streaming
and non-streaming chat modes, token counting, and cost tracking.
"""

import asyncio
import hashlib
import json
import logging
import time
from typing import Any, AsyncGenerator

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

# =============================================================================
# Global HTTP connection pool for LLM API calls
# =============================================================================
# Reuses TCP connections across requests, avoiding TLS handshake overhead.
# max_connections=200 supports high concurrency; keepalive=50 reuses idle conns.

_http_client: httpx.AsyncClient | None = None


def _get_http_client() -> httpx.AsyncClient:
    """Return a singleton httpx.AsyncClient with connection pooling."""
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(120.0, connect=10.0),
            limits=httpx.Limits(
                max_connections=200,
                max_keepalive_connections=50,
                keepalive_expiry=30,
            ),
        )
    return _http_client


# =============================================================================
# LLM concurrency limiter
# =============================================================================
# Prevents overwhelming the LLM API with too many simultaneous requests.
# 20 concurrent LLM calls is a safe default for DeepSeek/OpenAI rate limits.

_llm_semaphore = asyncio.Semaphore(20)


def _format_exc(exc: Exception) -> str:
    """Include the exception type so empty-message errors stay diagnosable.

    httpx 连接层异常（如 SSL EOF、连接重置）的 str() 经常为空，
    只记 str(exc) 会让日志里出现 "Provider 'deepseek' failed: " 这种无法排查的行。
    """
    msg = str(exc).strip()
    return f"{type(exc).__name__}: {msg}" if msg else type(exc).__name__


def _is_transient(exc: Exception) -> bool:
    """连接层瞬时故障（网络抖动、TLS 重置、超时）值得重试一次。"""
    return isinstance(exc, (httpx.TransportError, httpx.TimeoutException))


# =============================================================================
# 成本 / 延迟治理工具
# =============================================================================
# 所有 LLM 调用都收敛到 ``LLMService.chat`` / ``chat_stream``，因此 token 预算
# 裁剪、精确缓存、成本账本与指标上报全部集中在这一层实现，调用方零改动。
_EXACT_CACHE_PREFIX = "legal:llm:exact:"
_COST_LEDGER_PREFIX = "legal:llm:cost:"
# 瞬时错误重试退避。原先固定 sleep 2s 会把 P95 白白拖长一倍；0.5s 足以覆盖
# TLS/连接抖动，且用户感知不到。
_RETRY_DELAYS = (0.5,)


def _metrics_module():
    """Lazy import：指标上报失败绝不能影响 LLM 主流程。"""
    try:
        from app.middleware import metrics
        return metrics
    except Exception:  # pragma: no cover - best effort
        return None


def _hash_messages(
    messages: list[dict[str, str]],
    temperature: float,
    max_tokens: int,
    model: str,
    provider: str,
) -> str:
    """精确缓存指纹：输入 + 全部采样参数。"""
    payload = json.dumps(
        {"m": messages, "t": temperature, "mt": max_tokens, "model": model, "provider": provider},
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.md5(payload.encode("utf-8")).hexdigest()


async def _cache_get(key: str) -> dict[str, Any] | None:
    try:
        from app.core.redis import get_redis
        raw = await get_redis().get(key)
        if not raw:
            return None
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except Exception as exc:
        logger.debug("exact cache read skipped: %s", _format_exc(exc))
        return None


async def _cache_set(key: str, value: dict[str, Any], ttl: int) -> None:
    try:
        from app.core.redis import get_redis
        await get_redis().set(key, json.dumps(value, ensure_ascii=False), ex=max(1, int(ttl)))
    except Exception as exc:
        logger.debug("exact cache write skipped: %s", _format_exc(exc))


async def _ledger_record(model: str, input_tokens: int, output_tokens: int, cost: float) -> None:
    """按天写 Redis 成本账本（进程重启不清零，供管理端查询）。"""
    if not settings.LLM_COST_LEDGER_ENABLED:
        return
    try:
        from app.core.redis import get_redis
        key = f"{_COST_LEDGER_PREFIX}{time.strftime('%Y-%m-%d')}"
        pipe = get_redis().pipeline()
        pipe.hincrbyfloat(key, f"cost:{model}", float(cost))
        pipe.hincrby(key, f"tokens:{model}", int(input_tokens + output_tokens))
        pipe.hincrby(key, "calls", 1)
        pipe.expire(key, 60 * 60 * 24 * 400)
        await pipe.execute()
    except Exception as exc:
        logger.debug("cost ledger write skipped: %s", _format_exc(exc))


def _record_llm_metrics(
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cost: float,
    latency: float | None = None,
    ttft: float | None = None,
) -> None:
    m = _metrics_module()
    if m is None:
        return
    try:
        m.LLM_API_CALLS.labels(provider=provider, model=model).inc()
        if input_tokens:
            m.LLM_INPUT_TOKENS.labels(provider=provider, model=model).inc(input_tokens)
        if output_tokens:
            m.LLM_OUTPUT_TOKENS.labels(provider=provider, model=model).inc(output_tokens)
        if cost:
            m.LLM_COST_USD.labels(provider=provider, model=model).inc(cost)
        if latency is not None:
            m.LLM_LATENCY.labels(provider=provider, model=model).observe(latency)
        if ttft is not None:
            m.LLM_TTFT.labels(provider=provider, model=model).observe(ttft)
    except Exception:  # pragma: no cover
        pass

# Approximate cost per 1K tokens (USD)
COST_PER_1K = {
    "deepseek-chat": {"input": 0.00014, "output": 0.00028},
    "gpt-4o": {"input": 0.0025, "output": 0.01},
    "gpt-4o-mini": {"input": 0.00015, "output": 0.0006},
    "qwen-turbo": {"input": 0.0003, "output": 0.0006},
    "glm-4": {"input": 0.0005, "output": 0.0005},
}

# Provider configuration
PROVIDER_CONFIG = {
    "deepseek": {
        "api_key_attr": "DEEPSEEK_API_KEY",
        "api_base_attr": "DEEPSEEK_API_BASE",
        "model_attr": "DEEPSEEK_MODEL",
        "default_model": "deepseek-chat",
    },
    "openai": {
        "api_key_attr": "OPENAI_API_KEY",
        "api_base_attr": "OPENAI_API_BASE",
        "model_attr": "OPENAI_MODEL",
        "default_model": "gpt-4o",
    },
    "qwen": {
        "api_key_attr": "QWEN_API_KEY",
        "api_base_attr": "QWEN_API_BASE",
        "model_attr": "QWEN_MODEL",
        "default_model": "qwen-turbo",
    },
    "glm": {
        "api_key_attr": "GLM_API_KEY",
        "api_base_attr": "GLM_API_BASE",
        "model_attr": "GLM_MODEL",
        "default_model": "glm-4",
    },
}


class LLMService:
    """Unified LLM service with multi-provider support and fallback.

    Provides a single interface for chat completion across multiple LLM
    providers with automatic fallback, streaming support, token counting,
    and cost tracking.

    Attributes:
        provider_order: Ordered list of provider names to try.
        _total_tokens: Cumulative token usage.
        _total_cost: Cumulative cost in USD.
        _request_count: Total number of requests made.
    """

    def __init__(
        self,
        provider_order: list[str] | None = None,
        default_system_prompt: str | None = None,
    ) -> None:
        """Initialize the LLM service.

        Args:
            provider_order: Ordered list of providers to try (e.g., ["deepseek", "openai"]).
                           Defaults to [settings.LLM_PROVIDER, "openai"].
            default_system_prompt: Default system prompt for all conversations.
        """
        self.provider_order = provider_order or [settings.LLM_PROVIDER, "openai"]
        self.default_system_prompt = default_system_prompt or (
            "你是一个专业的法律智能助手，致力于提供准确、可靠的法律信息和建议。"
            "请基于中国法律法规体系回答，必要时引用具体法条。"
        )
        self._total_tokens: int = 0
        self._total_cost: float = 0.0
        self._request_count: int = 0
        self._call_times: list[float] = []

    async def chat(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        model: str | None = None,
        provider: str | None = None,
        task: str | None = None,
        timeout: float | None = None,
        no_cache: bool = False,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Send a chat completion request with automatic provider fallback.

        Args:
            messages: List of message dicts with "role" and "content" keys.
            temperature: Sampling temperature (0-2).
            max_tokens: Maximum tokens in the response.
            model: Override the default model.
            provider: Override the provider order (single provider).
            task: "fast" 路由到 LLM_FAST_MODEL，"deep" 路由到 LLM_DEEP_MODEL。
            timeout: 单请求超时（秒），默认取 LLM_LATENCY_BUDGET_SECONDS。
            no_cache: 跳过精确匹配缓存（需要强实时/唯一结果时使用）。
            **kwargs: Additional API parameters.

        Returns:
            Dict with keys: content, model, provider, tokens, cost, finish_reason.

        Raises:
            RuntimeError: If all providers fail.
        """
        # ① 任务分档：低价值/格式化任务走更便宜的模型，降低单次成本
        if task and model is None:
            if task == "fast" and settings.LLM_FAST_MODEL:
                model = settings.LLM_FAST_MODEL
            elif task == "deep" and settings.LLM_DEEP_MODEL:
                model = settings.LLM_DEEP_MODEL

        # ② token 预算：system 提示始终保留，只丢弃较早的历史（成本大头）
        budgeted = self._apply_budget(self._enrich_messages(messages))

        # ③ 精确缓存：相同输入+参数在 TTL 内直接复用 → 0 token、0 延迟
        cache_key: str | None = None
        if settings.LLM_EXACT_CACHE_ENABLED and not no_cache:
            if self.count_message_tokens(budgeted) <= settings.LLM_EXACT_CACHE_MAX_INPUT_TOKENS:
                cache_key = _EXACT_CACHE_PREFIX + _hash_messages(
                    budgeted, temperature, max_tokens, model or "", provider or "",
                )
                cached = await _cache_get(cache_key)
                if cached is not None:
                    cached["cached"] = True
                    m = _metrics_module()
                    if m is not None:
                        try:
                            m.LLM_CACHE_HITS.labels(kind="exact").inc()
                        except Exception:
                            pass
                    return cached

        request_timeout = timeout or settings.LLM_LATENCY_BUDGET_SECONDS
        providers = [provider] if provider else self.provider_order
        errors: list[str] = []

        for provider_name in providers:
            for attempt in range(len(_RETRY_DELAYS) + 1):
                try:
                    result = await self._chat_provider(
                        provider_name=provider_name,
                        messages=budgeted,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        model=model,
                        timeout=request_timeout,
                        **kwargs,
                    )
                    self._request_count += 1
                    if cache_key:
                        await _cache_set(cache_key, result, settings.LLM_EXACT_CACHE_TTL_SECONDS)
                    return result
                except Exception as exc:
                    err_text = _format_exc(exc)
                    if _is_transient(exc) and attempt < len(_RETRY_DELAYS):
                        delay = _RETRY_DELAYS[attempt]
                        logger.warning(
                            "Provider '%s' failed (attempt %d): %s — retrying in %.1fs",
                            provider_name, attempt + 1, err_text, delay,
                        )
                        await asyncio.sleep(delay)
                        continue
                    logger.warning("Provider '%s' failed: %s", provider_name, err_text)
                    errors.append(f"{provider_name}: {err_text}")
                    break

        raise RuntimeError("All LLM providers failed. Errors: " + "; ".join(errors))

    async def chat_with_fallback(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        model: str | None = None,
        provider: str | None = None,
        fallback_content: str = "",
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Send a chat completion request with fallback to a default response.

        If all LLM providers fail, returns a fallback response instead of raising.
        """
        try:
            return await self.chat(
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                model=model,
                provider=provider,
                **kwargs,
            )
        except Exception as exc:
            logger.warning("All LLM providers failed, using fallback: %s", exc)
            if fallback_content:
                return {
                    "content": fallback_content,
                    "model": "fallback",
                    "provider": "fallback",
                    "tokens": {"input": 0, "output": 0, "total": 0},
                    "cost": 0.0,
                    "finish_reason": "stop",
                    "elapsed_seconds": 0.0,
                }
            # Generate a generic fallback based on the last user message
            last_msg = ""
            for msg in reversed(messages):
                if msg.get("role") == "user":
                    last_msg = msg.get("content", "")
                    break
            return {
                "content": f"您好！我是法律智能助手。由于AI服务暂时不可用，我无法详细回答您的问题。请稍后重试或联系管理员配置AI服务。您的问题：{last_msg[:100]}",
                "model": "fallback",
                "provider": "fallback",
                "tokens": {"input": 0, "output": 0, "total": 0},
                "cost": 0.0,
                "finish_reason": "stop",
                "elapsed_seconds": 0.0,
            }

    async def chat_stream(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        model: str | None = None,
        provider: str | None = None,
        timeout: float | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator[str, None]:
        """Stream a chat completion response.

        Args:
            messages: List of message dicts with "role" and "content" keys.
            temperature: Sampling temperature (0-2).
            max_tokens: Maximum tokens in the response.
            model: Override the default model.
            provider: Override the provider order (single provider).
            **kwargs: Additional API parameters.

        Yields:
            Text chunks from the streaming response.
        """
        budgeted = self._apply_budget(self._enrich_messages(messages))
        providers = [provider] if provider else self.provider_order
        errors: list[str] = []

        for provider_name in providers:
            for attempt in range(len(_RETRY_DELAYS) + 1):
                yielded_any = False
                try:
                    async for chunk in self._chat_stream_provider(
                        provider_name=provider_name,
                        messages=budgeted,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        model=model,
                        timeout=timeout,
                        **kwargs,
                    ):
                        yielded_any = True
                        yield chunk
                    self._request_count += 1
                    return
                except Exception as exc:
                    err_text = _format_exc(exc)
                    # 已经吐字后再重试会产生重复内容，因此只允许"未出字"重试
                    if _is_transient(exc) and not yielded_any and attempt < len(_RETRY_DELAYS):
                        delay = _RETRY_DELAYS[attempt]
                        logger.warning(
                            "Provider '%s' streaming failed (attempt %d): %s — retrying in %.1fs",
                            provider_name, attempt + 1, err_text, delay,
                        )
                        await asyncio.sleep(delay)
                        continue
                    logger.warning("Provider '%s' streaming failed: %s", provider_name, err_text)
                    errors.append(f"{provider_name}: {err_text}")
                    break

        raise RuntimeError(
            "All LLM providers failed for streaming. Errors: " + "; ".join(errors)
        )

    async def _chat_provider(
        self,
        provider_name: str,
        messages: list[dict[str, str]],
        temperature: float,
        max_tokens: int,
        model: str | None = None,
        timeout: float | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Send a non-streaming chat request to a specific provider."""
        config = PROVIDER_CONFIG.get(provider_name)
        if config is None:
            raise ValueError(f"Unknown provider: {provider_name}")

        api_key = getattr(settings, config["api_key_attr"], "")
        if not api_key:
            raise ValueError(f"No API key configured for provider: {provider_name}")

        api_base = getattr(settings, config["api_base_attr"], "https://api.openai.com/v1")
        model_name = model or getattr(settings, config["model_attr"], config["default_model"])

        # Ensure system prompt is present
        enriched_messages = self._enrich_messages(messages)

        client = _get_http_client()

        url = f"{api_base}/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model_name,
            "messages": enriched_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
            **kwargs,
        }

        start_time = time.time()
        async with _llm_semaphore:
            response = await client.post(
                url,
                headers=headers,
                json=payload,
                timeout=timeout or settings.LLM_LATENCY_BUDGET_SECONDS,
            )
            response.raise_for_status()
            data = response.json()

        elapsed = time.time() - start_time
        self._call_times.append(elapsed)

        choice = data["choices"][0]
        usage = data.get("usage", {})
        input_tokens = usage.get("prompt_tokens", 0)
        output_tokens = usage.get("completion_tokens", 0)
        total_tokens = usage.get("total_tokens", input_tokens + output_tokens)

        cost = self._calculate_cost(model_name, input_tokens, output_tokens)
        self._total_tokens += total_tokens
        self._total_cost += cost

        logger.info(
            "LLM call: provider=%s model=%s tokens=%d cost=$%.6f time=%.2fs",
            provider_name, model_name, total_tokens, cost, elapsed,
        )
        _record_llm_metrics(
            provider_name, model_name, input_tokens, output_tokens, cost, latency=elapsed,
        )
        await _ledger_record(model_name, input_tokens, output_tokens, cost)

        return {
            "content": choice["message"]["content"],
            "model": data.get("model", model_name),
            "provider": provider_name,
            "tokens": {
                "input": input_tokens,
                "output": output_tokens,
                "total": total_tokens,
            },
            "cost": cost,
            "finish_reason": choice.get("finish_reason", "stop"),
            "elapsed_seconds": elapsed,
            "cached": False,
        }

    async def _chat_stream_provider(
        self,
        provider_name: str,
        messages: list[dict[str, str]],
        temperature: float,
        max_tokens: int,
        model: str | None = None,
        timeout: float | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator[str, None]:
        """Stream a chat completion from a specific provider."""
        config = PROVIDER_CONFIG.get(provider_name)
        if config is None:
            raise ValueError(f"Unknown provider: {provider_name}")

        api_key = getattr(settings, config["api_key_attr"], "")
        if not api_key:
            raise ValueError(f"No API key configured for provider: {provider_name}")

        api_base = getattr(settings, config["api_base_attr"], "https://api.openai.com/v1")
        model_name = model or getattr(settings, config["model_attr"], config["default_model"])

        enriched_messages = self._enrich_messages(messages)

        client = _get_http_client()

        url = f"{api_base}/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": model_name,
            "messages": enriched_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
            **kwargs,
        }
        # 关键：不请求 usage 的话流式调用永远进不了成本账本（统计缺口）
        if settings.LLM_STREAM_INCLUDE_USAGE:
            payload["stream_options"] = {"include_usage": True}

        start_time = time.time()
        first_token_at: float | None = None
        usage: dict[str, Any] = {}

        async with _llm_semaphore:
            async with client.stream(
                "POST",
                url,
                headers=headers,
                json=payload,
                timeout=timeout or settings.LLM_LATENCY_BUDGET_SECONDS,
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data_str = line[6:]
                    if data_str.strip() == "[DONE]":
                        break
                    try:
                        data = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue
                    # 末尾 usage chunk 的 choices 为空数组，必须先取 usage 再判断
                    if data.get("usage"):
                        usage = data["usage"]
                    choices = data.get("choices") or []
                    if not choices:
                        continue
                    content = (choices[0].get("delta") or {}).get("content", "")
                    if content:
                        if first_token_at is None:
                            first_token_at = time.time()
                        yield content

        # 流结束：记账 + 上报指标（首 token 延迟单独打点，用于前端体验优化）
        if usage:
            input_tokens = usage.get("prompt_tokens", 0)
            output_tokens = usage.get("completion_tokens", 0)
            total_tokens = usage.get("total_tokens", input_tokens + output_tokens)
            cost = self._calculate_cost(model_name, input_tokens, output_tokens)
            elapsed = time.time() - start_time
            self._total_tokens += total_tokens
            self._total_cost += cost
            self._call_times.append(elapsed)
            logger.info(
                "LLM stream: provider=%s model=%s tokens=%d cost=$%.6f time=%.2fs",
                provider_name, model_name, total_tokens, cost, elapsed,
            )
            _record_llm_metrics(
                provider_name, model_name, input_tokens, output_tokens, cost,
                latency=elapsed,
                ttft=(first_token_at - start_time) if first_token_at else None,
            )
            await _ledger_record(model_name, input_tokens, output_tokens, cost)

    def _enrich_messages(
        self,
        messages: list[dict[str, str]],
    ) -> list[dict[str, str]]:
        """Ensure messages have a system prompt if not already present.

        Args:
            messages: The message list to enrich.

        Returns:
            Enriched message list with system prompt.
        """
        if messages and messages[0].get("role") == "system":
            return list(messages)

        return [{"role": "system", "content": self.default_system_prompt}] + list(messages)

    def count_tokens(self, text: str, model: str = "deepseek-chat") -> int:
        """Estimate the number of tokens in the given text.

        For Chinese text, roughly 1.5-2 characters per token.
        For English text, roughly 4 characters per token.

        Args:
            text: The text to count tokens for.
            model: The model name (affects tokenization rules).

        Returns:
            Estimated token count.
        """
        if not text:
            return 0

        # Simple heuristic: mixed Chinese/English estimation
        chinese_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
        other_chars = len(text) - chinese_chars

        # Chinese: ~1.5 chars per token
        # Other: ~4 chars per token
        tokens = int(chinese_chars / 1.5 + other_chars / 4.0)
        return max(tokens, 1)

    def count_message_tokens(
        self,
        messages: list[dict[str, str]],
        model: str = "deepseek-chat",
    ) -> int:
        """Estimate token count for a list of messages.

        Args:
            messages: List of message dicts.
            model: The model name.

        Returns:
            Estimated total token count.
        """
        total = 0
        for msg in messages:
            # Add overhead per message
            total += 4
            total += self.count_tokens(msg.get("content", ""), model)
        total += 2  # Reply priming
        return total

    def _apply_budget(self, messages: list[dict[str, str]]) -> list[dict[str, str]]:
        """按 ``LLM_MAX_INPUT_TOKENS`` 裁剪输入，控制单次调用成本。

        规则：system 提示永远保留（它定义了输出格式与合规边界），只丢弃
        较早的对话历史；若最后一条自身就超预算（例如整篇合同），按字符
        比例截断，避免整段内容把 prompt 撑爆。
        """
        budget = settings.LLM_MAX_INPUT_TOKENS
        if not budget or budget <= 0 or not messages:
            return list(messages)
        if self.count_message_tokens(messages) <= budget:
            return list(messages)

        has_system = messages[0].get("role") == "system"
        system = [messages[0]] if has_system else []
        rest = list(messages[1:] if has_system else messages)

        keep = max(1, settings.LLM_CONTEXT_KEEP_RECENT)
        tail = rest[-keep:] if len(rest) > keep else rest
        if not tail:
            return system

        base_cost = self.count_message_tokens(system) if system else 0
        while len(tail) > 1 and base_cost + self.count_message_tokens(tail) > budget:
            tail.pop(0)

        # 单条仍然超预算：按比例截断正文
        remaining = budget - base_cost - 6  # 每条消息约 4-6 token 结构开销
        last = dict(tail[-1])
        content = last.get("content", "") or ""
        content_tokens = self.count_tokens(content)
        if remaining > 0 and content_tokens > remaining:
            keep_chars = int(len(content) * remaining / content_tokens)
            last["content"] = content[: max(200, keep_chars)]
            tail[-1] = last

        return system + tail

    def get_cost(self, model: str, input_tokens: int, output_tokens: int) -> float:
        """Calculate the cost for a given token usage.

        Args:
            model: The model name.
            input_tokens: Number of input/prompt tokens.
            output_tokens: Number of output/completion tokens.

        Returns:
            Cost in USD.
        """
        return self._calculate_cost(model, input_tokens, output_tokens)

    def _calculate_cost(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
    ) -> float:
        """Calculate cost based on per-token pricing."""
        pricing = COST_PER_1K.get(model, {"input": 0.001, "output": 0.002})
        input_cost = (input_tokens / 1000.0) * pricing["input"]
        output_cost = (output_tokens / 1000.0) * pricing["output"]
        return round(input_cost + output_cost, 8)

    def get_usage_stats(self) -> dict[str, Any]:
        """Return cumulative usage statistics.

        Returns:
            Dict with total_tokens, total_cost, request_count, avg_response_time.
        """
        avg_time = (
            sum(self._call_times) / len(self._call_times)
            if self._call_times
            else 0.0
        )
        return {
            "total_tokens": self._total_tokens,
            "total_cost_usd": round(self._total_cost, 6),
            "request_count": self._request_count,
            "avg_response_time_seconds": round(avg_time, 3),
        }

    def reset_stats(self) -> None:
        """Reset all usage statistics to zero."""
        self._total_tokens = 0
        self._total_cost = 0.0
        self._request_count = 0
        self._call_times.clear()


# =============================================================================
# LangChain-compatible ChatLLM Service
# =============================================================================

class ChatLLMService:
    """LangChain-compatible wrapper around LLMService for use by agents.

    Provides a `get_llm()` method that returns a LangChain-compatible chat model
    with `.ainvoke()` support, bridging the agents to the underlying LLMService.
    """

    def __init__(self, llm_service: LLMService | None = None) -> None:
        """Initialize with an optional LLMService instance.

        Args:
            llm_service: The underlying LLMService. If None, a new one is created.
        """
        self._llm_service = llm_service or LLMService()

    def get_llm(self, temperature: float = 0.7, max_tokens: int = 4096, model: str | None = None) -> "_LangChainChatModel":
        """Return a LangChain-compatible chat model instance.

        Args:
            temperature: Sampling temperature.
            max_tokens: Maximum tokens in the response.
            model: Optional model override.

        Returns:
            A LangChain-compatible chat model with `.ainvoke()` support.
        """
        return _LangChainChatModel(
            llm_service=self._llm_service,
            temperature=temperature,
            max_tokens=max_tokens,
            model=model,
        )


class _LangChainChatModel:
    """Lightweight LangChain-compatible chat model wrapper.

    Provides `.ainvoke()` that accepts a list of LangChain BaseMessage objects
    and returns an AIMessage-compatible response.
    """

    def __init__(
        self,
        llm_service: LLMService,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        model: str | None = None,
    ) -> None:
        self._llm_service = llm_service
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._model = model

    async def ainvoke(self, messages: list, **kwargs) -> "_AIResponse":
        """Invoke the LLM with a list of messages (LangChain BaseMessage objects).

        Args:
            messages: List of LangChain message objects (HumanMessage, AIMessage, SystemMessage).
            **kwargs: Additional arguments (ignored).

        Returns:
            An AIMessage-compatible response object.
        """
        # Convert LangChain messages to plain dicts
        plain_messages = []
        for msg in messages:
            if hasattr(msg, "content") and hasattr(msg, "type"):
                # LangChain BaseMessage
                role = "system" if msg.type == "system" else ("assistant" if msg.type == "ai" else "user")
                plain_messages.append({"role": role, "content": msg.content})
            elif isinstance(msg, dict):
                plain_messages.append(msg)
            else:
                plain_messages.append({"role": "user", "content": str(msg)})

        try:
            result = await self._llm_service.chat(
                messages=plain_messages,
                temperature=self._temperature,
                max_tokens=self._max_tokens,
                model=self._model,
            )
            return _AIResponse(content=result.get("content", ""))
        except Exception as exc:
            # Fallback when no API key is configured or all providers fail
            import logging
            logging.getLogger(__name__).warning("LLM call failed, using fallback: %s", exc)
            # Generate a context-aware fallback
            last_user_msg = ""
            for msg in reversed(plain_messages):
                if msg.get("role") == "user":
                    last_user_msg = msg.get("content", "")
                    break
            fallback = (
                "您好！我是法律智能助手。由于AI模型服务暂时未配置或不可用，"
                "我无法为您提供详细的法律分析。请联系系统管理员配置API密钥后重试。\n\n"
                f"您输入的内容：{last_user_msg[:200]}"
            )
            return _AIResponse(content=fallback)


class _AIResponse:
    """Lightweight AIMessage-compatible response object."""

    def __init__(self, content: str) -> None:
        self.content = content
        self.type = "ai"

    def __str__(self) -> str:
        return self.content


# =============================================================================
# Singleton accessor
# =============================================================================

_llm_service_instance: LLMService | None = None
_chat_llm_service_instance: ChatLLMService | None = None


def get_llm_service() -> ChatLLMService:
    """Return a singleton ChatLLMService instance for use by agents."""
    global _chat_llm_service_instance
    if _chat_llm_service_instance is None:
        _chat_llm_service_instance = ChatLLMService()
    return _chat_llm_service_instance


def get_raw_llm_service() -> LLMService:
    """Return a singleton LLMService instance for direct API calls."""
    global _llm_service_instance
    if _llm_service_instance is None:
        _llm_service_instance = LLMService()
    return _llm_service_instance