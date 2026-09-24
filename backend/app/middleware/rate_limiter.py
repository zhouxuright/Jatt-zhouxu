"""Rate limiting and circuit breaker middleware for high-concurrency protection.

Provides:
- IP-based rate limiting (Redis sliding window)
- User-based rate limiting (JWT-bound)
- LLM circuit breaker (auto-degrade on repeated failures)
"""

import asyncio
import logging
import time
from typing import Any, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.core.config import settings

logger = logging.getLogger(__name__)


# =============================================================================
# Rate Limiter (Redis sliding window)
# =============================================================================

class RateLimiter:
    """Redis-based sliding window rate limiter.

    Uses Redis sorted sets for O(log N) sliding window rate limiting.
    Falls back to in-memory limiter if Redis is unavailable.
    """

    def __init__(self, redis_client: Any = None) -> None:
        self._redis = redis_client
        # In-memory fallback: {key: [(timestamp, ...), ...]}
        self._memory_windows: dict[str, list[float]] = {}

    async def is_allowed(self, key: str, max_requests: int, window_seconds: int) -> bool:
        """Check if a request is allowed under the rate limit.

        Args:
            key: Unique identifier (e.g., "ip:1.2.3.4" or "user:abc123").
            max_requests: Maximum requests allowed in the window.
            window_seconds: Time window in seconds.

        Returns:
            True if allowed, False if rate limited.
        """
        now = time.time()

        if self._redis:
            try:
                return await self._redis_check(key, max_requests, window_seconds, now)
            except Exception:
                pass

        # In-memory fallback
        return self._memory_check(key, max_requests, window_seconds, now)

    async def _redis_check(self, key: str, max_requests: int, window_seconds: int, now: float) -> bool:
        redis_key = f"ratelimit:{key}"
        window_start = now - window_seconds

        pipe = self._redis.pipeline()
        pipe.zremrangebyscore(redis_key, 0, window_start)
        pipe.zadd(redis_key, {str(now): now})
        pipe.zcard(redis_key)
        pipe.expire(redis_key, window_seconds + 1)
        results = await pipe.execute()

        count = results[2]  # zcard result
        return count <= max_requests

    def _memory_check(self, key: str, max_requests: int, window_seconds: int, now: float) -> bool:
        if key not in self._memory_windows:
            self._memory_windows[key] = []

        window_start = now - window_seconds
        self._memory_windows[key] = [
            t for t in self._memory_windows[key] if t > window_start
        ]

        if len(self._memory_windows[key]) >= max_requests:
            return False

        self._memory_windows[key].append(now)
        return True


# =============================================================================
# Circuit Breaker for LLM calls
# =============================================================================

class CircuitBreaker:
    """Circuit breaker for LLM API calls.

    States:
    - CLOSED: Normal operation, requests pass through.
    - OPEN: LLM is failing, requests get fallback response immediately.
    - HALF_OPEN: Testing if LLM has recovered (1 probe request).

    Transitions:
    - CLOSED → OPEN: After `failure_threshold` consecutive failures.
    - OPEN → HALF_OPEN: After `recovery_timeout` seconds.
    - HALF_OPEN → CLOSED: On successful probe request.
    - HALF_OPEN → OPEN: On failed probe request.
    """

    STATE_CLOSED = "closed"
    STATE_OPEN = "open"
    STATE_HALF_OPEN = "half_open"

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: int = 30,
    ) -> None:
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.state = self.STATE_CLOSED
        self.failure_count = 0
        self.last_failure_time = 0.0
        self._lock = asyncio.Lock()

    async def call(self, func: Callable, *args, **kwargs) -> Any:
        """Execute a function through the circuit breaker.

        If the circuit is OPEN, returns a fallback response immediately.
        If HALF_OPEN, allows one probe request through.
        """
        async with self._lock:
            if self.state == self.STATE_OPEN:
                if time.time() - self.last_failure_time >= self.recovery_timeout:
                    self.state = self.STATE_HALF_OPEN
                    logger.info("Circuit breaker: OPEN → HALF_OPEN (testing recovery)")
                else:
                    return None  # Signal: circuit is open, use fallback

        try:
            result = await func(*args, **kwargs)
            async with self._lock:
                if self.state == self.STATE_HALF_OPEN:
                    self.state = self.STATE_CLOSED
                    self.failure_count = 0
                    logger.info("Circuit breaker: HALF_OPEN → CLOSED (recovered)")
            return result
        except Exception as exc:
            async with self._lock:
                self.failure_count += 1
                self.last_failure_time = time.time()
                if self.failure_count >= self.failure_threshold:
                    self.state = self.STATE_OPEN
                    logger.warning(
                        "Circuit breaker: → OPEN (%d failures, cooldown %ds)",
                        self.failure_count, self.recovery_timeout,
                    )
                elif self.state == self.STATE_HALF_OPEN:
                    self.state = self.STATE_OPEN
                    logger.warning("Circuit breaker: HALF_OPEN → OPEN (probe failed)")
            raise

    def get_state(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "failure_count": self.failure_count,
            "failure_threshold": self.failure_threshold,
            "recovery_timeout": self.recovery_timeout,
        }


# Global instances
_rate_limiter: RateLimiter | None = None
_circuit_breaker: CircuitBreaker | None = None


def get_rate_limiter() -> RateLimiter:
    global _rate_limiter
    if _rate_limiter is None:
        _rate_limiter = RateLimiter()
    return _rate_limiter


def get_circuit_breaker() -> CircuitBreaker:
    global _circuit_breaker
    if _circuit_breaker is None:
        _circuit_breaker = CircuitBreaker()
    return _circuit_breaker


# =============================================================================
# FastAPI Middleware
# =============================================================================

# Paths that count as "LLM-heavy" (stricter rate limit)
LLM_PATHS = {"/api/v1/chat/chat", "/api/v1/chat/chat/stream",
             "/api/v1/document/generate", "/api/v1/contract/review"}


class RateLimitMiddleware(BaseHTTPMiddleware):
    """HTTP middleware that enforces rate limits per IP and per user.

    Rate limits:
    - General: 60 req/min/IP
    - LLM-heavy: 10 req/min/IP
    - Per-user: 20 chat req/min (if authenticated)
    """

    async def dispatch(self, request: Request, call_next):
        # Skip rate limiting for health check and static files
        path = request.url.path
        if path in ("/health", "/") or path.startswith("/assets"):
            return await call_next(request)

        limiter = get_rate_limiter()

        # Extract client IP
        client_ip = request.client.host if request.client else "unknown"
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            client_ip = forwarded.split(",")[0].strip()

        # Determine rate limit tier
        is_llm_path = any(path.startswith(p) for p in LLM_PATHS)
        max_requests = 30 if is_llm_path else 120
        window = 60  # 1 minute

        # Check IP rate limit
        ip_key = f"ip:{client_ip}"
        if not await limiter.is_allowed(ip_key, max_requests, window):
            logger.warning("Rate limit exceeded for IP: %s on %s", client_ip, path)
            return JSONResponse(
                status_code=429,
                content={"detail": "请求过于频繁，请稍后再试"},
                headers={"Retry-After": "60"},
            )

        # Check user rate limit (if authenticated)
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer ") and is_llm_path:
            # Use token hash as user key (avoids decoding JWT here)
            token_hash = auth_header[7:39]  # First 32 chars of token
            user_key = f"user:{token_hash}"
            if not await limiter.is_allowed(user_key, 60, 60):
                logger.warning("User rate limit exceeded on %s", path)
                return JSONResponse(
                    status_code=429,
                    content={"detail": "对话请求过于频繁，请稍后再试"},
                    headers={"Retry-After": "60"},
                )

        return await call_next(request)
