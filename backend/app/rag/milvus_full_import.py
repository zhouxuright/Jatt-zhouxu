"""法律全文拆分为条文 + 向量化 + 写入 Milvus 的一键导入脚本。

数据来源：
1. data/datasets/hf_laws_*.json — HuggingFace 下载的法律全文（已按条拆分）
2. PostgreSQL legal_articles 表 — 已有的种子法条数据
3. data/datasets/parquet_laws.json — 22,552 部法律的元数据

功能：
- 读取已下载的法律全文 JSON 文件，解析为标准法条格式
- 与 PostgreSQL 中已有的法条去重合并
- 使用 BGE-M3 模型批量生成 embedding
- 写入 Milvus legal_articles 集合
- 支持断点续传（已存在的向量跳过）
- 进度条显示

使用方法：
    # 一键导入所有数据到 Milvus
    python -m app.rag.milvus_full_import

    # 仅导入 HuggingFace 法律全文
    python -m app.rag.milvus_full_import --source hf

    # 仅导入 PostgreSQL 已有法条
    python -m app.rag.milvus_full_import --source pg

    # 仅导入本地种子数据
    python -m app.rag.milvus_full_import --source seed

    # 导入所有来源
    python -m app.rag.milvus_full_import --source all

    # 干跑模式（不实际写入，只统计数据量）
    python -m app.rag.milvus_full_import --dry-run

    # 指定批次大小
    python -m app.rag.milvus_full_import --batch-size 200
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import re
import sys
import time
from pathlib import Path
from typing import Any

# 确保项目根目录在 sys.path 中
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

logger = logging.getLogger(__name__)

DATA_DIR = PROJECT_ROOT / "data"
DATASET_DIR = DATA_DIR / "datasets"

# =========================================================================
# 常量
# =========================================================================

MILVUS_COLLECTION = "legal_articles"
EMBEDDING_DIM = 1024  # BGE-M3
MILVUS_BATCH_SIZE = 500
EMBEDDING_BATCH_SIZE = 64

# 法律名称 → 分类 映射
CATEGORY_MAP = {
    "宪法": "宪法",
    "民法典": "民法", "民法": "民法",
    "刑法": "刑法", "刑事": "刑法",
    "劳动合同法": "劳动法", "劳动法": "劳动法",
    "公司法": "商法",
    "消费者权益保护法": "经济法",
    "著作权法": "知识产权", "专利法": "知识产权", "商标法": "知识产权",
    "行政诉讼法": "诉讼法", "民事诉讼法": "诉讼法", "刑事诉讼法": "诉讼法",
    "行政处罚法": "行政法", "行政许可法": "行政法",
    "个人信息保护法": "行政法", "网络安全法": "行政法", "数据安全法": "行政法",
    "社会保险法": "社会法",
    "反不正当竞争法": "经济法", "反垄断法": "经济法", "电子商务法": "经济法",
    "环境保护法": "行政法", "土地管理法": "经济法",
}


# =========================================================================
# 条文解析器
# =========================================================================

class ArticleParser:
    """将法律全文拆分为标准化的条文列表。"""

    # 匹配 "第一条"、"第二十三条"、"第一千零四十六条" 等
    ARTICLE_PATTERN = re.compile(
        r'(第[一二三四五六七八九十百零千\d]+条[ \s])'
    )

    # 匹配章节标题 "第一编 总则"、"第一章 xxx"
    CHAPTER_PATTERN = re.compile(
        r'^(第[一二三四五六七八九十百零]+[编章节][ \s].+)$',
        re.MULTILINE,
    )

    @staticmethod
    def parse_full_text(title: str, content: str) -> list[dict[str, Any]]:
        """将法律全文拆分为条文列表。

        Args:
            title: 法律名称，如 "中华人民共和国刑法"
            content: 法律全文内容

        Returns:
            条文列表，每条包含 law_name, article_number, content, chapter, category
        """
        articles: list[dict[str, Any]] = []
        category = _guess_category(title)

        # 先尝试按 "第X条" 拆分
        parts = ArticleParser.ARTICLE_PATTERN.split(content)

        if len(parts) <= 1:
            # 无法按条拆分，整段作为一个条目
            cleaned = re.sub(r'\s+', '', content).strip()
            if cleaned and len(cleaned) > 10:
                articles.append({
                    "law_name": title,
                    "article_number": "",
                    "content": cleaned[:8000],
                    "chapter": "",
                    "tags": _guess_tags(title),
                    "category": category,
                })
            return articles

        # 提取章节信息
        current_chapter = ""
        chapter_positions: list[tuple[int, str]] = []
        for match in ArticleParser.CHAPTER_PATTERN.finditer(content):
            chapter_positions.append((match.start(), match.group(1).strip()))

        # 解析每条法条
        for i in range(1, len(parts) - 1, 2):
            article_num = parts[i].strip()
            raw_content = parts[i + 1] if i + 1 < len(parts) else ""

            # 去除章节标题（如果法条内容里混入了下一章标题）
            raw_content = ArticleParser.CHAPTER_PATTERN.sub("", raw_content)

            # 清理空白
            cleaned = re.sub(r'\s+', '', raw_content).strip()

            if not cleaned or len(cleaned) < 5:
                continue

            # 确定所属章节
            article_pos = content.find(parts[i])
            for ch_pos, ch_name in chapter_positions:
                if ch_pos <= article_pos:
                    current_chapter = ch_name
                else:
                    break

            articles.append({
                "law_name": title,
                "article_number": article_num,
                "content": cleaned[:8000],  # Milvus VARCHAR 最大 8192
                "chapter": current_chapter,
                "tags": _guess_tags(title),
                "category": category,
            })

        return articles

    @staticmethod
    def parse_json_file(file_path: Path) -> list[dict[str, Any]]:
        """解析一个法律全文 JSON 文件。

        支持两种格式：
        1. 列表格式：[{"title": "...", "content": "..."}, ...]
        2. 字符串格式：纯文本法律全文
        """
        articles: list[dict[str, Any]] = []

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as exc:
            logger.error("Failed to read %s: %s", file_path, exc)
            return articles

        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    title = item.get("title", item.get("law_name", file_path.stem))
                    content = item.get("content", item.get("text", ""))
                    if content:
                        parsed = ArticleParser.parse_full_text(title, content)
                        articles.extend(parsed)
                elif isinstance(item, str):
                    parsed = ArticleParser.parse_full_text(file_path.stem, item)
                    articles.extend(parsed)

        elif isinstance(data, str):
            parsed = ArticleParser.parse_full_text(file_path.stem, data)
            articles.extend(parsed)

        elif isinstance(data, dict):
            title = data.get("title", file_path.stem)
            content = data.get("content", data.get("text", ""))
            if content:
                parsed = ArticleParser.parse_full_text(title, content)
                articles.extend(parsed)

        return articles


# =========================================================================
# 数据源加载器
# =========================================================================

def load_hf_law_files() -> list[dict[str, Any]]:
    """加载所有 HuggingFace 法律全文 JSON 文件。"""
    articles: list[dict[str, Any]] = []

    if not DATASET_DIR.exists():
        logger.warning("Dataset directory not found: %s", DATASET_DIR)
        return articles

    for file_path in sorted(DATASET_DIR.glob("hf_laws_*.json")):
        parsed = ArticleParser.parse_json_file(file_path)
        logger.info("  %s: %d articles", file_path.name, len(parsed))
        articles.extend(parsed)

    return articles


def load_seed_data() -> list[dict[str, Any]]:
    """加载本地种子法条数据。"""
    from app.rag.knowledge_seed import ARTICLES_DATA

    articles: list[dict[str, Any]] = []
    for item in ARTICLES_DATA:
        law_name = item.get("law", item.get("law_name", ""))
        content = item.get("content", "")
        if not content:
            continue

        articles.append({
            "law_name": law_name,
            "article_number": item.get("num", item.get("article_number", "")),
            "content": re.sub(r'\s+', '', content).strip()[:8000],
            "chapter": item.get("chapter", ""),
            "tags": item.get("tags", ""),
            "category": _guess_category(law_name),
        })

    return articles


async def load_pg_articles() -> list[dict[str, Any]]:
    """从 PostgreSQL legal_articles 表加载已有法条。"""
    from app.core.database import async_session_factory
    from app.models.legal_knowledge import Law, LegalArticle
    from sqlalchemy import select

    articles: list[dict[str, Any]] = []

    async with async_session_factory() as session:
        # 先加载 law_id → law_name 映射
        result = await session.execute(select(Law.id, Law.name, Law.law_type))
        law_map = {row[0]: (row[1], row[2]) for row in result.all()}

        # 加载所有法条
        result = await session.execute(
            select(
                LegalArticle.article_number,
                LegalArticle.content,
                LegalArticle.chapter,
                LegalArticle.tags,
                LegalArticle.law_id,
            )
        )

        for row in result.all():
            art_num, content, chapter, tags, law_id = row
            law_name, law_type = law_map.get(law_id, ("未知法律", "其他"))

            if not content:
                continue

            articles.append({
                "law_name": law_name,
                "article_number": art_num,
                "content": re.sub(r'\s+', '', content).strip()[:8000],
                "chapter": chapter or "",
                "tags": tags or "",
                "category": law_type,
            })

    return articles


# =========================================================================
# 去重
# =========================================================================

def deduplicate(articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """基于内容 hash 去重。"""
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []

    for art in articles:
        # 使用 law_name + article_number + content 前100字作为去重 key
        key = hashlib.md5(
            f"{art['law_name']}|{art['article_number']}|{art['content'][:100]}".encode()
        ).hexdigest()

        if key not in seen:
            seen.add(key)
            unique.append(art)

    return unique


# =========================================================================
# Milvus 写入器
# =========================================================================

class MilvusWriter:
    """向 Milvus 写入法条向量，支持断点续传。"""

    def __init__(self) -> None:
        self._connected = False
        self._collection = None

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

            # 创建集合（如果不存在）
            if not utility.has_collection(MILVUS_COLLECTION):
                fields = [
                    FieldSchema(name="id", dtype=DataType.VARCHAR, is_primary=True, max_length=64),
                    FieldSchema(name="law_name", dtype=DataType.VARCHAR, max_length=256),
                    FieldSchema(name="article_number", dtype=DataType.VARCHAR, max_length=64),
                    FieldSchema(name="content", dtype=DataType.VARCHAR, max_length=8192),
                    FieldSchema(name="tags", dtype=DataType.VARCHAR, max_length=512),
                    FieldSchema(name="category", dtype=DataType.VARCHAR, max_length=64),
                    FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=EMBEDDING_DIM),
                ]
                schema = CollectionSchema(fields=fields, description="Chinese legal articles for RAG")
                collection = Collection(name=MILVUS_COLLECTION, schema=schema)

                # HNSW 索引（比 IVF_FLAT 更适合大规模数据）
                index_params = {
                    "index_type": "HNSW",
                    "metric_type": "COSINE",
                    "params": {"M": 24, "efConstruction": 300},
                }
                collection.create_index(field_name="embedding", index_params=index_params)
                collection.load()
                logger.info("Created Milvus collection '%s' with HNSW index", MILVUS_COLLECTION)
            else:
                self._collection = Collection(MILVUS_COLLECTION)
                self._collection.load()
                logger.info(
                    "Connected to existing collection '%s' (%d entities)",
                    MILVUS_COLLECTION,
                    self._collection.num_entities,
                )

            return True

        except Exception as exc:
            logger.error("Failed to connect to Milvus: %s", exc)
            return False

    def get_existing_ids(self) -> set[str]:
        """获取 Milvus 中已有的 ID 集合（用于断点续传）。"""
        if not self._collection:
            from pymilvus import Collection
            self._collection = Collection(MILVUS_COLLECTION)
            self._collection.load()

        # 查询所有已有 ID
        existing = set()
        try:
            # 用 query 获取所有 ID（分批）
            result = self._collection.query(
                expr="id != ''",
                output_fields=["id"],
                limit=100000,
            )
            for item in result:
                existing.add(item.get("id", ""))
        except Exception:
            # 如果 query 不支持，跳过断点续传检查
            logger.warning("Could not query existing IDs, will skip dedup check")

        return existing

    def generate_embeddings(self, texts: list[str]) -> list[list[float]]:
        """使用 BGE-M3 批量生成 embedding。"""
        from app.services.model_registry import ModelRegistry

        model = ModelRegistry.get_embedding_model()
        all_embeddings: list[list[float]] = []

        for i in range(0, len(texts), EMBEDDING_BATCH_SIZE):
            batch = texts[i : i + EMBEDDING_BATCH_SIZE]
            output = model.encode(batch, return_dense=True)
            batch_embeddings = [e.tolist() for e in output["dense_vecs"]]
            all_embeddings.extend(batch_embeddings)

            # 进度日志
            if i > 0 and (i // EMBEDDING_BATCH_SIZE) % 20 == 0:
                logger.info(
                    "    Embedding: %d / %d (%.1f%%)",
                    i, len(texts), i / len(texts) * 100,
                )

        return all_embeddings

    def write_batch(
        self,
        articles: list[dict[str, Any]],
        start_id: int = 0,
        existing_ids: set[str] | None = None,
    ) -> tuple[int, int]:
        """写入一批法条到 Milvus。

        Returns:
            (inserted_count, skipped_count)
        """
        from pymilvus import Collection

        if not self._collection:
            self._collection = Collection(MILVUS_COLLECTION)
            self._collection.load()

        inserted = 0
        skipped = 0

        for batch_start in range(0, len(articles), MILVUS_BATCH_SIZE):
            batch_end = min(batch_start + MILVUS_BATCH_SIZE, len(articles))
            batch = articles[batch_start:batch_end]

            ids: list[str] = []
            law_names: list[str] = []
            article_numbers: list[str] = []
            contents: list[str] = []
            tags_list: list[str] = []
            categories: list[str] = []
            to_embed: list[str] = []

            for i, art in enumerate(batch):
                art_id = f"art_{start_id + batch_start + i:08d}"

                # 断点续传：跳过已存在的 ID
                if existing_ids and art_id in existing_ids:
                    skipped += 1
                    continue

                ids.append(art_id)
                law_names.append(art["law_name"][:256])
                article_numbers.append(art["article_number"][:64])
                contents.append(art["content"][:8000])
                tags_list.append(art.get("tags", "")[:512])
                categories.append(art.get("category", "其他")[:64])
                to_embed.append(art["content"])

            if not ids:
                continue

            # 生成 embedding
            logger.info(
                "  Generating embeddings for batch %d-%d (%d articles)...",
                batch_start, batch_end, len(ids),
            )
            embeddings = self.generate_embeddings(to_embed)

            # 写入 Milvus
            data = [ids, law_names, article_numbers, contents, tags_list, categories, embeddings]
            try:
                self._collection.insert(data)
                inserted += len(ids)
                logger.info(
                    "  Inserted: %d (total: %d inserted, %d skipped)",
                    len(ids), inserted, skipped,
                )
            except Exception as exc:
                logger.error("  Failed to insert batch: %s", exc)

        # Flush 并重建索引
        self._collection.flush()
        self._collection.load()

        return inserted, skipped


# =========================================================================
# 辅助函数
# =========================================================================

def _guess_category(law_name: str) -> str:
    """根据法律名称猜测分类。"""
    # 先尝试精确匹配
    for key, cat in CATEGORY_MAP.items():
        if key in law_name:
            return cat

    # 更广泛的关键词匹配
    keywords = {
        "宪法": ["宪法", "全国人民代表大会组织", "立法法", "选举法", "国旗", "国歌", "国徽"],
        "民法": ["民法典", "民法", "物权", "合同", "婚姻家庭", "继承", "人格权", "总则",
                 "著作权", "专利", "商标", "知识产权"],
        "刑法": ["刑法", "刑事", "监狱", "社区矫正", "禁毒", "治安管理处罚"],
        "行政法": ["行政处罚", "行政许可", "行政强制", "行政复议", "个人信息", "网络安全",
                  "数据安全", "环境保护", "环境保护", "污染防治", "噪声", "大气", "水污染",
                  "土壤", "放射性", "防沙治沙", "环境影响评价", "海洋", "青藏高原", "黄河",
                  "食品安全", "药品管理", "疫苗", "传染病", "国境卫生", "母婴保健",
                  "精神卫生", "基本医疗卫生", "中医药", "医师", "广告", "红十字会",
                  "海关", "出境入境", "居民身份证", "枪支", "保守国家秘密", "档案",
                  "密码", "国家情报", "反间谍", "人民防空", "国防", "兵役", "军事",
                  "警察", "武装警察", "消防", "应急救援", "对外关系", "境外非政府"],
        "经济法": ["消费者权益", "反不正当竞争", "反垄断", "电子商务", "土地管理",
                  "税法", "财政", "城市房地产", "城乡规划", "测绘", "气象",
                  "科学技术", "科技成果转化", "科学技术普及", "生物安全", "核安全",
                  "粮食", "海岛", "海上交通安全", "港口", "铁路", "民用航空",
                  "公路", "道路交通", "邮政", "电信", "旅游", "电影产业",
                  "文化", "公共图书馆", "公共文化", "非物质文化", "文物",
                  "体育", "教育", "义务教育", "职业教育", "高等教育", "民办教育",
                  "教师", "公务员", "公证", "律师"],
        "社会法": ["劳动法", "劳动合同", "社会保险", "工会", "就业促进",
                  "人口与计划生育", "未成年人", "老年人", "残疾人", "妇女权益",
                  "法律援助", "志愿服务"],
        "商法": ["公司法", "合伙企业", "破产", "票据", "保险法", "海商", "证券"],
        "诉讼法": ["民事诉讼法", "刑事诉讼法", "行政诉讼法", "仲裁", "人民调解"],
        "地方性法规": ["地方", "省", "市", "自治区", "自治州", "自治县"],
        "司法解释": ["司法解释", "最高法", "最高检"],
    }

    for cat, kws in keywords.items():
        for kw in kws:
            if kw in law_name:
                return cat

    return "其他"


def _guess_tags(law_name: str) -> str:
    """根据法律名称生成标签。"""
    short = law_name.replace("中华人民共和国", "")
    category = _guess_category(law_name)
    return f"{category},{short}"


# =========================================================================
# 主流程
# =========================================================================

async def run_full_import(
    source: str = "all",
    batch_size: int = MILVUS_BATCH_SIZE,
    dry_run: bool = False,
) -> dict[str, Any]:
    """运行完整的条文拆分 + 向量化 + Milvus 导入。"""

    results: dict[str, Any] = {
        "sources": {},
        "total_unique": 0,
        "milvus_inserted": 0,
        "milvus_skipped": 0,
    }

    # ---- 阶段 1: 加载数据 ----
    all_articles: list[dict[str, Any]] = []

    if source in ("hf", "all"):
        logger.info("=" * 60)
        logger.info("阶段 1a: 加载 HuggingFace 法律全文")
        logger.info("=" * 60)
        hf_articles = load_hf_law_files()
        results["sources"]["hf_laws"] = len(hf_articles)
        all_articles.extend(hf_articles)
        logger.info("  → %d articles", len(hf_articles))

    if source in ("seed", "all"):
        logger.info("=" * 60)
        logger.info("阶段 1b: 加载本地种子数据")
        logger.info("=" * 60)
        seed_articles = load_seed_data()
        results["sources"]["seed"] = len(seed_articles)
        all_articles.extend(seed_articles)
        logger.info("  → %d articles", len(seed_articles))

    if source in ("pg", "all"):
        logger.info("=" * 60)
        logger.info("阶段 1c: 加载 PostgreSQL 已有法条")
        logger.info("=" * 60)
        try:
            pg_articles = await load_pg_articles()
            results["sources"]["pg"] = len(pg_articles)
            all_articles.extend(pg_articles)
            logger.info("  → %d articles", len(pg_articles))
        except Exception as exc:
            logger.warning("Failed to load from PostgreSQL: %s", exc)
            results["sources"]["pg"] = 0

    total_before_dedup = len(all_articles)
    logger.info("\n总条文数（去重前）: %d", total_before_dedup)

    # ---- 阶段 2: 去重 ----
    logger.info("=" * 60)
    logger.info("阶段 2: 去重")
    logger.info("=" * 60)
    unique_articles = deduplicate(all_articles)
    results["total_unique"] = len(unique_articles)
    logger.info(
        "  去重后: %d 条 (去除了 %d 条重复)",
        len(unique_articles),
        total_before_dedup - len(unique_articles),
    )

    # ---- 阶段 3: 按分类统计 ----
    category_counts: dict[str, int] = {}
    for art in unique_articles:
        cat = art.get("category", "其他")
        category_counts[cat] = category_counts.get(cat, 0) + 1

    logger.info("\n按分类统计:")
    for cat, count in sorted(category_counts.items(), key=lambda x: -x[1]):
        logger.info("  %-15s : %6d", cat, count)

    if dry_run:
        logger.info("\n[DRY RUN] 统计完成，不执行写入")
        return results

    # ---- 阶段 4: 写入 Milvus ----
    logger.info("=" * 60)
    logger.info("阶段 3: 写入 Milvus")
    logger.info("=" * 60)

    writer = MilvusWriter()
    if not writer.connect():
        logger.error("Cannot connect to Milvus. Aborting.")
        results["error"] = "Milvus connection failed"
        return results

    # 获取已有 ID（断点续传）
    logger.info("Checking existing IDs for resume support...")
    existing_ids = writer.get_existing_ids()
    logger.info("  Existing IDs in Milvus: %d", len(existing_ids))

    start_time = time.time()

    inserted, skipped = writer.write_batch(
        unique_articles,
        start_id=len(existing_ids),
        existing_ids=existing_ids,
    )

    elapsed = time.time() - start_time
    results["milvus_inserted"] = inserted
    results["milvus_skipped"] = skipped

    logger.info(
        "\nMilvus import completed in %.1f seconds",
        elapsed,
    )
    logger.info("  Inserted: %d", inserted)
    logger.info("  Skipped (existing): %d", skipped)

    return results


def print_summary(results: dict[str, Any]) -> None:
    """打印导入结果摘要。"""
    print("\n" + "=" * 60)
    print("  法律条文向量化导入 — 结果摘要")
    print("=" * 60)

    print("\n[数据来源]")
    for source, count in results.get("sources", {}).items():
        print(f"  {source:20s} : {count:>8,} 条")

    print(f"\n  {'去重后总计':20s} : {results.get('total_unique', 0):>8,} 条")

    if "milvus_inserted" in results:
        print(f"\n[Milvus]")
        print(f"  {'新增写入':20s} : {results.get('milvus_inserted', 0):>8,} 条")
        print(f"  {'跳过(已存在)':20s} : {results.get('milvus_skipped', 0):>8,} 条")

    if "error" in results:
        print(f"\n  [ERROR] {results['error']}")

    print("\n" + "=" * 60)


# =========================================================================
# CLI
# =========================================================================

def main() -> None:
    parser = argparse.ArgumentParser(description="法律条文向量化一键导入 Milvus")
    parser.add_argument(
        "--source",
        choices=["hf", "seed", "pg", "all"],
        default="all",
        help="数据来源 (default: all)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=MILVUS_BATCH_SIZE,
        help=f"Milvus 写入批次大小 (default: {MILVUS_BATCH_SIZE})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="干跑模式：只统计数据量，不实际写入",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    results = asyncio.run(run_full_import(
        source=args.source,
        batch_size=args.batch_size,
        dry_run=args.dry_run,
    ))

    print_summary(results)


if __name__ == "__main__":
    main()
