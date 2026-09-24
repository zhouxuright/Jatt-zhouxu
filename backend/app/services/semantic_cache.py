"""Redis-based semantic cache for LLM responses.

Caches responses by semantic similarity rather than exact string match,
reducing LLM API calls for semantically equivalent queries.

Key optimizations for high concurrency:
- Uses Redis Hash instead of KEYS * (O(1) vs O(N))
- Batch cosine similarity via numpy (vectorized)
- Configurable TTL and similarity threshold
"""

import hashlib
import json
import logging
import time
from typing import Any

import numpy as np

from app.core.config import settings

logger = logging.getLogger(__name__)

# Redis key for the cache index (set of all cache entry keys)
CACHE_INDEX_KEY = "legal:semantic_cache:index"
CACHE_ENTRY_PREFIX = "legal:sc:"


class SemanticCache:
    """Redis-backed semantic cache for LLM responses.

    Uses a Redis Hash to store cache entries and a Redis Set to track
    all cache keys. This avoids the O(N) KEYS * scan on every lookup.

    Attributes:
        similarity_threshold: Minimum cosine similarity for a cache hit.
        default_ttl: Default TTL in seconds for cached entries.
    """

    def __init__(
        self,
        similarity_threshold: float = 0.92,
        default_ttl: int = 3600,
    ) -> None:
        self.similarity_threshold = similarity_threshold
        self.default_ttl = default_ttl
        self._redis: Any = None
        self._stats: dict[str, int] = {"hits": 0, "misses": 0, "sets": 0}

    def _get_redis(self) -> Any:
        """Get or create async Redis client."""
        if self._redis is not None:
            return self._redis
        try:
            import redis.asyncio as redis_asyncio
            self._redis = redis_asyncio.from_url(
                settings.REDIS_URL,
                max_connections=settings.REDIS_MAX_CONNECTIONS,
                decode_responses=True,
            )
            logger.info("Semantic cache Redis connected: %s", settings.REDIS_URL)
            return self._redis
        except Exception as exc:
            logger.warning("Redis connection failed, semantic cache disabled: %s", exc)
            return None

    async def get(self, query: str, query_embedding: list[float] | None = None) -> dict[str, Any] | None:
        """Look up a cached response for a semantically similar query.

        Uses Redis SMEMBERS + HMGET instead of KEYS *.
        Cosine similarity is computed via numpy (vectorized).

        Args:
            query: The user query (used for hash key generation).
            query_embedding: Pre-computed query embedding. If None, computes it.

        Returns:
            Cached response dict or None.
        """
        redis_client = self._get_redis()
        if redis_client is None:
            return None

        try:
            # Get all cache entry keys from the index set (O(1) with small sets)
            keys = await redis_client.smembers(CACHE_INDEX_KEY)
            if not keys:
                self._stats["misses"] += 1
                return None

            # Batch fetch all entries in one pipeline (O(K) where K = cache size)
            pipe = redis_client.pipeline()
            for key in keys:
                pipe.hgetall(key)
            entries = await pipe.execute()

            # Compute cosine similarity via numpy (vectorized)
            if query_embedding is None:
                from app.services.model_registry import ModelRegistry
                query_embedding = await ModelRegistry.embed_query(query)

            query_vec = np.array(query_embedding, dtype=np.float32)

            best_match = None
            best_sim = 0.0

            for key, entry_data in zip(keys, entries):
                if not entry_data:
                    continue
                try:
                    cached_emb = json.loads(entry_data.get("embedding", "[]"))
                    if not cached_emb:
                        continue

                    cached_vec = np.array(cached_emb, dtype=np.float32)
                    similarity = float(np.dot(query_vec, cached_vec) / (
                        np.linalg.norm(query_vec) * np.linalg.norm(cached_vec) + 1e-8
                    ))

                    if similarity >= self.similarity_threshold and similarity > best_sim:
                        best_sim = similarity
                        best_match = {
                            "content": entry_data.get("content", ""),
                            "similarity": round(similarity, 4),
                            "original_query": entry_data.get("query", ""),
                            "cached_at": entry_data.get("cached_at", ""),
                        }
                except Exception:
                    continue

            if best_match:
                self._stats["hits"] += 1
                return best_match

            self._stats["misses"] += 1
            return None

        except Exception as exc:
            logger.warning("Semantic cache lookup failed: %s", exc)
            self._stats["misses"] += 1
            return None

    async def set(
        self,
        query: str,
        content: str,
        query_embedding: list[float] | None = None,
        ttl: int | None = None,
    ) -> bool:
        """Cache a query-response pair.

        Args:
            query: The original user query.
            content: The LLM response content to cache.
            query_embedding: Pre-computed embedding. If None, computes it.
            ttl: TTL in seconds (defaults to self.default_ttl).

        Returns:
            True if cached successfully.
        """
        redis_client = self._get_redis()
        if redis_client is None:
            return False

        try:
            if query_embedding is None:
                from app.services.model_registry import ModelRegistry
                query_embedding = await ModelRegistry.embed_query(query)

            query_hash = hashlib.md5(query.encode("utf-8")).hexdigest()
            cache_key = f"{CACHE_ENTRY_PREFIX}{query_hash}"
            ttl = ttl or self.default_ttl

            entry = {
                "query": query,
                "content": content,
                "embedding": json.dumps(query_embedding),
                "cached_at": str(time.time()),
            }

            pipe = redis_client.pipeline()
            pipe.hset(cache_key, mapping=entry)
            pipe.expire(cache_key, ttl)
            pipe.sadd(CACHE_INDEX_KEY, cache_key)
            await pipe.execute()

            self._stats["sets"] += 1
            logger.debug("Cached response for: '%s' (TTL=%ds)", query[:50], ttl)
            return True

        except Exception as exc:
            logger.warning("Semantic cache set failed: %s", exc)
            return False

    async def get_stats(self) -> dict[str, Any]:
        """Return cache statistics."""
        total = self._stats["hits"] + self._stats["misses"]
        hit_rate = (self._stats["hits"] / total * 100) if total > 0 else 0.0

        redis_client = self._get_redis()
        total_entries = 0
        if redis_client:
            try:
                total_entries = await redis_client.scard(CACHE_INDEX_KEY)
            except Exception:
                pass

        return {
            "hits": self._stats["hits"],
            "misses": self._stats["misses"],
            "sets": self._stats["sets"],
            "hit_rate": round(hit_rate, 1),
            "total_entries": total_entries,
            "similarity_threshold": self.similarity_threshold,
            "default_ttl": self.default_ttl,
        }


# Singleton
_semantic_cache: SemanticCache | None = None


def get_semantic_cache() -> SemanticCache:
    global _semantic_cache
    if _semantic_cache is None:
        _semantic_cache = SemanticCache()
    return _semantic_cache
