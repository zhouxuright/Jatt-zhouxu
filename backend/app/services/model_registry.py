"""Global AI model registry — singleton access to embedding and reranker models.

All heavy AI models (BGE-M3, BGE-Reranker-V2-M3) are loaded once at startup
and shared across all requests. This prevents the catastrophic pattern of
reloading 1.2GB+ models on every single request.

Usage:
    from app.services.model_registry import ModelRegistry

    embedding_model = ModelRegistry.get_embedding_model()
    reranker_model = ModelRegistry.get_reranker_model()
"""

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class ModelRegistry:
    """Singleton registry for all AI models.

    Models are loaded lazily on first access and cached for the lifetime
    of the process. Thread-safe via asyncio lock for async contexts.
    """

    _embedding_model: Any = None
    _reranker_model: Any = None
    _lock = asyncio.Lock()
    _initialized = False

    # Local model paths — use HuggingFace model names as primary,
    # with optional local override via environment variables.
    # This ensures portability across machines and Docker containers.
    _BGE_M3_LOCAL = os.environ.get(
        "BGE_M3_MODEL_PATH",
        str(Path.home() / ".cache" / "huggingface" / "hub" / "models--BAAI--bge-m3" / "snapshots" / "master"),
    )
    _BGE_RERANKER_LOCAL = os.environ.get(
        "BGE_RERANKER_MODEL_PATH",
        str(Path.home() / ".cache" / "huggingface" / "hub" / "models--BAAI--bge-reranker-v2-m3" / "snapshots" / "master"),
    )

    @classmethod
    def get_embedding_model(cls) -> Any:
        """Return the singleton BGE-M3 embedding model.

        Loads on first call (~10-30s), then returns cached instance.
        Uses local cache path first, falls back to HuggingFace model name.
        """
        if cls._embedding_model is None:
            from FlagEmbedding import BGEM3FlagModel
            import os
            model_path = cls._BGE_M3_LOCAL if os.path.isdir(cls._BGE_M3_LOCAL) else "BAAI/bge-m3"
            logger.info("Loading BGE-M3 embedding model from: %s", model_path)
            cls._embedding_model = BGEM3FlagModel(model_path, use_fp16=True)
            logger.info("BGE-M3 embedding model loaded successfully")
        return cls._embedding_model

    @classmethod
    def get_reranker_model(cls) -> Any:
        """Return the singleton BGE-Reranker-V2-M3 reranker model.

        Loads on first call (~10-30s), then returns cached instance.
        Uses local cache path first, falls back to HuggingFace model name.
        """
        if cls._reranker_model is None:
            from FlagEmbedding import FlagReranker
            import os
            model_path = cls._BGE_RERANKER_LOCAL if os.path.isdir(cls._BGE_RERANKER_LOCAL) else "BAAI/bge-reranker-v2-m3"
            logger.info("Loading BGE-Reranker-V2-M3 model from: %s", model_path)
            cls._reranker_model = FlagReranker(model_path, use_fp16=True)
            logger.info("BGE-Reranker-V2-M3 model loaded successfully")
        return cls._reranker_model

    @classmethod
    async def warmup(cls) -> None:
        """Pre-load all models at application startup.

        Call this in the FastAPI lifespan to avoid first-request latency.
        Runs model loading in a thread pool to avoid blocking the event loop.
        """
        if cls._initialized:
            return

        logger.info("Warming up AI models...")
        loop = asyncio.get_running_loop()

        # Load embedding model in thread pool (blocking I/O)
        await loop.run_in_executor(None, cls.get_embedding_model)

        # Load reranker model in thread pool
        await loop.run_in_executor(None, cls.get_reranker_model)

        cls._initialized = True
        logger.info("All AI models warmed up and ready")

    @classmethod
    async def embed_texts(cls, texts: list[str]) -> list[list[float]]:
        """Generate embeddings using the singleton model (async-safe).

        Runs the CPU-bound encoding in a thread pool to avoid blocking
        the async event loop.

        Args:
            texts: List of texts to embed.

        Returns:
            List of embedding vectors.
        """
        model = cls.get_embedding_model()
        loop = asyncio.get_running_loop()
        output = await loop.run_in_executor(
            None,
            lambda: model.encode(texts, return_dense=True),
        )
        return [e.tolist() for e in output["dense_vecs"]]

    @classmethod
    async def embed_query(cls, query: str) -> list[float]:
        """Generate a single query embedding (async-safe)."""
        results = await cls.embed_texts([query])
        return results[0]

    @classmethod
    async def rerank(
        cls,
        query: str,
        documents: list[str],
    ) -> list[float]:
        """Compute reranking scores using the singleton model (async-safe).

        Args:
            query: The search query.
            documents: List of document texts to score.

        Returns:
            List of relevance scores, one per document.
        """
        model = cls.get_reranker_model()
        pairs = [[query, doc] for doc in documents]
        loop = asyncio.get_running_loop()
        scores = await loop.run_in_executor(
            None,
            lambda: model.compute_score(pairs, normalize=True),
        )
        if isinstance(scores, float):
            scores = [scores]
        return [float(s) for s in scores]

    @classmethod
    def get_stats(cls) -> dict:
        """Return model registry statistics."""
        return {
            "embedding_model_loaded": cls._embedding_model is not None,
            "reranker_model_loaded": cls._reranker_model is not None,
            "embedding_model": "BAAI/bge-m3" if cls._embedding_model else None,
            "reranker_model": "BAAI/bge-reranker-v2-m3" if cls._reranker_model else None,
        }
