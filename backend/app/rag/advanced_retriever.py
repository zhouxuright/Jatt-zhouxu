"""Advanced RAG retrieval: BM25 keyword + Milvus vector + Graph-enhanced + Cross-Encoder reranking.

Architecture:
1. BM25 keyword search (rank_bm25) → top-K candidates
2. Milvus vector similarity search → top-K candidates
3. Knowledge graph traversal → cross-referenced articles, same-chapter articles
4. Reciprocal Rank Fusion (RRF) → merge & deduplicate (with 1.2x graph boost)
5. Cross-Encoder reranking → final top-N results

This implements the full hybrid retrieval + reranking pipeline.
"""

import asyncio
import logging
import math
import re
from collections import Counter
from typing import Any

logger = logging.getLogger(__name__)


# ============================================================================
# BM25 Implementation (Chinese-optimized)
# ============================================================================

class BM25Retriever:
    """BM25 keyword retriever optimized for Chinese legal text.

    Uses jieba-style character n-gram tokenization for Chinese text
    since word segmentation may not be available.
    """

    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self.documents: list[dict[str, Any]] = []
        self.doc_tokens: list[list[str]] = []
        self.doc_len: list[int] = []
        self.avgdl: float = 0.0
        self.idf: dict[str, float] = {}
        self.n_docs: int = 0

    def _tokenize(self, text: str) -> list[str]:
        """Tokenize Chinese text using character bigrams + legal keywords.

        For Chinese text without word segmentation, we use:
        1. Legal keyword matching (highest priority)
        2. Character bigrams for general matching
        3. Single characters as fallback
        """
        tokens: list[str] = []

        # 1. Legal domain keywords (multi-char)
        legal_kws = [
            "劳动合同", "劳动关系", "用人单位", "劳动者", "经济补偿", "赔偿金",
            "违约责任", "违约金", "解除合同", "终止合同", "双倍工资",
            "民法典", "合同法", "刑法", "宪法", "劳动法",
            "婚姻关系", "夫妻共同", "财产分割", "抚养权", "赡养",
            "借款合同", "还款", "利息", "债务", "借条",
            "侵权责任", "过错责任", "损害赔偿", "高空抛物",
            "故意杀人", "故意伤害", "盗窃", "诈骗", "贪污", "受贿",
            "知识产权", "著作权", "专利权", "商标权",
            "仲裁", "诉讼", "管辖", "起诉", "上诉",
            "无固定期限", "书面合同", "试用期", "社会保险",
            "人身自由", "人格尊严", "平等权", "人权",
            "依法治国", "法治",
            # 房屋租赁 / 房地产领域
            "房屋租赁", "租赁合同", "租赁", "租房", "押金", "租金", "房租",
            "出租", "承租", "出租人", "承租人", "房东", "房客", "转租",
            "保证金", "订金", "定金", "中介费", "中介", "房地产", "商品房",
            "物业费", "物业服务", "不动产", "产权", "过户", "购房",
        ]
        for kw in legal_kws:
            if kw in text:
                tokens.append(kw)

        # 2. Character bigrams
        text_clean = re.sub(r'\s+', '', text)
        for i in range(len(text_clean) - 1):
            bigram = text_clean[i:i+2]
            if bigram.strip():
                tokens.append(bigram)

        # 3. Single characters (for short queries)
        for ch in text_clean:
            if ch.strip() and not ch.isdigit():
                tokens.append(ch)

        return tokens

    def add_documents(self, documents: list[dict[str, Any]]) -> None:
        """Add documents to the BM25 index.

        Args:
            documents: List of dicts with 'content', 'law_name', 'article_number', etc.
        """
        self.documents = documents
        self.doc_tokens = [self._tokenize(doc.get("content", "") + " " + doc.get("tags", "") + " " + doc.get("law_name", "")) for doc in documents]
        self.doc_len = [len(tokens) for tokens in self.doc_tokens]
        self.n_docs = len(documents)
        self.avgdl = sum(self.doc_len) / max(self.n_docs, 1)

        # Compute IDF
        df: dict[str, int] = Counter()
        for tokens in self.doc_tokens:
            unique_tokens = set(tokens)
            for token in unique_tokens:
                df[token] += 1

        self.idf = {}
        for term, freq in df.items():
            self.idf[term] = math.log((self.n_docs - freq + 0.5) / (freq + 0.5) + 1.0)

    def search(self, query: str, top_k: int = 10) -> list[dict[str, Any]]:
        """Search documents using BM25 scoring.

        Args:
            query: Search query text.
            top_k: Number of results to return.

        Returns:
            List of documents with BM25 scores, sorted by relevance.
        """
        if not self.documents:
            return []

        query_tokens = self._tokenize(query)
        if not query_tokens:
            return []

        scores: list[float] = []
        for i, doc_tokens in enumerate(self.doc_tokens):
            score = 0.0
            doc_len = self.doc_len[i]
            token_freq: dict[str, int] = Counter(doc_tokens)

            for qt in query_tokens:
                if qt not in token_freq:
                    continue
                tf = token_freq[qt]
                idf = self.idf.get(qt, 0.0)
                numerator = tf * (self.k1 + 1)
                denominator = tf + self.k1 * (1 - self.b + self.b * doc_len / max(self.avgdl, 1))
                score += idf * numerator / denominator

            scores.append(score)

        # Sort by score descending
        indexed_scores = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)

        results = []
        for idx, score in indexed_scores[:top_k]:
            if score <= 0:
                break
            doc = dict(self.documents[idx])
            doc["bm25_score"] = float(score)
            doc["source"] = "bm25"
            results.append(doc)

        return results


# ============================================================================
# Cross-Encoder Reranker
# ============================================================================

class CrossEncoderReranker:
    """Cross-encoder reranker using BAAI/BGE-Reranker-V2-M3 via ModelRegistry singleton.

    Delegates model loading to ModelRegistry to ensure the model is loaded
    only once and shared across all requests.
    """

    def __init__(self) -> None:
        self.model_name: str = "BAAI/bge-reranker-v2-m3"

    def _load_model(self) -> Any:
        """Get the singleton reranker model from ModelRegistry."""
        from app.services.model_registry import ModelRegistry
        return ModelRegistry.get_reranker_model()

    def rerank(
        self,
        query: str,
        documents: list[dict[str, Any]],
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        """Rerank documents using BGE-Reranker-V2-M3 scoring.

        Args:
            query: The search query.
            documents: List of candidate documents to rerank.
            top_k: Number of top results to return.

        Returns:
            Reranked list of documents with updated scores.
        """
        if not documents:
            return []

        # Cross-encoder inference (BGE-Reranker-V2-M3) is ~3-6s per candidate
        # pair on CPU; a ~24-candidate batch then takes 70-150s and blows past
        # the nginx 120s timeout (504). On CPU-only deployments (no CUDA), skip
        # the cross-encoder and keep the already RRF-fused / vector-ranked order
        # — the BGE-M3 cosine scores are a strong semantic signal on their own.
        # GPU deployments still get full cross-encoder reranking.
        try:
            import torch
        except Exception:  # pragma: no cover - torch is a hard dependency of FlagEmbedding
            torch = None
        if torch is None or not torch.cuda.is_available():
            return documents[:top_k]

        model = self._load_model()
        if model is None:
            return documents[:top_k]

        # Build (query, document) pairs
        pairs = []
        for doc in documents:
            doc_text = doc.get("content", "")
            law_name = doc.get("law_name", "")
            article_number = doc.get("article_number", "")
            pair_text = f"{law_name} {article_number} {doc_text}"
            pairs.append([query, pair_text])

        try:
            # FlagEmbedding FlagReranker (BGE-Reranker-V2-M3)
            scores = model.compute_score(pairs, normalize=True)
            if isinstance(scores, float):
                scores = [scores]

            for doc, score in zip(documents, scores):
                doc["rerank_score"] = float(score)

            documents.sort(key=lambda x: x.get("rerank_score", 0), reverse=True)
            return documents[:top_k]

        except Exception as exc:
            logger.warning("Reranking failed: %s", exc)
            return documents[:top_k]


# ============================================================================
# Reciprocal Rank Fusion (RRF)
# ============================================================================

def reciprocal_rank_fusion(
    result_lists: list[list[dict[str, Any]]],
    k: int = 60,
) -> list[dict[str, Any]]:
    """Merge multiple ranked result lists using Reciprocal Rank Fusion.

    RRF score = sum(1 / (k + rank_i)) for each result list i.

    Args:
        result_lists: Lists of ranked results from different retrieval strategies.
        k: RRF constant controlling rank emphasis (default: 60).

    Returns:
        Fused and re-ranked list of results.
    """
    if not result_lists:
        return []

    scores: dict[str, tuple[float, dict[str, Any]]] = {}

    for results in result_lists:
        for rank, doc in enumerate(results):
            # Use law_name + article_number as unique key
            doc_id = f"{doc.get('law_name', '')}_{doc.get('article_number', '')}"
            if not doc_id or doc_id == "_":
                doc_id = str(hash(doc.get("content", "")))

            rrf_score = 1.0 / (k + rank + 1)

            if doc_id in scores:
                existing_score, existing_doc = scores[doc_id]
                scores[doc_id] = (
                    existing_score + rrf_score,
                    {**existing_doc, **doc, "rrf_score": existing_score + rrf_score},
                )
            else:
                scores[doc_id] = (rrf_score, {**doc, "rrf_score": rrf_score})

    sorted_items = sorted(scores.values(), key=lambda x: x[0], reverse=True)
    return [doc for _, doc in sorted_items]


# ============================================================================
# Advanced RAG Pipeline
# ============================================================================

class AdvancedRAGPipeline:
    """Full Advanced RAG pipeline: BM25 + Milvus vector + Graph + Cross-Encoder reranking.

    Usage:
        pipeline = AdvancedRAGPipeline()
        pipeline.initialize()  # Load BM25 index + models
        results = pipeline.retrieve("劳动合同到期不续签需要赔偿吗")
    """

    def __init__(self) -> None:
        self._bm25 = BM25Retriever()
        self._reranker = CrossEncoderReranker()
        self._initialized = False
        self._documents: list[dict[str, Any]] = []
        self._graph_builder = None

    def initialize(self) -> bool:
        """Initialize the RAG pipeline: load documents into BM25 index.

        For full imports (>1000 articles in Milvus), loads a sample of
        documents from PostgreSQL for BM25 indexing. The full dataset is
        searched via Milvus vector search. Falls back to seed data if
        the database is not yet populated.
        """
        if self._initialized:
            return True

        try:
            # Try to load from PostgreSQL for full BM25 coverage
            documents = self._load_documents_from_db()

            if not documents:
                # Fallback to seed data
                from app.rag.milvus_service import SEED_LEGAL_ARTICLES
                documents = SEED_LEGAL_ARTICLES
                logger.info("Using seed data for BM25 (no DB data available)")

            self._documents = documents
            self._bm25.add_documents(self._documents)
            self._initialized = True
            logger.info("Advanced RAG pipeline initialized: %d documents indexed", len(self._documents))
            return True
        except Exception as exc:
            logger.error("Failed to initialize RAG pipeline: %s", exc)
            return False

    def _load_documents_from_db(self, limit: int = 10000) -> list[dict[str, Any]]:
        """Load documents from PostgreSQL for BM25 indexing.

        Loads up to `limit` documents, prioritizing important laws.
        For million-scale data, we load a representative sample rather
        than the full set (which would be too large for in-memory BM25).

        Note: This method uses asyncio.run() which will fail if called from
        within an already-running event loop. In that case, it returns [] and
        the caller falls back to seed data. The RAG pipeline should be
        re-initialized via reload() after mass import.
        """
        from sqlalchemy import select, func

        try:
            from app.core.database import async_session_factory
            from app.models.legal_knowledge import Law, LegalArticle

            # Check if we're inside an already-running event loop
            try:
                asyncio.get_running_loop()
                # We ARE in an async context — asyncio.run() would fail.
                # Return empty and let caller fall back to seed data.
                logger.debug("Cannot load from DB inside async context, will use seed data")
                return []
            except RuntimeError:
                pass  # No running loop — safe to use asyncio.run()

            async def _count():
                async with async_session_factory() as session:
                    result = await session.execute(
                        select(func.count()).select_from(LegalArticle)
                    )
                    return result.scalar() or 0

            count = asyncio.run(_count())

            if count < 100:
                return []

            logger.info("Found %d articles in DB, loading %d for BM25 index", count, min(count, limit))

            async def _load():
                async with async_session_factory() as session:
                    result = await session.execute(
                        select(
                            LegalArticle.id,
                            LegalArticle.article_number,
                            LegalArticle.content,
                            LegalArticle.chapter,
                            LegalArticle.tags,
                            LegalArticle.law_id,
                            Law.name,
                            Law.law_type,
                        )
                        .join(Law, LegalArticle.law_id == Law.id)
                        .limit(limit)
                    )
                    docs = []
                    for row in result.all():
                        docs.append({
                            "law_name": row[6],
                            "article_number": row[1],
                            "content": row[2],
                            "chapter": row[3] or "",
                            "tags": row[4] or "",
                            "category": row[7] or "其他",
                        })
                    return docs

            return asyncio.run(_load())

        except Exception as exc:
            logger.warning("Failed to load documents from DB for BM25: %s", exc)
            return []

    def reload(self) -> bool:
        """Reload BM25 index from the database. Call after mass import."""
        self._initialized = False
        self._documents = []
        return self.initialize()

    def retrieve(
        self,
        query: str,
        top_k: int = 5,
        use_bm25: bool = True,
        use_vector: bool = True,
        use_graph: bool = True,
        use_reranker: bool = True,
    ) -> list[dict[str, Any]]:
        """Execute the full hybrid retrieval + reranking pipeline.

        Steps:
        1. BM25 keyword search → top-2K candidates
        2. Milvus vector search → top-2K candidates
        3. Knowledge graph traversal → cross-referenced & same-chapter articles
        4. RRF fusion → merge & deduplicate (1.2x boost for graph-connected results)
        5. Cross-Encoder reranking → final top-K

        Args:
            query: Search query text.
            top_k: Number of final results.
            use_bm25: Enable BM25 keyword search.
            use_vector: Enable Milvus vector search.
            use_graph: Enable knowledge graph retrieval.
            use_reranker: Enable cross-encoder reranking.

        Returns:
            List of ranked documents with scores from all stages.
        """
        if not self._initialized:
            self.initialize()

        candidate_lists: list[list[dict[str, Any]]] = []

        # Stage 1: BM25 keyword retrieval
        if use_bm25:
            bm25_results = self._bm25.search(query, top_k=top_k * 3)
            if bm25_results:
                logger.info("BM25: found %d results (top score: %.3f)",
                           len(bm25_results), bm25_results[0].get("bm25_score", 0))
                candidate_lists.append(bm25_results)

        # Stage 2: Milvus vector retrieval
        if use_vector:
            try:
                from app.rag.milvus_service import get_milvus_rag_service
                service = get_milvus_rag_service()
                service.create_collection()
                vector_results = service.search(query, top_k=top_k * 3)
                if vector_results:
                    logger.info("Milvus vector: found %d results (top score: %.3f)",
                               len(vector_results), vector_results[0].get("score", 0))
                    candidate_lists.append(vector_results)
            except Exception as exc:
                logger.warning("Milvus vector search failed: %s", exc)

        # Stage 3: Knowledge graph retrieval
        if use_graph:
            graph_results = self._graph_retrieve(candidate_lists)
            if graph_results:
                logger.info("Knowledge graph: found %d related articles", len(graph_results))
                candidate_lists.append(graph_results)

        # Fallback: if all fail, return empty
        if not candidate_lists:
            return []

        # Stage 4: Reciprocal Rank Fusion
        if len(candidate_lists) > 1:
            fused = reciprocal_rank_fusion(candidate_lists, k=60)
            logger.info("RRF fusion: %d candidates → %d unique",
                       sum(len(l) for l in candidate_lists), len(fused))
        else:
            fused = candidate_lists[0]

        # Apply 1.2x boost for graph-connected results
        if use_graph:
            graph_ids = set()
            for gr in (candidate_lists[2] if len(candidate_lists) > 2 else []):
                doc_key = f"{gr.get('law_name', '')}_{gr.get('article_number', '')}"
                graph_ids.add(doc_key)
            for doc in fused:
                doc_key = f"{doc.get('law_name', '')}_{doc.get('article_number', '')}"
                if doc_key in graph_ids:
                    doc["rrf_score"] = doc.get("rrf_score", 0) * 1.2
                    doc["graph_boosted"] = True

        # Stage 5: Cross-Encoder reranking
        if use_reranker and len(fused) > top_k:
            reranked = self._reranker.rerank(query, fused, top_k=top_k)
            logger.info("Cross-encoder reranked: %d → %d results", len(fused), len(reranked))
            return reranked

        return fused[:top_k]

    def _graph_retrieve(
        self,
        candidate_lists: list[list[dict[str, Any]]],
    ) -> list[dict[str, Any]]:
        """Use the knowledge graph to find related articles from top results.

        Given the top results from BM25/vector retrieval, traverse the graph
        to find cross-referenced articles, same-chapter articles, and related
        judicial interpretations.

        Args:
            candidate_lists: Current candidate result lists from prior stages.

        Returns:
            Additional candidate documents discovered through graph traversal.
        """
        if not candidate_lists:
            return []

        # Collect article IDs from top results (use law_name + article_number mapping)
        top_docs = []
        for results in candidate_lists:
            top_docs.extend(results[:5])  # top 5 from each source

        graph_results: list[dict[str, Any]] = []
        seen: set[str] = set()

        try:
            if self._graph_builder is None:
                from app.core.database import async_session_factory
                from app.rag.legal_knowledge_graph import LegalKnowledgeGraphBuilder
                self._graph_builder = LegalKnowledgeGraphBuilder(async_session_factory)

            # Get related context via graph neighbors
            # We need article DB IDs; try to match from our indexed documents
            article_ids = self._resolve_article_ids(top_docs)
            if article_ids:
                neighbors = self._graph_builder.get_related_context(article_ids)
                for neighbor in neighbors:
                    if neighbor["type"] == "article":
                        law_name = neighbor.get("label", "")
                        # Try to extract law_name and article_number from the graph node
                        content_preview = neighbor.get("content_preview", "")
                        meta = neighbor.get("metadata", {})
                        doc_key = f"{law_name}_{neighbor.get('id', '')}"
                        if doc_key not in seen:
                            seen.add(doc_key)
                            graph_results.append({
                                "law_name": meta.get("law_name", law_name),
                                "article_number": meta.get("article_number", ""),
                                "content": content_preview,
                                "tags": "",
                                "category": "",
                                "score": 0.5,
                                "source": "knowledge_graph",
                                "graph_relation": neighbor.get("relation", ""),
                            })
        except Exception as exc:
            logger.debug("Graph retrieval failed (non-critical): %s", exc)

        return graph_results

    def _resolve_article_ids(self, docs: list[dict[str, Any]]) -> list[str]:
        """Resolve law_name + article_number to article database IDs.

        Uses the in-memory document index to find matching article IDs.
        """
        ids: list[str] = []
        for doc in docs:
            law_name = doc.get("law_name", "")
            article_number = doc.get("article_number", "")
            if law_name and article_number:
                # Look up in our indexed documents
                for i, indexed_doc in enumerate(self._documents):
                    if (indexed_doc.get("law_name") == law_name and
                            indexed_doc.get("article_number") == article_number):
                        # Use a synthetic ID based on index position
                        ids.append(f"doc_{i}")
                        break
        return ids

    def get_related_context(self, article_ids: list[str]) -> list[dict]:
        """Get graph neighbors for a batch of articles.

        Returns related articles, concepts, and laws connected to the given
        articles through the knowledge graph.

        Args:
            article_ids: List of article database IDs.

        Returns:
            List of dicts with neighbor info (id, type, label, relation, etc.).
        """
        try:
            if self._graph_builder is None:
                from app.core.database import async_session_factory
                from app.rag.legal_knowledge_graph import LegalKnowledgeGraphBuilder
                self._graph_builder = LegalKnowledgeGraphBuilder(async_session_factory)

            return self._graph_builder.get_related_context(article_ids)
        except Exception as exc:
            logger.warning("Failed to get related context: %s", exc)
            return []


# ============================================================================
# Singleton
# ============================================================================

_rag_pipeline: AdvancedRAGPipeline | None = None


def get_rag_pipeline() -> AdvancedRAGPipeline:
    """Return a singleton AdvancedRAGPipeline instance."""
    global _rag_pipeline
    if _rag_pipeline is None:
        _rag_pipeline = AdvancedRAGPipeline()
        _rag_pipeline.initialize()
    return _rag_pipeline
