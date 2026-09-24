"""Embedding service providing a unified interface for text embedding generation.

Supports both local sentence-transformers models and API-based (OpenAI-compatible)
embedding providers. Includes LRU caching for frequently embedded texts.
"""

import asyncio
import hashlib
import logging
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import numpy as np
from app.core.config import settings

logger = logging.getLogger(__name__)


class EmbeddingService:
    """Unified embedding generation service with caching and batch support.

    Supports two backends:
    - Local: Uses sentence-transformers (e.g., text2vec-base-chinese for Chinese legal text)
    - API: Uses OpenAI-compatible embedding API endpoints

    Attributes:
        model_name: Name of the embedding model to use.
        backend: Either "local" or "api".
        dimension: Output vector dimension.
        _model: Lazily loaded local model instance.
        _cache: LRU cache for frequently embedded texts.
    """

    # Recommended Chinese-optimized embedding model for legal text
    DEFAULT_MODEL = "shibing624/text2vec-base-chinese"

    def __init__(
        self,
        model_name: str | None = None,
        backend: str = "local",
        cache_size: int = 10000,
        api_key: str | None = None,
        api_base: str | None = None,
    ) -> None:
        """Initialize the embedding service.

        Args:
            model_name: Model name/path. Defaults to text2vec-base-chinese for Chinese.
            backend: "local" for sentence-transformers or "api" for remote API.
            cache_size: Maximum number of cached embeddings.
            api_key: API key (required for API backend).
            api_base: API base URL (required for API backend).
        """
        self.model_name = model_name or self.DEFAULT_MODEL
        self.backend = backend
        self.cache_size = cache_size
        self.api_key = api_key or settings.OPENAI_API_KEY
        self.api_base = api_base or settings.OPENAI_API_BASE

        self._model: Any = None
        self._dimension: int | None = None
        self._cache: OrderedDict[str, np.ndarray] = OrderedDict()
        self._cache_hits: int = 0
        self._cache_misses: int = 0
        self._executor = ThreadPoolExecutor(max_workers=2)

    @property
    def dimension(self) -> int:
        """Return the embedding vector dimension."""
        if self._dimension is None:
            # Default dimension for text2vec-base-chinese
            self._dimension = 768
        return self._dimension

    def _get_cache_key(self, text: str) -> str:
        """Generate a deterministic cache key for the given text."""
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def _load_local_model(self) -> Any:
        """Lazily load the local sentence-transformers model."""
        if self._model is not None:
            return self._model

        try:
            from sentence_transformers import SentenceTransformer

            logger.info("Loading embedding model: %s (device=%s)", self.model_name, settings.EMBEDDING_DEVICE)
            self._model = SentenceTransformer(
                self.model_name,
                device=settings.EMBEDDING_DEVICE,
            )
            # Update dimension from loaded model
            sample_embedding = self._model.encode("test", convert_to_numpy=True)
            self._dimension = int(sample_embedding.shape[0])
            logger.info("Embedding model loaded successfully, dimension=%d", self._dimension)
            return self._model
        except ImportError:
            logger.error("sentence-transformers not installed. Run: pip install sentence-transformers")
            raise
        except Exception as exc:
            logger.error("Failed to load embedding model '%s': %s", self.model_name, exc)
            raise

    async def embed_text(self, text: str) -> list[float]:
        """Generate embedding for a single text.

        Args:
            text: The input text to embed.

        Returns:
            A list of floats representing the embedding vector.
        """
        cache_key = self._get_cache_key(text)

        # Check cache first
        if cache_key in self._cache:
            self._cache_hits += 1
            # Move to end (most recently used)
            self._cache.move_to_end(cache_key)
            return self._cache[cache_key].tolist()

        self._cache_misses += 1

        if self.backend == "local":
            embedding = await self._embed_local([text])
        elif self.backend == "api":
            embedding = await self._embed_api([text])
        else:
            raise ValueError(f"Unsupported embedding backend: {self.backend}")

        result = embedding[0].tolist() if isinstance(embedding[0], np.ndarray) else list(embedding[0])

        # Cache the result
        self._add_to_cache(cache_key, np.array(result))
        return result

    async def embed_texts(self, texts: list[str], batch_size: int = 32) -> list[list[float]]:
        """Generate embeddings for a batch of texts.

        Args:
            texts: List of input texts.
            batch_size: Number of texts to embed per batch.

        Returns:
            List of embedding vectors, each as a list of floats.
        """
        if not texts:
            return []

        # Separate cached and uncached texts
        results: list[list[float] | None] = [None] * len(texts)
        uncached_indices: list[int] = []
        uncached_texts: list[str] = []

        for i, text in enumerate(texts):
            cache_key = self._get_cache_key(text)
            if cache_key in self._cache:
                self._cache_hits += 1
                self._cache.move_to_end(cache_key)
                results[i] = self._cache[cache_key].tolist()
            else:
                self._cache_misses += 1
                uncached_indices.append(i)
                uncached_texts.append(text)

        if not uncached_texts:
            return [r for r in results if r is not None]  # type: ignore[return-value]

        # Embed uncached texts in batches
        all_embeddings: list[np.ndarray] = []
        for i in range(0, len(uncached_texts), batch_size):
            batch = uncached_texts[i : i + batch_size]
            if self.backend == "local":
                embeddings = await self._embed_local(batch)
            elif self.backend == "api":
                embeddings = await self._embed_api(batch)
            else:
                raise ValueError(f"Unsupported embedding backend: {self.backend}")
            all_embeddings.extend(embeddings)

        # Assign results and cache
        for idx, text, emb in zip(uncached_indices, uncached_texts, all_embeddings):
            results[idx] = emb.tolist()
            cache_key = self._get_cache_key(text)
            self._add_to_cache(cache_key, emb)

        return [r for r in results if r is not None]  # type: ignore[return-value]

    async def embed_query(self, query: str) -> list[float]:
        """Generate embedding for a search query.

        This is a convenience alias for embed_text, but may apply query-specific
        preprocessing in the future (e.g., instruction prefix for some models).

        Args:
            query: The search query text.

        Returns:
            Embedding vector as a list of floats.
        """
        return await self.embed_text(query)

    async def _embed_local(self, texts: list[str]) -> list[np.ndarray]:
        """Run local embedding in a thread pool to avoid blocking the event loop.

        Args:
            texts: List of texts to embed.

        Returns:
            List of numpy embedding arrays.
        """
        model = self._load_local_model()
        loop = asyncio.get_running_loop()
        embeddings = await loop.run_in_executor(
            self._executor,
            lambda: model.encode(
                texts,
                convert_to_numpy=True,
                normalize_embeddings=True,
                show_progress_bar=False,
            ),
        )
        return [np.array(emb, dtype=np.float32) for emb in embeddings]

    async def _embed_api(self, texts: list[str]) -> list[np.ndarray]:
        """Generate embeddings using an OpenAI-compatible API.

        Args:
            texts: List of texts to embed.

        Returns:
            List of numpy embedding arrays.
        """
        if not self.api_key:
            raise ValueError("API key is required for API-based embedding backend")

        import httpx

        url = f"{self.api_base}/embeddings"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        # Swap model for API calls (text2vec-base-chinese is not available via API)
        api_model = "text-embedding-ada-002"

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                url,
                headers=headers,
                json={
                    "input": texts,
                    "model": api_model,
                },
            )
            response.raise_for_status()
            data = response.json()

        embeddings = [np.array(item["embedding"], dtype=np.float32) for item in data["data"]]
        self._dimension = int(embeddings[0].shape[0])
        return embeddings

    def _add_to_cache(self, key: str, embedding: np.ndarray) -> None:
        """Add an embedding to the LRU cache, evicting oldest if at capacity.

        Args:
            key: The cache key.
            embedding: The embedding vector to cache.
        """
        if key in self._cache:
            self._cache.move_to_end(key)
        self._cache[key] = embedding
        while len(self._cache) > self.cache_size:
            self._cache.popitem(last=False)

    def get_cache_stats(self) -> dict[str, int]:
        """Return cache hit/miss statistics.

        Returns:
            Dictionary with hits, misses, size, and max_size.
        """
        return {
            "hits": self._cache_hits,
            "misses": self._cache_misses,
            "size": len(self._cache),
            "max_size": self.cache_size,
        }

    def clear_cache(self) -> None:
        """Clear the embedding cache and reset statistics."""
        self._cache.clear()
        self._cache_hits = 0
        self._cache_misses = 0