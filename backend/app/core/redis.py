"""Shared Redis client — single connection pool for the entire application.

All services (semantic cache, task manager, batch processor, rate limiter, etc.)
should use ``get_redis()`` instead of creating their own ``redis.asyncio.from_url()``
connections. This avoids creating 5+ separate TCP connection pools to the same
Redis server, reducing file descriptor usage and memory overhead.
"""

import logging
from typing import Any

import redis.asyncio as aioredis

from app.core.config import settings

logger = logging.getLogger(__name__)

_redis_client: aioredis.Redis | None = None


def get_redis() -> aioredis.Redis:
    """Return the shared Redis client (lazy singleton).

    Creates a single connection pool on first call. All subsequent calls
    return the same client. Call ``close_redis()`` at shutdown to release.
    """
    global _redis_client
    if _redis_client is None:
        _redis_client = aioredis.from_url(
            settings.REDIS_URL,
            max_connections=settings.REDIS_MAX_CONNECTIONS,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5,
            retry_on_timeout=True,
        )
        logger.info("Shared Redis client created (max_connections=%d)", settings.REDIS_MAX_CONNECTIONS)
    return _redis_client


async def close_redis() -> None:
    """Close the shared Redis connection pool. Call at application shutdown."""
    global _redis_client
    if _redis_client is not None:
        await _redis_client.close()
        _redis_client = None
        logger.info("Shared Redis client closed")


async def check_redis_connection() -> bool:
    """Verify Redis connectivity."""
    try:
        client = get_redis()
        return await client.ping()
    except Exception:
        return False
