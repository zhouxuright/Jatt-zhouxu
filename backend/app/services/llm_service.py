"""Unified LLM service with multi-provider support and automatic fallback.

Supports DeepSeek (primary), OpenAI GPT-4, Qwen, and GLM with streaming
and non-streaming chat modes, token counting, and cost tracking.
"""

import asyncio
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
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Send a chat completion request with automatic provider fallback.

        Args:
            messages: List of message dicts with "role" and "content" keys.
            temperature: Sampling temperature (0-2).
            max_tokens: Maximum tokens in the response.
            model: Override the default model.
            provider: Override the provider order (single provider).
            **kwargs: Additional API parameters.

        Returns:
            Dict with keys: content, model, provider, tokens, cost, finish_reason.

        Raises:
            RuntimeError: If all providers fail.
        """
        providers = [provider] if provider else self.provider_order
        last_error: Exception | None = None

        for provider_name in providers:
            try:
                result = await self._chat_provider(
                    provider_name=provider_name,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    model=model,
                    **kwargs,
                )
                self._request_count += 1
                return result
            except Exception as exc:
                logger.warning("Provider '%s' failed: %s", provider_name, exc)
                last_error = exc
                continue

        raise RuntimeError(f"All LLM providers failed. Last error: {last_error}")

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
        providers = [provider] if provider else self.provider_order
        last_error: Exception | None = None

        for provider_name in providers:
            try:
                async for chunk in self._chat_stream_provider(
                    provider_name=provider_name,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    model=model,
                    **kwargs,
                ):
                    yield chunk
                self._request_count += 1
                return
            except Exception as exc:
                logger.warning("Provider '%s' streaming failed: %s", provider_name, exc)
                last_error = exc
                continue

        raise RuntimeError(f"All LLM providers failed for streaming. Last error: {last_error}")

    async def _chat_provider(
        self,
        provider_name: str,
        messages: list[dict[str, str]],
        temperature: float,
        max_tokens: int,
        model: str | None = None,
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
            response = await client.post(url, headers=headers, json=payload)
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
        }

    async def _chat_stream_provider(
        self,
        provider_name: str,
        messages: list[dict[str, str]],
        temperature: float,
        max_tokens: int,
        model: str | None = None,
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
        payload = {
            "model": model_name,
            "messages": enriched_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
            **kwargs,
        }

        async with _llm_semaphore:
            async with client.stream("POST", url, headers=headers, json=payload) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        data_str = line[6:]
                        if data_str.strip() == "[DONE]":
                            break
                        try:
                            data = json.loads(data_str)
                            delta = data["choices"][0].get("delta", {})
                            content = delta.get("content", "")
                            if content:
                                yield content
                        except json.JSONDecodeError:
                            continue

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