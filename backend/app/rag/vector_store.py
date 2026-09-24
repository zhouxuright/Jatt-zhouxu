"""Vector store manager supporting Milvus (production) and ChromaDB (development).

Provides a unified interface for vector storage and similarity search across
multiple legal document collections. Auto-detects Milvus availability and
falls back to ChromaDB when Milvus is not reachable.
"""

import logging
import uuid
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)

# Predefined collections for the legal domain
COLLECTIONS = [
    "legal_articles",    # Laws and regulations
    "legal_cases",       # Court cases and judgments
    "legal_knowledge",   # Legal theories, interpretations
    "user_documents",    # User-uploaded documents
]


class VectorStoreManager:
    """Manager for vector store operations across multiple collections.

    Supports two backends:
    - Milvus: Production-grade distributed vector database.
    - ChromaDB: Lightweight local vector store for development.

    The manager auto-detects Milvus availability and falls back to ChromaDB.

    Attributes:
        embedding_service: The EmbeddingService instance for generating embeddings.
        backend: Active backend name ("milvus" or "chroma").
        _milvus_client: Milvus client instance (if connected).
        _chroma_client: ChromaDB client instance (if connected).
        _collections: Dict of collection name to collection handle.
    """

    def __init__(self, embedding_service: Any = None) -> None:
        """Initialize the vector store manager.

        Args:
            embedding_service: EmbeddingService instance for generating embeddings.
                               If None, one will be lazily created.
        """
        self._embedding_service = embedding_service
        self._milvus_client: Any = None
        self._chroma_client: Any = None
        self._collections: dict[str, Any] = {}
        self._backend: str | None = None
        self._initialized: bool = False

    @property
    def backend(self) -> str:
        """Return the active backend name."""
        if self._backend is None:
            self._backend = self._detect_backend()
        return self._backend

    def _detect_backend(self) -> str:
        """Detect which backend to use: Milvus if available, otherwise ChromaDB.

        Returns:
            "milvus" or "chroma".
        """
        # Try Milvus first
        if self._try_milvus_connection():
            logger.info("Using Milvus as vector store backend")
            return "milvus"

        logger.info("Milvus unavailable, falling back to ChromaDB")
        return "chroma"

    def _try_milvus_connection(self) -> bool:
        """Attempt to connect to the Milvus server.

        Returns:
            True if connection succeeded, False otherwise.
        """
        try:
            from pymilvus import connections

            connections.connect(
                alias="default",
                host=settings.MILVUS_HOST,
                port=settings.MILVUS_PORT,
            )
            connections.disconnect("default")
            return True
        except ImportError:
            logger.debug("pymilvus not installed")
            return False
        except Exception as exc:
            logger.debug("Milvus connection failed: %s", exc)
            return False

    async def initialize(self) -> None:
        """Initialize the vector store backend and create collections.

        Must be called before any other operations. Creates all predefined
        collections if they don't exist.
        """
        if self._initialized:
            return

        if self.backend == "milvus":
            await self._init_milvus()
        else:
            await self._init_chroma()

        self._initialized = True
        logger.info("Vector store initialized with backend: %s", self.backend)

    async def _init_milvus(self) -> None:
        """Initialize Milvus backend and create collections."""
        try:
            from pymilvus import Collection, CollectionSchema, DataType, FieldSchema, connections, utility

            connections.connect(
                alias="default",
                host=settings.MILVUS_HOST,
                port=settings.MILVUS_PORT,
            )

            dim = self._get_dimension()

            for collection_name in COLLECTIONS:
                if utility.has_collection(collection_name):
                    collection = Collection(collection_name)
                    collection.load()
                    self._collections[collection_name] = collection
                    logger.debug("Loaded existing Milvus collection: %s", collection_name)
                    continue

                # Define schema
                fields = [
                    FieldSchema(name="id", dtype=DataType.VARCHAR, is_primary=True, max_length=64),
                    FieldSchema(name="text", dtype=DataType.VARCHAR, max_length=65535),
                    FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=dim),
                    FieldSchema(name="metadata", dtype=DataType.JSON),
                ]
                schema = CollectionSchema(fields, description=f"Legal documents - {collection_name}")

                collection = Collection(collection_name, schema)

                # Create index
                index_params = {
                    "metric_type": "COSINE",
                    "index_type": "IVF_FLAT",
                    "params": {"nlist": 128},
                }
                collection.create_index("embedding", index_params)
                collection.load()
                self._collections[collection_name] = collection
                logger.info("Created Milvus collection: %s (dim=%d)", collection_name, dim)

            self._milvus_client = True
        except Exception as exc:
            logger.error("Failed to initialize Milvus: %s", exc)
            raise

    async def _init_chroma(self) -> None:
        """Initialize ChromaDB backend and create collections."""
        try:
            import chromadb
            from chromadb.config import Settings as ChromaSettings

            self._chroma_client = chromadb.PersistentClient(
                path=settings.CHROMA_PERSIST_DIRECTORY,
                settings=ChromaSettings(anonymized_telemetry=False),
            )

            for collection_name in COLLECTIONS:
                try:
                    collection = self._chroma_client.get_collection(collection_name)
                    logger.debug("Loaded existing ChromaDB collection: %s", collection_name)
                except Exception:
                    collection = self._chroma_client.create_collection(
                        name=collection_name,
                        metadata={"hnsw:space": "cosine"},
                    )
                    logger.info("Created ChromaDB collection: %s", collection_name)

                self._collections[collection_name] = collection
        except ImportError:
            logger.error("chromadb not installed. Run: pip install chromadb")
            raise
        except Exception as exc:
            logger.error("Failed to initialize ChromaDB: %s", exc)
            raise

    def _get_dimension(self) -> int:
        """Get the embedding dimension used to create collections.

        Prefers the embedding service's dimension, which it resolves from the
        actually-loaded model. Falls back to the configured MILVUS_DIMENSION.

        NOTE: this value must match the embeddings written by
        `add_documents()`. If they disagree, collection creation succeeds and
        every insert then fails on a dimension mismatch, so the two must come
        from the same source — do not pass an embedding service here unless it
        is the same instance used for insertion.
        """
        if self._embedding_service is not None:
            return self._embedding_service.dimension
        return settings.MILVUS_DIMENSION

    async def add_documents(
        self,
        texts: list[str],
        collection_name: str,
        metadatas: list[dict[str, Any]] | None = None,
        ids: list[str] | None = None,
    ) -> list[str]:
        """Add documents to the specified collection.

        Args:
            texts: List of document text contents.
            collection_name: Target collection name.
            metadatas: Optional list of metadata dicts for each document.
            ids: Optional list of document IDs. Auto-generated if not provided.

        Returns:
            List of document IDs that were added.
        """
        if collection_name not in COLLECTIONS:
            raise ValueError(f"Invalid collection: {collection_name}. Valid: {COLLECTIONS}")

        if not self._initialized:
            await self.initialize()

        # Generate embeddings
        embeddings = await self._embedding_service.embed_texts(texts)

        # Generate IDs if not provided
        if ids is None:
            ids = [str(uuid.uuid4()) for _ in texts]

        if metadatas is None:
            metadatas = [{} for _ in texts]

        if self.backend == "milvus":
            await self._milvus_insert(collection_name, texts, embeddings, metadatas, ids)
        else:
            self._chroma_insert(collection_name, texts, embeddings, metadatas, ids)

        logger.info("Added %d documents to collection '%s'", len(texts), collection_name)
        return ids

    async def _milvus_insert(
        self,
        collection_name: str,
        texts: list[str],
        embeddings: list[list[float]],
        metadatas: list[dict[str, Any]],
        ids: list[str],
    ) -> None:
        """Insert documents into a Milvus collection."""
        from pymilvus import Collection

        collection: Collection = self._collections[collection_name]
        data = [
            ids,
            texts,
            embeddings,
            metadatas,
        ]
        collection.insert(data)
        collection.flush()

    def _chroma_insert(
        self,
        collection_name: str,
        texts: list[str],
        embeddings: list[list[float]],
        metadatas: list[dict[str, Any]],
        ids: list[str],
    ) -> None:
        """Insert documents into a ChromaDB collection."""
        collection = self._collections[collection_name]
        collection.add(
            ids=ids,
            embeddings=embeddings,
            documents=texts,
            metadatas=metadatas,
        )

    async def similarity_search(
        self,
        query: str,
        collection_name: str,
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        """Search for documents similar to the query.

        Args:
            query: The search query text.
            collection_name: Collection to search in.
            top_k: Number of results to return.

        Returns:
            List of result dicts with keys: id, text, metadata, score.
        """
        results = await self.similarity_search_with_score(
            query=query,
            collection_name=collection_name,
            top_k=top_k,
        )
        return [{"id": r["id"], "text": r["text"], "metadata": r["metadata"]} for r in results]

    async def similarity_search_with_score(
        self,
        query: str,
        collection_name: str,
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        """Search for similar documents with relevance scores.

        Args:
            query: The search query text.
            collection_name: Collection to search in.
            top_k: Number of results to return.

        Returns:
            List of result dicts with keys: id, text, metadata, score.
        """
        if collection_name not in COLLECTIONS:
            raise ValueError(f"Invalid collection: {collection_name}. Valid: {COLLECTIONS}")

        if not self._initialized:
            await self.initialize()

        query_embedding = await self._embedding_service.embed_query(query)

        if self.backend == "milvus":
            return await self._milvus_search(collection_name, query_embedding, top_k)
        else:
            return self._chroma_search(collection_name, query_embedding, top_k)

    async def _milvus_search(
        self,
        collection_name: str,
        query_embedding: list[float],
        top_k: int,
    ) -> list[dict[str, Any]]:
        """Search a Milvus collection."""
        from pymilvus import Collection

        collection: Collection = self._collections[collection_name]
        search_params = {
            "metric_type": "COSINE",
            "params": {"nprobe": 16},
        }
        results = collection.search(
            data=[query_embedding],
            anns_field="embedding",
            param=search_params,
            limit=top_k,
            output_fields=["id", "text", "metadata"],
        )

        return [
            {
                "id": hit.id,
                "text": hit.entity.get("text", ""),
                "metadata": hit.entity.get("metadata", {}),
                "score": float(hit.distance),
            }
            for hit in results[0]
        ]

    def _chroma_search(
        self,
        collection_name: str,
        query_embedding: list[float],
        top_k: int,
    ) -> list[dict[str, Any]]:
        """Search a ChromaDB collection."""
        collection = self._collections[collection_name]
        results = collection.query(
            query_embeddings=[query_embedding],
            n_results=top_k,
            include=["documents", "metadatas", "distances"],
        )

        items: list[dict[str, Any]] = []
        if results["ids"] and results["ids"][0]:
            for i, doc_id in enumerate(results["ids"][0]):
                items.append({
                    "id": doc_id,
                    "text": results["documents"][0][i] if results["documents"] else "",
                    "metadata": results["metadatas"][0][i] if results["metadatas"] else {},
                    "score": 1.0 - float(results["distances"][0][i]) if results["distances"] else 0.0,
                })
        return items

    async def delete_documents(
        self,
        ids: list[str],
        collection_name: str,
    ) -> int:
        """Delete documents by ID from a collection.

        Args:
            ids: List of document IDs to delete.
            collection_name: Target collection name.

        Returns:
            Number of documents deleted.
        """
        if collection_name not in COLLECTIONS:
            raise ValueError(f"Invalid collection: {collection_name}")

        if not self._initialized:
            await self.initialize()

        if self.backend == "milvus":
            from pymilvus import Collection
            collection: Collection = self._collections[collection_name]
            expr = f"id in {ids}" if len(ids) > 1 else f'id == "{ids[0]}"'
            result = collection.delete(expr)
            collection.flush()
            deleted = result.delete_count if hasattr(result, "delete_count") else len(ids)
        else:
            collection = self._collections[collection_name]
            collection.delete(ids=ids)
            deleted = len(ids)

        logger.info("Deleted %d documents from collection '%s'", deleted, collection_name)
        return deleted

    async def count(self, collection_name: str) -> int:
        """Return the number of documents in a collection.

        Args:
            collection_name: Target collection name.

        Returns:
            Document count.
        """
        if collection_name not in COLLECTIONS:
            raise ValueError(f"Invalid collection: {collection_name}")

        if not self._initialized:
            await self.initialize()

        if self.backend == "milvus":
            from pymilvus import Collection
            collection: Collection = self._collections[collection_name]
            collection.flush()
            return collection.num_entities
        else:
            collection = self._collections[collection_name]
            return collection.count()

    def get_collection_names(self) -> list[str]:
        """Return the list of available collection names."""
        return list(COLLECTIONS)

    def get_stats(self) -> dict[str, Any]:
        """Return statistics about the vector store.

        Returns:
            Dict with backend, collections, and document counts.
        """
        stats: dict[str, Any] = {
            "backend": self.backend,
            "initialized": self._initialized,
            "collections": {},
        }
        for name in COLLECTIONS:
            if name in self._collections:
                if self.backend == "milvus":
                    from pymilvus import Collection
                    col: Collection = self._collections[name]
                    col.flush()
                    stats["collections"][name] = col.num_entities
                else:
                    stats["collections"][name] = self._collections[name].count()
            else:
                stats["collections"][name] = 0
        return stats