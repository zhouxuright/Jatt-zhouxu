"""优化的 Milvus 向量导入器。

使用 BGE-M3 (1024 维) 生成嵌入向量，HNSW 索引，
支持断点续传和批量写入。
"""

from __future__ import annotations

import logging
import time
from typing import Any

from app.rag.mass_import.progress import ImportProgress
from app.rag.milvus_schema import (
    LEGAL_ARTICLE_BYTE_LIMITS,
    LEGAL_ARTICLES_COLLECTION,
    truncate_utf8,
)

logger = logging.getLogger(__name__)

# 常量（集合名收敛到 app.rag.milvus_schema，避免多处硬编码漂移）
MILVUS_COLLECTION = LEGAL_ARTICLES_COLLECTION
EMBEDDING_DIM = 1024  # BGE-M3
MILVUS_BATCH_SIZE = 500
EMBED_BATCH_SIZE = 64

#: 参与业务键匹配、不应被截断的字段（截断后与 PostgreSQL 侧对不上）
_KEY_FIELDS = frozenset({"law_name", "article_number"})

# 兼容旧调用名（实现收敛到 app.rag.milvus_schema.truncate_utf8）
_truncate_utf8 = truncate_utf8


class OptimizedMilvusImporter:
    """高性能 Milvus 向量导入器。"""

    def __init__(self) -> None:
        self._connected = False
        self._collection = None
        self._limits: dict[str, int] | None = None
        self._stats = {
            "vectors_inserted": 0,
            "embeddings_generated": 0,
            "batches_processed": 0,
        }

    def _field_limits(self) -> dict[str, int]:
        """从**实际集合 schema** 读取 varchar 字节上限。

        不从 milvus_schema 常量直接取：已存在的集合可能建于旧上限（如
        law_name=256B），按其真实上限截断才不会整批写入失败；新建集合则天然
        等于常量值。这样"建表值"与"写入值"不会漂移。
        """
        if self._limits is None:
            limits: dict[str, int] = {}
            if self._collection is not None:
                for field in self._collection.schema.fields:
                    params = getattr(field, "params", None) or {}
                    if "max_length" in params:
                        limits[field.name] = int(params["max_length"])
            self._limits = limits or dict(LEGAL_ARTICLE_BYTE_LIMITS)
        return self._limits

    def connect(self) -> bool:
        """连接 Milvus 并确保集合存在。"""
        try:
            from pymilvus import (
                Collection, CollectionSchema, DataType,
                FieldSchema, connections, utility,
            )
            from app.core.config import settings

            connections.connect(
                alias="default",
                host=settings.MILVUS_HOST,
                port=settings.MILVUS_PORT,
            )
            self._connected = True

            if not utility.has_collection(MILVUS_COLLECTION):
                # 字段长度统一取自 milvus_schema：Milvus 按 UTF-8 字节计上限，
                # 中文 1 字 = 3 字节，硬编码易踩坑（详见该模块文档）。
                fields = [
                    FieldSchema(
                        name="id", dtype=DataType.VARCHAR, is_primary=True,
                        max_length=LEGAL_ARTICLE_ID_MAX,
                    ),
                    FieldSchema(
                        name="law_name", dtype=DataType.VARCHAR,
                        max_length=LEGAL_ARTICLE_LAW_NAME_MAX,
                    ),
                    FieldSchema(
                        name="article_number", dtype=DataType.VARCHAR,
                        max_length=LEGAL_ARTICLE_ARTICLE_NUMBER_MAX,
                    ),
                    FieldSchema(
                        name="content", dtype=DataType.VARCHAR,
                        max_length=LEGAL_ARTICLE_CONTENT_MAX,
                    ),
                    FieldSchema(
                        name="tags", dtype=DataType.VARCHAR,
                        max_length=LEGAL_ARTICLE_TAGS_MAX,
                    ),
                    FieldSchema(
                        name="category", dtype=DataType.VARCHAR,
                        max_length=LEGAL_ARTICLE_CATEGORY_MAX,
                    ),
                    FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=EMBEDDING_DIM),
                ]
                schema = CollectionSchema(
                    fields=fields,
                    description="Chinese legal articles for RAG (full import)"
                )
                collection = Collection(name=MILVUS_COLLECTION, schema=schema)

                # IVF_FLAT 索引（与运行时 milvus_service.search 的 nprobe 检索方式兼容）
                index_params = {
                    "index_type": "IVF_FLAT",
                    "metric_type": "COSINE",
                    "params": {"nlist": 2048},
                }
                collection.create_index(
                    field_name="embedding",
                    index_params=index_params,
                )
                logger.info("Created Milvus collection '%s' with IVF_FLAT index (nlist=2048)", MILVUS_COLLECTION)
            else:
                self._collection = Collection(MILVUS_COLLECTION)
                self._collection.load()
                logger.info("Connected to '%s' (%d entities)",
                             MILVUS_COLLECTION, self._collection.num_entities)

            return True

        except Exception as exc:
            logger.error("Failed to connect to Milvus: %s", exc)
            return False

    def count_entities(self) -> int:
        """返回 collection 当前实体数（用于 resume 偏移计算）。"""
        if not self._collection:
            from pymilvus import Collection
            self._collection = Collection(MILVUS_COLLECTION)
            self._collection.load()
        return self._collection.num_entities

    def get_existing_ids(self) -> set[str]:
        """分页获取已有 ID 集合（Milvus query 单次窗口上限 16384）。"""
        if not self._collection:
            from pymilvus import Collection
            self._collection = Collection(MILVUS_COLLECTION)
            self._collection.load()

        existing: set[str] = set()
        page_size = 8000
        offset = 0
        try:
            while True:
                result = self._collection.query(
                    expr="id != ''",
                    output_fields=["id"],
                    limit=page_size,
                    offset=offset,
                )
                if not result:
                    break
                for item in result:
                    existing.add(item.get("id", ""))
                offset += len(result)
                if len(result) < page_size:
                    break
            logger.info("Found %d existing IDs in Milvus", len(existing))
        except Exception as exc:
            logger.warning("Could not query existing IDs: %s", exc)

        return existing

    def _generate_embeddings(self, texts: list[str]) -> list[list[float]]:
        """使用 BGE-M3 批量生成 embedding。"""
        from app.services.model_registry import ModelRegistry

        model = ModelRegistry.get_embedding_model()
        all_embeddings: list[list[float]] = []

        for i in range(0, len(texts), EMBED_BATCH_SIZE):
            batch = texts[i:i + EMBED_BATCH_SIZE]
            output = model.encode(batch, return_dense=True)
            batch_vecs = [e.tolist() for e in output["dense_vecs"]]
            all_embeddings.extend(batch_vecs)
            self._stats["embeddings_generated"] += len(batch)

        return all_embeddings

    def import_articles(
        self,
        articles: list[dict[str, Any]],
        start_id: int = 0,
        existing_ids: set[str] | None = None,
    ) -> dict[str, int]:
        """批量导入条文向量到 Milvus。

        Args:
            articles: 去重后的条文列表
            start_id: 已导入的条文数量（resume 偏移），从 articles[start_id] 继续
            existing_ids: 保留参数（已弃用，供给定 start_id 的向量直接续传）

        Returns:
            导入统计
        """
        if not self._connected:
            if not self.connect():
                logger.error("Cannot connect to Milvus")
                return self._stats

        from pymilvus import Collection

        if not self._collection:
            self._collection = Collection(MILVUS_COLLECTION)
            self._collection.load()

        total = len(articles)
        if start_id >= total:
            logger.info("All %d articles already imported (start_id=%d)", total, start_id)
            return self._stats
        logger.info("Starting Milvus import: %d articles (resume from %d)", total, start_id)
        progress = ImportProgress(total, "milvus_import")
        # 让进度条从 start_id 起算，避免续传时进度回退
        for _ in range(0, start_id, MILVUS_BATCH_SIZE):
            progress.update(min(MILVUS_BATCH_SIZE, start_id))

        for batch_start in range(start_id, total, MILVUS_BATCH_SIZE):
            batch_end = min(batch_start + MILVUS_BATCH_SIZE, total)
            batch = articles[batch_start:batch_end]

            ids: list[str] = []
            law_names: list[str] = []
            article_numbers: list[str] = []
            contents: list[str] = []
            tags_list: list[str] = []
            categories: list[str] = []
            to_embed: list[str] = []

            limits = self._field_limits()

            for i, art in enumerate(batch):
                art_id = f"art_{batch_start + i:08d}"

                ids.append(art_id)

                # 键字段（law_name / article_number）截断会与 PostgreSQL 侧对不上，
                # 使该条永远被判为"缺失"。这里显式告警，便于运维发现并放宽 schema。
                for key_field in _KEY_FIELDS:
                    raw = art.get(key_field, "")
                    if len(str(raw).encode("utf-8")) > limits.get(key_field, 0):
                        self._stats.setdefault("key_field_truncated", 0)
                        self._stats["key_field_truncated"] += 1
                        logger.warning(
                            "字段 %s 超出集合上限(%dB)，已截断；该条法条的业务键将与权威库不一致: %s",
                            key_field, limits.get(key_field, 0), str(raw)[:40],
                        )

                law_names.append(_truncate_utf8(art.get("law_name", ""), limits["law_name"]))
                article_numbers.append(
                    _truncate_utf8(art.get("article_number", ""), limits["article_number"])
                )
                contents.append(_truncate_utf8(art.get("content", ""), limits["content"]))
                tags_list.append(_truncate_utf8(art.get("tags", ""), limits["tags"]))
                categories.append(
                    _truncate_utf8(art.get("category", "其他"), limits["category"])
                )
                to_embed.append(art.get("content", ""))

            if not ids:
                progress.update(len(batch))
                continue

            # 生成 embedding
            try:
                embeddings = self._generate_embeddings(to_embed)
            except Exception as exc:
                logger.error("Embedding generation failed at batch %d: %s", batch_start, exc)
                progress.update(len(batch))
                continue

            # 写入 Milvus
            data = [ids, law_names, article_numbers, contents, tags_list, categories, embeddings]
            try:
                self._collection.insert(data)
                self._stats["vectors_inserted"] += len(ids)
                self._stats["batches_processed"] += 1
            except Exception as exc:
                logger.error("Milvus insert failed at batch %d: %s", batch_start, exc)

            progress.update(len(batch))

        # Flush 并重建索引
        try:
            self._collection.flush()
            self._collection.load()
            logger.info("Milvus flush complete. Total entities: %d",
                         self._collection.num_entities)
        except Exception as exc:
            logger.error("Milvus flush failed: %s", exc)

        progress.finish()
        logger.info("Milvus import done: %d vectors inserted, %d embeddings generated",
                     self._stats["vectors_inserted"],
                     self._stats["embeddings_generated"])

        return self._stats

    def get_stats(self) -> dict[str, int]:
        return dict(self._stats)
