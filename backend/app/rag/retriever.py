"""Hybrid retriever combining vector, keyword, and knowledge graph search.

Implements Reciprocal Rank Fusion (RRF) for result merging and cross-encoder
reranking for improved relevance.
"""

import asyncio
import logging
import math
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)


class HybridRetriever:
    """Combines multiple retrieval strategies with fusion and reranking.

    Retrieval strategies:
    - Vector search: Semantic similarity via embedding vectors
    - Keyword search: BM25/Elasticsearch-based keyword matching
    - Knowledge graph: Entity-based traversal through legal relationships

    Fusion methods:
    - Reciprocal Rank Fusion (RRF): Combines ranked lists from multiple sources
    - Weighted score fusion: Linear combination of normalized scores

    Attributes:
        vector_store: VectorStoreManager instance for vector search.
        embedding_service: EmbeddingService for query embedding.
        knowledge_graph: KnowledgeGraphManager for graph queries.
        _reranker: Cross-encoder model for reranking (lazy loaded).
    """

    def __init__(
        self,
        vector_store: Any = None,
        embedding_service: Any = None,
        knowledge_graph: Any = None,
        rrf_k: int = 60,
        use_reranker: bool = True,
    ) -> None:
        """Initialize the hybrid retriever.

        Args:
            vector_store: VectorStoreManager instance.
            embedding_service: EmbeddingService instance.
            knowledge_graph: KnowledgeGraphManager instance.
            rrf_k: RRF constant controlling rank emphasis (default: 60).
            use_reranker: Whether to use cross-encoder reranking.
        """
        self._vector_store = vector_store
        self._embedding_service = embedding_service
        self._knowledge_graph = knowledge_graph
        self.rrf_k = rrf_k
        self.use_reranker = use_reranker
        self._reranker: Any = None
        self._es_client: Any = None

    async def retrieve(
        self,
        query: str,
        collection_name: str = "legal_articles",
        top_k: int = 5,
        use_vector: bool = True,
        use_keyword: bool = True,
        use_graph: bool = True,
    ) -> list[dict[str, Any]]:
        """Retrieve relevant documents using hybrid search.

        Args:
            query: The search query.
            collection_name: Target collection name.
            top_k: Number of final results.
            use_vector: Enable vector search.
            use_keyword: Enable keyword search.
            use_graph: Enable knowledge graph search.

        Returns:
            List of retrieved document dicts with scores.
        """
        results = await self.retrieve_with_scores(
            query=query,
            collection_name=collection_name,
            top_k=top_k * 3,  # Fetch more for reranking
            use_vector=use_vector,
            use_keyword=use_keyword,
            use_graph=use_graph,
        )

        # Rerank if enabled
        if self.use_reranker and len(results) > top_k:
            results = await self._rerank(query, results, top_k)

        return results[:top_k]

    async def retrieve_with_scores(
        self,
        query: str,
        collection_name: str = "legal_articles",
        top_k: int = 15,
        use_vector: bool = True,
        use_keyword: bool = True,
        use_graph: bool = True,
    ) -> list[dict[str, Any]]:
        """Retrieve documents with relevance scores from multiple sources.

        Args:
            query: The search query.
            collection_name: Target collection name.
            top_k: Number of results per source.
            use_vector: Enable vector search.
            use_keyword: Enable keyword search.
            use_graph: Enable knowledge graph search.

        Returns:
            List of fused document dicts with scores.
        """
        tasks = []

        if use_vector and self._vector_store:
            tasks.append(self._vector_retrieve(query, collection_name, top_k))

        if use_keyword:
            tasks.append(self._keyword_retrieve(query, collection_name, top_k))

        if use_graph and self._knowledge_graph:
            tasks.append(self._graph_retrieve(query, top_k))

        if not tasks:
            return []

        # Run all retrievers in parallel
        result_lists = await asyncio.gather(*tasks, return_exceptions=True)

        # Collect valid results
        all_results: list[list[dict[str, Any]]] = []
        for result in result_lists:
            if isinstance(result, Exception):
                logger.warning("Retrieval error: %s", result)
            elif isinstance(result, list):
                all_results.append(result)

        if not all_results:
            return []

        # Fuse results using Reciprocal Rank Fusion
        fused = self._reciprocal_rank_fusion(all_results)
        return fused[:top_k]

    async def multi_query_retrieve(
        self,
        queries: list[str],
        collection_name: str = "legal_articles",
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        """Retrieve using multiple query variations for better recall.

        Args:
            queries: List of query variations.
            collection_name: Target collection name.
            top_k: Number of final results.

        Returns:
            List of fused document dicts with scores.
        """
        all_results: list[list[dict[str, Any]]] = []

        for query in queries:
            results = await self.retrieve_with_scores(
                query=query,
                collection_name=collection_name,
                top_k=top_k * 2,
                use_vector=True,
                use_keyword=True,
                use_graph=False,
            )
            all_results.append(results)

        if not all_results:
            return []

        fused = self._reciprocal_rank_fusion(all_results)
        return fused[:top_k]

    async def _vector_retrieve(
        self,
        query: str,
        collection_name: str,
        top_k: int,
    ) -> list[dict[str, Any]]:
        """Perform vector similarity search."""
        try:
            results = await self._vector_store.similarity_search_with_score(
                query=query,
                collection_name=collection_name,
                top_k=top_k,
            )
            for r in results:
                r["source"] = "vector"
            return results
        except Exception as exc:
            logger.warning("Vector retrieval failed: %s", exc)
            return []

    async def _keyword_retrieve(
        self,
        query: str,
        collection_name: str,
        top_k: int,
    ) -> list[dict[str, Any]]:
        """Perform keyword-based search using Elasticsearch.

        Falls back to simple string matching if Elasticsearch is unavailable.
        """
        es_results = await self._es_search(query, collection_name, top_k)
        if es_results:
            return es_results

        # Fallback: simple keyword matching via vector store text search
        if self._vector_store:
            try:
                results = await self._vector_store.similarity_search_with_score(
                    query=query,
                    collection_name=collection_name,
                    top_k=top_k,
                )
                for r in results:
                    r["source"] = "keyword_fallback"
                    # Boost score for exact/substring matches
                    text_lower = r.get("text", "").lower()
                    query_lower = query.lower()
                    if query_lower in text_lower:
                        r["score"] = min(r["score"] * 1.3, 1.0)
                return results
            except Exception as exc:
                logger.warning("Keyword fallback failed: %s", exc)

        return []

    async def _es_search(
        self,
        query: str,
        collection_name: str,
        top_k: int,
    ) -> list[dict[str, Any]]:
        """Search using Elasticsearch."""
        try:
            client = await self._get_es_client()
            if client is None:
                return []

            index_name = f"{settings.ELASTICSEARCH_INDEX_PREFIX}{collection_name}"
            body = {
                "query": {
                    "multi_match": {
                        "query": query,
                        "fields": ["text", "title^2"],
                        "type": "best_fields",
                    }
                },
                "size": top_k,
            }
            response = client.search(index=index_name, body=body)
            hits = response.get("hits", {}).get("hits", [])

            return [
                {
                    "id": hit["_id"],
                    "text": hit["_source"].get("text", ""),
                    "metadata": hit["_source"].get("metadata", {}),
                    "score": hit["_score"] / 10.0 if hit.get("_score") else 0.0,
                    "source": "keyword",
                }
                for hit in hits
            ]
        except ImportError:
            return []
        except Exception as exc:
            logger.debug("Elasticsearch search failed: %s", exc)
            return []

    async def _get_es_client(self) -> Any:
        """Get or create Elasticsearch client."""
        if self._es_client is not None:
            return self._es_client

        try:
            from elasticsearch import Elasticsearch

            self._es_client = Elasticsearch(
                settings.ELASTICSEARCH_HOST,
                request_timeout=10,
            )
            if not self._es_client.ping():
                logger.debug("Elasticsearch ping failed")
                self._es_client = None
                return None
            return self._es_client
        except ImportError:
            logger.debug("elasticsearch not installed")
            return None
        except Exception as exc:
            logger.debug("Elasticsearch connection failed: %s", exc)
            self._es_client = None
            return None

    async def _graph_retrieve(
        self,
        query: str,
        top_k: int,
    ) -> list[dict[str, Any]]:
        """Search the knowledge graph for relevant entities."""
        try:
            # Search for legal concepts and articles
            entities = await self._knowledge_graph.search_entities(
                keyword=query,
                limit=top_k,
            )

            results: list[dict[str, Any]] = []
            for entity in entities:
                # Get related entities for each match
                related = await self._knowledge_graph.query_related_laws(
                    article_id=entity.get("id", ""),
                    limit=5,
                )

                results.append({
                    "id": entity.get("id", ""),
                    "text": f"{entity.get('type', '')}: {entity.get('name', '')}",
                    "metadata": {
                        "entity_type": entity.get("type"),
                        "related": related,
                        "properties": entity.get("properties", {}),
                    },
                    "score": 0.8,
                    "source": "knowledge_graph",
                })

            return results
        except Exception as exc:
            logger.warning("Graph retrieval failed: %s", exc)
            return []

    def _reciprocal_rank_fusion(
        self,
        result_lists: list[list[dict[str, Any]]],
    ) -> list[dict[str, Any]]:
        """Merge multiple ranked result lists using Reciprocal Rank Fusion.

        RRF score = sum(1 / (k + rank_i)) for each result list i.
        The constant k controls how much rank position matters.

        Args:
            result_lists: Lists of ranked results from different sources.

        Returns:
            Fused and re-ranked list of results.
        """
        if not result_lists:
            return []

        # Score each document by its rank across all lists
        scores: dict[str, tuple[float, dict[str, Any]]] = {}

        for results in result_lists:
            for rank, doc in enumerate(results):
                doc_id = doc.get("id", "")
                if not doc_id:
                    doc_id = self._make_doc_id(doc)
                    doc["id"] = doc_id

                rrf_score = 1.0 / (self.rrf_k + rank + 1)
                if doc_id in scores:
                    existing_score, existing_doc = scores[doc_id]
                    scores[doc_id] = (
                        existing_score + rrf_score,
                        self._merge_docs(existing_doc, doc),
                    )
                else:
                    scores[doc_id] = (rrf_score, doc)

        # Sort by RRF score descending
        sorted_items = sorted(scores.values(), key=lambda x: x[0], reverse=True)
        fused = []
        for score, doc in sorted_items:
            doc["score"] = score
            fused.append(doc)

        return fused

    def _make_doc_id(self, doc: dict[str, Any]) -> str:
        """Generate a synthetic ID from document content."""
        text = doc.get("text", "")
        return str(hash(text))

    def _merge_docs(
        self,
        doc1: dict[str, Any],
        doc2: dict[str, Any],
    ) -> dict[str, Any]:
        """Merge two document dicts, preferring the one with more metadata."""
        if len(doc1.get("metadata", {})) >= len(doc2.get("metadata", {})):
            merged = {**doc1}
        else:
            merged = {**doc2}

        # Combine sources
        sources = set()
        if "source" in doc1:
            sources.add(doc1["source"])
        if "source" in doc2:
            sources.add(doc2["source"])
        merged["source"] = "+".join(sorted(sources))

        return merged

    async def _rerank(
        self,
        query: str,
        documents: list[dict[str, Any]],
        top_k: int,
    ) -> list[dict[str, Any]]:
        """Rerank documents using a cross-encoder model.

        Args:
            query: The search query.
            documents: List of retrieved documents.
            top_k: Number of top results to return.

        Returns:
            Reranked list of documents.
        """
        try:
            model = self._load_reranker()
            if model is None:
                return documents[:top_k]

            pairs = [(query, doc.get("text", "")) for doc in documents]
            scores = model.predict(pairs, show_progress_bar=False)

            for doc, score in zip(documents, scores):
                doc["rerank_score"] = float(score)
                doc["score"] = float(score)

            documents.sort(key=lambda x: x.get("score", 0), reverse=True)
            return documents[:top_k]

        except Exception as exc:
            logger.warning("Reranking failed: %s", exc)
            return documents[:top_k]

    def _load_reranker(self) -> Any:
        """Lazily load the cross-encoder reranker model."""
        if self._reranker is not None:
            return self._reranker

        try:
            from sentence_transformers import CrossEncoder

            reranker_model = "cross-encoder/ms-marco-MiniLM-L-6-v2"
            self._reranker = CrossEncoder(reranker_model)
            logger.info("Loaded reranker model: %s", reranker_model)
            return self._reranker
        except ImportError:
            logger.warning("sentence-transformers not installed, reranking disabled")
            return None
        except Exception as exc:
            logger.warning("Failed to load reranker: %s", exc)
            return None

    def get_stats(self) -> dict[str, Any]:
        """Return statistics about the retriever configuration.

        Returns:
            Dict with retriever configuration and component status.
        """
        return {
            "vector_store": self._vector_store is not None,
            "embedding_service": self._embedding_service is not None,
            "knowledge_graph": self._knowledge_graph is not None,
            "reranker_loaded": self._reranker is not None,
            "rrf_k": self.rrf_k,
            "use_reranker": self.use_reranker,
        }