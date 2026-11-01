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
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)


class ModelRegistry:
    """Singleton registry for all AI models.

    Models are loaded lazily on first access and cached for the lifetime
    of the process. Thread-safe via a process-wide ``threading.Lock`` with
    double-checked locking: retrieval runs on worker threads, so an
    asyncio lock (bound to one event loop) would not protect the loaders
    and two concurrent first requests could load a 1.2GB model twice inside
    the same interpreter — which is what crashed it with a segfault.
    """

    _embedding_model: Any = None
    _reranker_model: Any = None
    _lock = threading.Lock()
    _initialized = False

    # 单条 query 的 embedding LRU 缓存。检索链里同一个 query 会被编码多次
    # （BM25 管线 / Milvus / 语义缓存），BGE-M3 单条 CPU 编码 1-3s，
    # 命中缓存可直接省掉这部分延迟与算力。
    _embed_cache: "OrderedDict[str, list[float]]" = OrderedDict()
    _embed_cache_lock = threading.Lock()

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
            with cls._lock:
                if cls._embedding_model is None:
                    cls._pin_torch_threads()
                    from FlagEmbedding import BGEM3FlagModel
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
            with cls._lock:
                if cls._reranker_model is None:
                    cls._pin_torch_threads()
                    from FlagEmbedding import FlagReranker
                    model_path = cls._BGE_RERANKER_LOCAL if os.path.isdir(cls._BGE_RERANKER_LOCAL) else "BAAI/bge-reranker-v2-m3"
                    logger.info("Loading BGE-Reranker-V2-M3 model from: %s", model_path)
                    cls._reranker_model = FlagReranker(model_path, use_fp16=True)
                    logger.info("BGE-Reranker-V2-M3 model loaded successfully")
        return cls._reranker_model

    @staticmethod
    def _cuda_available() -> bool:
        """Whether torch can see a CUDA device (cross-encoder needs GPU)."""
        try:
            import torch
            return bool(torch.cuda.is_available())
        except Exception:  # pragma: no cover - torch is a hard dependency
            return False

    @staticmethod
    def _pin_torch_threads() -> None:
        """Force torch to a single compute thread.

        In a 2-CPU container the default intra-op pool spawned one thread per
        core per inference; overlapping inferences raced in native code and
        segfaulted the interpreter. One thread per process is stable and, for
        single-query inference, not measurably slower.
        """
        try:
            import torch
            torch.set_num_threads(1)
            torch.set_num_interop_threads(1)
        except Exception as exc:  # pragma: no cover - best effort
            logger.debug("Could not pin torch threads: %s", exc)

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

        # Load reranker model in thread pool.
        #
        # Cross-encoder 重排只在 GPU 上真正执行：CrossEncoderReranker.rerank()
        # 检测不到 CUDA 时直接按融合顺序返回，根本不会加载模型。CPU 部署
        # 若仍在这里预热 reranker，只会白占约 1.1GB 常驻内存（生产实测
        # 容器内存 2.14GiB / limit 3.5GiB），因此这里按可用性跳过。
        if settings.RAG_RERANK_ENABLED and cls._cuda_available():
            await loop.run_in_executor(None, cls.get_reranker_model)
        else:
            logger.info(
                "Skipping reranker warmup (rerank_enabled=%s, cuda=%s)",
                settings.RAG_RERANK_ENABLED, cls._cuda_available(),
            )

        cls._initialized = True
        logger.info("All AI models warmed up and ready")

    @classmethod
    def _cache_get(cls, text: str) -> list[float] | None:
        with cls._embed_cache_lock:
            vec = cls._embed_cache.get(text)
            if vec is not None:
                cls._embed_cache.move_to_end(text)
            return vec

    @classmethod
    def _cache_put(cls, text: str, vec: list[float]) -> None:
        with cls._embed_cache_lock:
            cls._embed_cache[text] = vec
            cls._embed_cache.move_to_end(text)
            while len(cls._embed_cache) > settings.RAG_QUERY_EMBEDDING_CACHE_SIZE:
                cls._embed_cache.popitem(last=False)

    @classmethod
    def embed_one_sync(cls, text: str) -> list[float]:
        """Synchronous single-text embedding with an LRU cache.

        Used by the retrieval pipeline (which runs on its own executor
        thread) so repeated queries never pay the BGE-M3 encoding cost twice.
        """
        cached = cls._cache_get(text)
        if cached is not None:
            return cached
        model = cls.get_embedding_model()
        output = model.encode([text], return_dense=True)
        vec = output["dense_vecs"][0].tolist()
        cls._cache_put(text, vec)
        return vec

    @classmethod
    async def embed_texts(cls, texts: list[str]) -> list[list[float]]:
        """Generate embeddings using the singleton model (async-safe).

        Runs the CPU-bound encoding in a thread pool to avoid blocking
        the async event loop. Single-text calls are LRU-cached.

        Args:
            texts: List of texts to embed.

        Returns:
            List of embedding vectors.
        """
        if len(texts) == 1:
            cached = cls._cache_get(texts[0])
            if cached is not None:
                return [cached]

        model = cls.get_embedding_model()
        loop = asyncio.get_running_loop()
        output = await loop.run_in_executor(
            None,
            lambda: model.encode(texts, return_dense=True),
        )
        vectors = [e.tolist() for e in output["dense_vecs"]]
        if len(texts) == 1:
            cls._cache_put(texts[0], vectors[0])
        return vectors

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
