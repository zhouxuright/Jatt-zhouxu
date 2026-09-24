"""法律知识库批量数据导入脚本。

支持三种数据源：
1. 本地种子数据（knowledge_seed.py 中已有的 148+ 条法条）
2. JSON / CSV 文件导入（自定义格式的法律数据）
3. 开源数据集下载导入（CnOpenData / ModelScope / ChatLaw 等）

数据同时写入：
- PostgreSQL（laws + legal_articles + court_cases + legal_concepts 表）
- Milvus（legal_articles 向量集合，用于 RAG 检索）

使用方法：
    # 导入本地种子数据
    python -m app.rag.batch_import --source seed

    # 从 JSON 文件导入
    python -m app.rag.batch_import --source json --file ./data/laws.json

    # 从 CSV 文件导入
    python -m app.rag.batch_import --source csv --file ./data/articles.csv

    # 下载并导入开源数据集
    python -m app.rag.batch_import --source opensource --dataset cnopen

    # 全部导入（种子数据 + 开源数据集）
    python -m app.rag.batch_import --source all

    # 仅导入 PostgreSQL（不写 Milvus）
    python -m app.rag.batch_import --source seed --skip-milvus

    # 仅导入 Milvus（不写 PostgreSQL）
    python -m app.rag.batch_import --source seed --skip-postgres
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import logging
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

# 确保项目根目录在 sys.path 中
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

logger = logging.getLogger(__name__)

# =========================================================================
# 配置
# =========================================================================

# 批量插入 Milvus 的批次大小（避免单次插入过多导致内存溢出）
MILVUS_BATCH_SIZE = 500

# 批量插入 PostgreSQL 的批次大小
PG_BATCH_SIZE = 200

# Embedding 生成的批次大小
EMBEDDING_BATCH_SIZE = 64

# 数据目录
DATA_DIR = PROJECT_ROOT / "data"
DATASET_DIR = DATA_DIR / "datasets"


# =========================================================================
# PostgreSQL 导入
# =========================================================================

class PostgreSQLImporter:
    """批量导入数据到 PostgreSQL。"""

    def __init__(self, session_factory: Any) -> None:
        self._session_factory = session_factory
        self._stats = {
            "laws_created": 0,
            "laws_skipped": 0,
            "articles_created": 0,
            "articles_skipped": 0,
            "cases_created": 0,
            "cases_skipped": 0,
            "concepts_created": 0,
            "concepts_skipped": 0,
        }

    async def import_laws(self, laws_data: list[dict[str, Any]]) -> dict[str, int]:
        """导入法律列表到 laws 表。

        Args:
            laws_data: 法律数据列表，每条包含 name, law_type, status 等。

        Returns:
            导入统计。
        """
        from app.models.legal_knowledge import Law
        from sqlalchemy import select

        async with self._session_factory() as session:
            for law_data in laws_data:
                # 检查是否已存在
                result = await session.execute(
                    select(Law).where(Law.name == law_data["name"]).limit(1)
                )
                existing = result.scalars().first()
                if existing:
                    self._stats["laws_skipped"] += 1
                    continue

                law = Law(
                    id=str(uuid.uuid4()),
                    name=law_data["name"],
                    short_name=law_data.get("short_name", ""),
                    law_type=law_data.get("law_type", "其他"),
                    category=law_data.get("category", ""),
                    effective_date=law_data.get("effective_date", ""),
                    status=law_data.get("status", "active"),
                    issuing_authority=law_data.get("issuing_authority", ""),
                    abstract=law_data.get("abstract", ""),
                )
                session.add(law)
                self._stats["laws_created"] += 1

            await session.commit()
            logger.info(
                "Laws imported: %d created, %d skipped",
                self._stats["laws_created"],
                self._stats["laws_skipped"],
            )
        return self._stats

    async def import_articles(
        self,
        articles_data: list[dict[str, Any]],
        laws_map: dict[str, str] | None = None,
    ) -> dict[str, int]:
        """导入法条到 legal_articles 表。

        Args:
            articles_data: 法条数据列表。
            laws_map: 法律名称 → law_id 的映射。如果为 None，会自动查找。

        Returns:
            导入统计。
        """
        from app.models.legal_knowledge import Law, LegalArticle
        from sqlalchemy import select

        if laws_map is None:
            laws_map = await self._build_laws_map()

        async with self._session_factory() as session:
            batch_count = 0
            for art_data in articles_data:
                law_name = art_data.get("law", art_data.get("law_name", ""))
                law_id = laws_map.get(law_name)

                if not law_id:
                    logger.warning("Law not found for article: %s, skipping", law_name)
                    self._stats["articles_skipped"] += 1
                    continue

                # 检查是否已存在（同一法律的同一法条）
                result = await session.execute(
                    select(LegalArticle).where(
                        LegalArticle.law_id == law_id,
                        LegalArticle.article_number == art_data.get("num", art_data.get("article_number", "")),
                    ).limit(1)
                )
                if result.scalars().first():
                    self._stats["articles_skipped"] += 1
                    continue

                article = LegalArticle(
                    id=str(uuid.uuid4()),
                    law_id=law_id,
                    article_number=art_data.get("num", art_data.get("article_number", "")),
                    title=art_data.get("title", ""),
                    content=art_data.get("content", ""),
                    chapter=art_data.get("chapter", ""),
                    section=art_data.get("section", ""),
                    effective_status=art_data.get("effective_status", "active"),
                    tags=art_data.get("tags", ""),
                )
                session.add(article)
                self._stats["articles_created"] += 1
                batch_count += 1

                # 分批提交
                if batch_count >= PG_BATCH_SIZE:
                    await session.commit()
                    batch_count = 0

            await session.commit()
            logger.info(
                "Articles imported: %d created, %d skipped",
                self._stats["articles_created"],
                self._stats["articles_skipped"],
            )
        return self._stats

    async def import_court_cases(self, cases_data: list[dict[str, Any]]) -> dict[str, int]:
        """导入案例到 court_cases 表。"""
        from app.models.legal_knowledge import CourtCase
        from sqlalchemy import select

        async with self._session_factory() as session:
            batch_count = 0
            for case_data in cases_data:
                case_number = case_data.get("case_number", "")
                if not case_number:
                    self._stats["cases_skipped"] += 1
                    continue

                result = await session.execute(
                    select(CourtCase).where(CourtCase.case_number == case_number).limit(1)
                )
                if result.scalars().first():
                    self._stats["cases_skipped"] += 1
                    continue

                case = CourtCase(
                    id=str(uuid.uuid4()),
                    case_number=case_number,
                    title=case_data.get("title", ""),
                    court_name=case_data.get("court_name", ""),
                    case_type=case_data.get("case_type", ""),
                    cause_of_action=case_data.get("cause_of_action", ""),
                    decision_date=case_data.get("decision_date", ""),
                    parties=case_data.get("parties", ""),
                    summary=case_data.get("summary", ""),
                    full_text=case_data.get("full_text", ""),
                    key_points=case_data.get("key_points", ""),
                    referenced_laws=case_data.get("referenced_laws", ""),
                    judgment_result=case_data.get("judgment_result", ""),
                    tags=case_data.get("tags", ""),
                )
                session.add(case)
                self._stats["cases_created"] += 1
                batch_count += 1

                if batch_count >= PG_BATCH_SIZE:
                    await session.commit()
                    batch_count = 0

            await session.commit()
        return self._stats

    async def import_legal_concepts(self, concepts_data: list[dict[str, Any]]) -> dict[str, int]:
        """导入法律概念到 legal_concepts 表。"""
        from app.models.legal_knowledge import LegalConcept
        from sqlalchemy import select

        async with self._session_factory() as session:
            for concept_data in concepts_data:
                name = concept_data.get("name", "")
                if not name:
                    self._stats["concepts_skipped"] += 1
                    continue

                result = await session.execute(
                    select(LegalConcept).where(LegalConcept.name == name).limit(1)
                )
                if result.scalars().first():
                    self._stats["concepts_skipped"] += 1
                    continue

                concept = LegalConcept(
                    id=str(uuid.uuid4()),
                    name=name,
                    definition=concept_data.get("definition", ""),
                    category=concept_data.get("category", ""),
                    related_articles=concept_data.get("related_articles", ""),
                    related_concepts=concept_data.get("related_concepts", ""),
                )
                session.add(concept)
                self._stats["concepts_created"] += 1

            await session.commit()
        return self._stats

    async def _build_laws_map(self) -> dict[str, str]:
        """构建法律名称 → law_id 的映射。"""
        from app.models.legal_knowledge import Law
        from sqlalchemy import select

        async with self._session_factory() as session:
            result = await session.execute(select(Law))
            laws = result.scalars().all()
            return {law.name: law.id for law in laws}

    def get_stats(self) -> dict[str, int]:
        return dict(self._stats)


# =========================================================================
# Milvus 向量导入
# =========================================================================

class MilvusVectorImporter:
    """批量导入向量数据到 Milvus。"""

    def __init__(self) -> None:
        self._connected = False
        self._stats = {
            "vectors_inserted": 0,
            "batches_processed": 0,
            "embeddings_generated": 0,
        }

    def _ensure_connected(self) -> bool:
        """确保已连接到 Milvus。"""
        if self._connected:
            return True
        try:
            from app.core.config import settings
            from pymilvus import connections
            connections.connect(
                alias="default",
                host=settings.MILVUS_HOST,
                port=settings.MILVUS_PORT,
            )
            self._connected = True
            return True
        except Exception as exc:
            logger.error("Milvus connection failed: %s", exc)
            return False

    def _ensure_collection(self) -> bool:
        """确保 legal_articles 集合存在。"""
        from pymilvus import CollectionSchema, DataType, Collection, FieldSchema, utility

        from app.core.config import settings

        from app.rag.milvus_schema import (
            LEGAL_ARTICLE_ARTICLE_NUMBER_MAX,
            LEGAL_ARTICLE_CATEGORY_MAX,
            LEGAL_ARTICLE_CONTENT_MAX,
            LEGAL_ARTICLE_ID_MAX,
            LEGAL_ARTICLE_LAW_NAME_MAX,
            LEGAL_ARTICLE_TAGS_MAX,
            LEGAL_ARTICLES_COLLECTION,
        )

        COLLECTION_NAME = LEGAL_ARTICLES_COLLECTION
        EMBEDDING_DIM = 1024  # BGE-M3

        if utility.has_collection(COLLECTION_NAME):
            return True

        # 字段长度统一取自 milvus_schema：Milvus 按 UTF-8 字节计上限
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
        schema = CollectionSchema(fields=fields, description="Chinese legal articles for RAG")
        collection = Collection(name=COLLECTION_NAME, schema=schema)

        index_params = {"index_type": "IVF_FLAT", "metric_type": "COSINE", "params": {"nlist": 128}}
        collection.create_index(field_name="embedding", index_params=index_params)
        collection.load()

        logger.info("Created Milvus collection 'legal_articles'")
        return True

    def _generate_embeddings(self, texts: list[str]) -> list[list[float]]:
        """使用 BGE-M3 模型生成 embeddings。"""
        from app.services.model_registry import ModelRegistry

        model = ModelRegistry.get_embedding_model()
        all_embeddings = []

        for i in range(0, len(texts), EMBEDDING_BATCH_SIZE):
            batch = texts[i : i + EMBEDDING_BATCH_SIZE]
            output = model.encode(batch, return_dense=True)
            batch_embeddings = [e.tolist() for e in output["dense_vecs"]]
            all_embeddings.extend(batch_embeddings)
            self._stats["embeddings_generated"] += len(batch)

            if (i // EMBEDDING_BATCH_SIZE) % 10 == 0 and i > 0:
                logger.info(
                    "Embedding progress: %d / %d (%.1f%%)",
                    i,
                    len(texts),
                    i / len(texts) * 100,
                )

        return all_embeddings

    def import_articles(
        self,
        articles: list[dict[str, Any]],
        category_map: dict[str, str] | None = None,
    ) -> dict[str, int]:
        """批量导入法条向量到 Milvus。

        Args:
            articles: 法条数据列表。
            category_map: 法律名称 → 分类 的映射。

        Returns:
            导入统计。
        """
        if not self._ensure_connected():
            logger.error("Cannot import: Milvus not connected")
            return self._stats

        if not self._ensure_collection():
            logger.error("Cannot import: Milvus collection creation failed")
            return self._stats

        from pymilvus import Collection

        collection = Collection("legal_articles")
        collection.load()

        if category_map is None:
            category_map = _build_default_category_map()

        # 按批次处理
        total = len(articles)
        logger.info("Starting Milvus import: %d articles", total)
        start_time = time.time()

        for batch_start in range(0, total, MILVUS_BATCH_SIZE):
            batch_end = min(batch_start + MILVUS_BATCH_SIZE, total)
            batch = articles[batch_start:batch_end]

            # 生成 IDs
            ids = [
                a.get("id", f"art_{batch_start + i:06d}")
                for i, a in enumerate(batch)
            ]

            # 提取字段
            law_names = [
                a.get("law", a.get("law_name", ""))
                for a in batch
            ]
            article_numbers = [
                a.get("num", a.get("article_number", ""))
                for a in batch
            ]
            contents = [a.get("content", "") for a in batch]
            tags_list = [a.get("tags", "") for a in batch]
            categories = [
                category_map.get(
                    a.get("law", a.get("law_name", "")),
                    a.get("category", "其他"),
                )
                for a in batch
            ]

            # 生成 embeddings
            logger.info(
                "Generating embeddings for batch %d-%d (%d articles)...",
                batch_start,
                batch_end,
                len(batch),
            )
            embeddings = self._generate_embeddings(contents)

            # 插入 Milvus
            data = [ids, law_names, article_numbers, contents, tags_list, categories, embeddings]
            try:
                collection.insert(data)
                self._stats["vectors_inserted"] += len(batch)
                self._stats["batches_processed"] += 1
                logger.info(
                    "Batch inserted: %d vectors (total: %d / %d)",
                    len(batch),
                    self._stats["vectors_inserted"],
                    total,
                )
            except Exception as exc:
                logger.error("Failed to insert batch %d-%d: %s", batch_start, batch_end, exc)

        # Flush 并加载
        collection.flush()
        collection.load()

        elapsed = time.time() - start_time
        logger.info(
            "Milvus import completed: %d vectors in %.1f seconds (%.1f vectors/sec)",
            self._stats["vectors_inserted"],
            elapsed,
            self._stats["vectors_inserted"] / max(elapsed, 1),
        )

        return self._stats

    def import_court_cases(self, cases: list[dict[str, Any]]) -> dict[str, int]:
        """批量导入案例向量到 Milvus（存入 legal_cases 集合）。"""
        # 简化版：将案例的 summary + key_points 作为向量内容
        if not self._ensure_connected():
            return self._stats

        from pymilvus import Collection, CollectionSchema, DataType, FieldSchema, utility

        COLLECTION_NAME = "legal_cases"
        EMBEDDING_DIM = 1024

        if not utility.has_collection(COLLECTION_NAME):
            fields = [
                FieldSchema(name="id", dtype=DataType.VARCHAR, is_primary=True, max_length=64),
                FieldSchema(name="case_number", dtype=DataType.VARCHAR, max_length=128),
                FieldSchema(name="title", dtype=DataType.VARCHAR, max_length=512),
                FieldSchema(name="content", dtype=DataType.VARCHAR, max_length=8192),
                FieldSchema(name="tags", dtype=DataType.VARCHAR, max_length=512),
                FieldSchema(name="category", dtype=DataType.VARCHAR, max_length=64),
                FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=EMBEDDING_DIM),
            ]
            schema = CollectionSchema(fields=fields, description="Court cases for RAG")
            collection = Collection(name=COLLECTION_NAME, schema=schema)
            index_params = {"index_type": "IVF_FLAT", "metric_type": "COSINE", "params": {"nlist": 128}}
            collection.create_index(field_name="embedding", index_params=index_params)
            collection.load()

        collection = Collection(COLLECTION_NAME)
        collection.load()

        # 准备数据
        for batch_start in range(0, len(cases), MILVUS_BATCH_SIZE):
            batch_end = min(batch_start + MILVUS_BATCH_SIZE, len(cases))
            batch = cases[batch_start:batch_end]

            ids = [f"case_{batch_start + i:06d}" for i in range(len(batch))]
            case_numbers = [c.get("case_number", "") for c in batch]
            titles = [c.get("title", "") for c in batch]
            contents = [
                f"{c.get('summary', '')} {c.get('key_points', '')} {c.get('judgment_result', '')}"
                for c in batch
            ]
            tags_list = [c.get("tags", "") for c in batch]
            categories = [c.get("case_type", "其他") for c in batch]

            embeddings = self._generate_embeddings(contents)

            data = [ids, case_numbers, titles, contents, tags_list, categories, embeddings]
            try:
                collection.insert(data)
                self._stats["vectors_inserted"] += len(batch)
                self._stats["batches_processed"] += 1
            except Exception as exc:
                logger.error("Failed to insert case batch: %s", exc)

        collection.flush()
        collection.load()
        return self._stats

    def get_stats(self) -> dict[str, int]:
        return dict(self._stats)


# =========================================================================
# 数据源处理
# =========================================================================

def _build_default_category_map() -> dict[str, str]:
    """构建法律名称 → 分类的默认映射。"""
    return {
        "中华人民共和国宪法": "宪法",
        "中华人民共和国民法典": "民法",
        "中华人民共和国刑法": "刑法",
        "中华人民共和国劳动合同法": "劳动法",
        "中华人民共和国劳动法": "劳动法",
        "中华人民共和国公司法": "商法",
        "中华人民共和国消费者权益保护法": "经济法",
        "中华人民共和国知识产权法（著作权法）": "知识产权",
        "中华人民共和国专利法": "知识产权",
        "中华人民共和国商标法": "知识产权",
        "中华人民共和国行政诉讼法": "诉讼法",
        "中华人民共和国民事诉讼法": "诉讼法",
        "中华人民共和国刑事诉讼法": "诉讼法",
        "中华人民共和国行政处罚法": "行政法",
        "中华人民共和国行政许可法": "行政法",
        "中华人民共和国社会保险法": "社会法",
        "中华人民共和国反不正当竞争法": "经济法",
        "中华人民共和国反垄断法": "经济法",
        "中华人民共和国数据安全法": "行政法",
        "中华人民共和国个人信息保护法": "行政法",
        "中华人民共和国网络安全法": "行政法",
        "中华人民共和国电子商务法": "经济法",
        "中华人民共和国土地管理法": "经济法",
        "中华人民共和国环境保护法": "行政法",
        # 已废止法律
        "中华人民共和国婚姻法（已废止，并入民法典）": "民法",
        "中华人民共和国继承法（已废止，并入民法典）": "民法",
        "中华人民共和国担保法（已废止，并入民法典）": "民法",
        "中华人民共和国合同法（已废止，并入民法典）": "民法",
        "中华人民共和国侵权责任法（已废止，并入民法典）": "民法",
        "中华人民共和国物权法（已废止，并入民法典）": "民法",
        # 司法解释
        "中华人民共和国婚姻法司法解释（一）": "民法",
    }


def load_seed_data() -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    """从 knowledge_seed.py 加载种子数据。"""
    from app.rag.knowledge_seed import (
        ARTICLES_DATA,
        COURT_CASES_DATA,
        LAWS_DATA,
        LEGAL_CONCEPTS_DATA,
    )

    logger.info(
        "Loaded seed data: %d laws, %d articles, %d cases, %d concepts",
        len(LAWS_DATA),
        len(ARTICLES_DATA),
        len(COURT_CASES_DATA),
        len(LEGAL_CONCEPTS_DATA),
    )
    return LAWS_DATA, ARTICLES_DATA, COURT_CASES_DATA, LEGAL_CONCEPTS_DATA


def load_json_file(file_path: str) -> dict[str, list[dict]]:
    """从 JSON 文件加载数据。

    JSON 格式示例：
    {
        "laws": [...],
        "articles": [...],
        "cases": [...],
        "concepts": [...]
    }
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    result = {
        "laws": data.get("laws", []),
        "articles": data.get("articles", []),
        "cases": data.get("cases", []),
        "concepts": data.get("concepts", []),
    }
    logger.info(
        "Loaded from JSON: %d laws, %d articles, %d cases, %d concepts",
        len(result["laws"]),
        len(result["articles"]),
        len(result["cases"]),
        len(result["concepts"]),
    )
    return result


def load_csv_file(file_path: str) -> list[dict]:
    """从 CSV 文件加载法条数据。

    CSV 格式（表头）：law, num, title, content, tags, category
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    articles = []
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            articles.append(dict(row))

    logger.info("Loaded %d articles from CSV: %s", len(articles), file_path)
    return articles


# =========================================================================
# 开源数据集下载
# =========================================================================

def download_opensource_datasets() -> list[dict[str, Any]]:
    """下载并整合开源法律数据集。

    返回标准化的法条数据列表。
    """
    DATASET_DIR.mkdir(parents=True, exist_ok=True)

    all_articles: list[dict[str, Any]] = []

    # ---- 1. 从 ModelScope 下载中国法律文本数据集 ----
    articles_from_modelscope = _download_modelscope_laws()
    all_articles.extend(articles_from_modelscope)

    # ---- 2. 从本地 data/ 目录扫描 JSON 文件 ----
    articles_from_local = _scan_local_data_files()
    all_articles.extend(articles_from_local)

    logger.info(
        "Total articles from open-source datasets: %d",
        len(all_articles),
    )
    return all_articles


def _download_modelscope_laws() -> list[dict[str, Any]]:
    """尝试从 ModelScope 下载中国法律文本数据集。

    数据集：dengcao/Chinese-Laws
    如果下载失败，返回空列表并提示手动下载。
    """
    cache_file = DATASET_DIR / "modelscope_chinese_laws.json"

    # 如果已下载过，直接读取
    if cache_file.exists():
        logger.info("Loading cached ModelScope dataset: %s", cache_file)
        with open(cache_file, "r", encoding="utf-8") as f:
            return json.load(f)

    try:
        # 尝试使用 modelscope SDK 下载
        from modelscope.msdatasets import MsDataset

        logger.info("Downloading Chinese-Laws dataset from ModelScope...")
        ds = MsDataset.load("dengcao/Chinese-Laws", split="train")

        articles = []
        for item in ds:
            article = {
                "law": item.get("law_name", item.get("title", "")),
                "num": item.get("article_number", item.get("num", "")),
                "content": item.get("content", item.get("text", "")),
                "title": item.get("title", ""),
                "tags": item.get("tags", ""),
                "category": item.get("category", ""),
            }
            if article["content"]:
                articles.append(article)

        # 缓存到本地
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(articles, f, ensure_ascii=False, indent=2)

        logger.info("Downloaded %d articles from ModelScope", len(articles))
        return articles

    except ImportError:
        logger.warning(
            "modelscope SDK not installed. Install with: pip install modelscope\n"
            "Or manually download the dataset and place JSON files in: %s",
            DATASET_DIR,
        )
        return []
    except Exception as exc:
        logger.warning("Failed to download from ModelScope: %s", exc)
        logger.info(
            "You can manually download from: https://www.modelscope.cn/datasets/dengcao/Chinese-Laws\n"
            "And place the JSON file in: %s",
            DATASET_DIR,
        )
        return []


def _scan_local_data_files() -> list[dict[str, Any]]:
    """扫描 data/ 目录下的 JSON/JSONL 文件并加载。"""
    articles: list[dict[str, Any]] = []

    if not DATA_DIR.exists():
        return articles

    for file_path in DATA_DIR.glob("*.json"):
        if file_path.name == "modelscope_chinese_laws.json":
            continue  # 已在上一步处理
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            if isinstance(data, list):
                for item in data:
                    article = _normalize_article(item)
                    if article:
                        articles.append(article)
            elif isinstance(data, dict):
                # 可能是 {"articles": [...]} 或 {"data": [...]}
                for key in ("articles", "data", "laws", "items"):
                    if key in data and isinstance(data[key], list):
                        for item in data[key]:
                            article = _normalize_article(item)
                            if article:
                                articles.append(article)
                        break

            logger.info("Loaded %d articles from: %s", len(articles), file_path.name)
        except Exception as exc:
            logger.warning("Failed to load %s: %s", file_path.name, exc)

    # 扫描 JSONL 文件
    for file_path in DATA_DIR.glob("*.jsonl"):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        item = json.loads(line)
                        article = _normalize_article(item)
                        if article:
                            articles.append(article)
                    except json.JSONDecodeError:
                        continue
            logger.info("Loaded JSONL file: %s", file_path.name)
        except Exception as exc:
            logger.warning("Failed to load %s: %s", file_path.name, exc)

    return articles


def _normalize_article(item: dict[str, Any]) -> dict[str, Any] | None:
    """将各种格式的法律数据标准化为统一格式。"""
    # 尝试从多种可能的字段名中提取内容
    content = (
        item.get("content")
        or item.get("text")
        or item.get("body")
        or item.get("article_content")
        or ""
    )
    if not content:
        return None

    law_name = (
        item.get("law")
        or item.get("law_name")
        or item.get("title")
        or item.get("law_title")
        or ""
    )
    article_num = (
        item.get("num")
        or item.get("article_number")
        or item.get("article_num")
        or item.get("条号")
        or ""
    )
    tags = (
        item.get("tags")
        or item.get("keywords")
        or item.get("标签")
        or ""
    )
    category = (
        item.get("category")
        or item.get("分类")
        or item.get("type")
        or ""
    )
    title = (
        item.get("title")
        or item.get("标题")
        or ""
    )

    return {
        "law": law_name,
        "num": article_num,
        "title": title if title != law_name else "",
        "content": content,
        "tags": tags if isinstance(tags, str) else ",".join(tags) if isinstance(tags, list) else "",
        "category": category,
    }


# =========================================================================
# 主流程
# =========================================================================

async def run_import(
    source: str,
    file_path: str | None = None,
    dataset: str | None = None,
    skip_milvus: bool = False,
    skip_postgres: bool = False,
) -> dict[str, Any]:
    """运行批量导入。

    Args:
        source: 数据源类型 (seed / json / csv / opensource / all)。
        file_path: JSON/CSV 文件路径。
        dataset: 开源数据集名称。
        skip_milvus: 跳过 Milvus 导入。
        skip_postgres: 跳过 PostgreSQL 导入。

    Returns:
        导入统计。
    """
    results: dict[str, Any] = {"source": source, "pg_stats": {}, "milvus_stats": {}}

    # ---- 加载数据 ----
    laws_data: list[dict] = []
    articles_data: list[dict] = []
    cases_data: list[dict] = []
    concepts_data: list[dict] = []

    if source in ("seed", "all"):
        laws_data, articles_data, cases_data, concepts_data = load_seed_data()

    if source == "json" and file_path:
        json_data = load_json_file(file_path)
        laws_data.extend(json_data.get("laws", []))
        articles_data.extend(json_data.get("articles", []))
        cases_data.extend(json_data.get("cases", []))
        concepts_data.extend(json_data.get("concepts", []))

    if source == "csv" and file_path:
        articles_data.extend(load_csv_file(file_path))

    if source in ("opensource", "all"):
        opensource_articles = download_opensource_datasets()
        articles_data.extend(opensource_articles)

    logger.info(
        "Data loaded: %d laws, %d articles, %d cases, %d concepts",
        len(laws_data),
        len(articles_data),
        len(cases_data),
        len(concepts_data),
    )

    if not any([laws_data, articles_data, cases_data, concepts_data]):
        logger.warning("No data to import!")
        return results

    # ---- PostgreSQL 导入 ----
    if not skip_postgres:
        try:
            from app.core.database import async_session_factory

            pg_importer = PostgreSQLImporter(async_session_factory)

            if laws_data:
                logger.info("=== Importing laws to PostgreSQL ===")
                await pg_importer.import_laws(laws_data)

            if articles_data:
                logger.info("=== Importing articles to PostgreSQL ===")
                await pg_importer.import_articles(articles_data)

            if cases_data:
                logger.info("=== Importing court cases to PostgreSQL ===")
                await pg_importer.import_court_cases(cases_data)

            if concepts_data:
                logger.info("=== Importing legal concepts to PostgreSQL ===")
                await pg_importer.import_legal_concepts(concepts_data)

            results["pg_stats"] = pg_importer.get_stats()
            logger.info("PostgreSQL import stats: %s", results["pg_stats"])
        except Exception as exc:
            logger.error("PostgreSQL import failed: %s", exc)
            results["pg_stats"] = {"error": str(exc)}
    else:
        logger.info("Skipping PostgreSQL import (--skip-postgres)")

    # ---- Milvus 导入 ----
    if not skip_milvus:
        try:
            milvus_importer = MilvusVectorImporter()
            category_map = _build_default_category_map()

            if articles_data:
                logger.info("=== Importing article vectors to Milvus ===")
                milvus_importer.import_articles(articles_data, category_map)

            if cases_data:
                logger.info("=== Importing case vectors to Milvus ===")
                milvus_importer.import_court_cases(cases_data)

            results["milvus_stats"] = milvus_importer.get_stats()
            logger.info("Milvus import stats: %s", results["milvus_stats"])
        except Exception as exc:
            logger.error("Milvus import failed: %s", exc)
            results["milvus_stats"] = {"error": str(exc)}
    else:
        logger.info("Skipping Milvus import (--skip-milvus)")

    return results


def print_summary(results: dict[str, Any]) -> None:
    """打印导入结果摘要。"""
    print("\n" + "=" * 60)
    print("  法律知识库批量导入 — 结果摘要")
    print("=" * 60)

    pg = results.get("pg_stats", {})
    if pg and "error" not in pg:
        print("\n[PostgreSQL]")
        print(f"  法律(Laws)     : {pg.get('laws_created', 0)} 条新增, {pg.get('laws_skipped', 0)} 条跳过")
        print(f"  法条(Articles) : {pg.get('articles_created', 0)} 条新增, {pg.get('articles_skipped', 0)} 条跳过")
        print(f"  案例(Cases)    : {pg.get('cases_created', 0)} 条新增, {pg.get('cases_skipped', 0)} 条跳过")
        print(f"  概念(Concepts) : {pg.get('concepts_created', 0)} 条新增, {pg.get('concepts_skipped', 0)} 条跳过")
    elif pg.get("error"):
        print(f"\n[PostgreSQL] 导入失败: {pg['error']}")
    else:
        print("\n[PostgreSQL] 已跳过")

    mv = results.get("milvus_stats", {})
    if mv and "error" not in mv:
        print("\n[Milvus 向量库]")
        print(f"  向量插入       : {mv.get('vectors_inserted', 0)} 条")
        print(f"  批次数         : {mv.get('batches_processed', 0)}")
        print(f"  Embedding 生成 : {mv.get('embeddings_generated', 0)} 条")
    elif mv.get("error"):
        print(f"\n[Milvus] 导入失败: {mv['error']}")
    else:
        print("\n[Milvus] 已跳过")

    print("\n" + "=" * 60)


# =========================================================================
# CLI
# =========================================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description="法律知识库批量数据导入工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python -m app.rag.batch_import --source seed
  python -m app.rag.batch_import --source json --file ./data/laws.json
  python -m app.rag.batch_import --source csv --file ./data/articles.csv
  python -m app.rag.batch_import --source opensource
  python -m app.rag.batch_import --source all
  python -m app.rag.batch_import --source seed --skip-milvus
  python -m app.rag.batch_import --source seed --skip-postgres
        """,
    )
    parser.add_argument(
        "--source",
        choices=["seed", "json", "csv", "opensource", "all"],
        default="seed",
        help="数据源类型 (default: seed)",
    )
    parser.add_argument(
        "--file",
        type=str,
        default=None,
        help="JSON/CSV 文件路径 (用于 --source json 或 csv)",
    )
    parser.add_argument(
        "--dataset",
        type=str,
        default=None,
        help="开源数据集名称 (用于 --source opensource)",
    )
    parser.add_argument(
        "--skip-milvus",
        action="store_true",
        help="跳过 Milvus 向量导入",
    )
    parser.add_argument(
        "--skip-postgres",
        action="store_true",
        help="跳过 PostgreSQL 导入",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="日志级别 (default: INFO)",
    )

    args = parser.parse_args()

    # 配置日志
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 验证参数
    if args.source in ("json", "csv") and not args.file:
        parser.error(f"--source {args.source} 需要指定 --file 参数")

    # 运行导入
    results = asyncio.run(run_import(
        source=args.source,
        file_path=args.file,
        dataset=args.dataset,
        skip_milvus=args.skip_milvus,
        skip_postgres=args.skip_postgres,
    ))

    print_summary(results)


if __name__ == "__main__":
    main()
