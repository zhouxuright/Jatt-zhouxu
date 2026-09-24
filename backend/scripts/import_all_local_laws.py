"""
Import ALL local law data files into PostgreSQL and ChromaDB.

Processes:
1. 117 HuggingFace law files (hf_laws_*.json) — 11,611 entries with full text
2. parquet_laws.json (22,552 law metadata records)

Each HF file is a JSON array where each element has:
  - title: law name (e.g. "中华人民共和国宪法")
  - content: article text (usually one article per entry)

Multiple entries in the same file share the same law title — they are
different articles of the same law.

Usage:
    cd backend
    python scripts/import_all_local_laws.py
"""

import asyncio
import json
import os
import re
import sys
import hashlib
import logging
from datetime import datetime

# ---------------------------------------------------------------------------
# Bootstrap: make app imports work when running from the scripts/ directory
# ---------------------------------------------------------------------------
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.join(_SCRIPT_DIR, '..')
sys.path.insert(0, _PROJECT_ROOT)
os.chdir(_PROJECT_ROOT)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('scripts/import_all_local_laws.log', encoding='utf-8'),
    ],
)
logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(_PROJECT_ROOT, 'data', 'datasets')

# ChromaDB collection name
CHROMA_COLLECTION = "legal_articles"
# Embedding batch size (tune for GPU/CPU memory)
EMBED_BATCH_SIZE = 64
# How often to commit PostgreSQL rows
PG_COMMIT_EVERY = 200


# ===================================================================
# Article-level parsing
# ===================================================================

def extract_article_number(content: str) -> tuple[str, str]:
    """Return (article_number, cleaned_content) from an entry's content.

    The HF data typically has content like:
        "第一条 为了保护个人信息权益..."
    Sometimes the content starts with a chapter header:
        "第一章 总 则\\n第一条 ..."

    Returns:
        ("第一条", "为了保护个人信息权益...")  — or
        ("", <original>) if no article pattern is found
    """
    # Strip HTML artifacts that occasionally appear
    text = re.sub(r'<[^>]+>', '', content)
    text = re.sub(r'&[a-zA-Z]+;', ' ', text)
    text = text.strip()

    # Chinese digit pattern for article markers
    cn = r'[零一二三四五六七八九十百千万\d]+'
    # Try to find "第X条"
    m = re.search(rf'(第{cn}条)', text)
    if m:
        article_num = m.group(1)
        # Content after the article marker (strip the marker itself)
        rest = text[m.end():].strip()
        # Also strip a leading chapter header if it appears before the article
        return article_num, rest if rest else text

    # Fallback: maybe it starts with a chapter header like "第一章 总纲"
    chapter_m = re.match(rf'(第{cn}[编章节])\s*(.*)', text, re.DOTALL)
    if chapter_m:
        return chapter_m.group(1), chapter_m.group(2).strip()

    return '', text


# ===================================================================
# Law type inference
# ===================================================================

_TYPE_KEYWORDS: dict[str, list[str]] = {
    '宪法': ['宪法'],
    '民法': ['民法典', '民事', '物权', '合同', '婚姻', '继承', '侵权', '担保', '人格权'],
    '刑法': ['刑法', '刑事', '刑罚'],
    '行政法': ['行政', '行政处罚', '行政许可', '行政复议', '公务员', '兵役', '国防',
              '治安管理', '出入境', '海关', '档案', '保密', '密码', '对外关系',
              '城乡规划', '土地管理', '房地产'],
    '经济法': ['公司', '企业', '破产', '银行', '保险', '证券', '税收', '财政', '审计',
              '反垄断', '反不正当竞争', '消费者权益', '产品质量', '价格', '计量',
              '广告', '粮食', '邮政', '电信'],
    '社会法': ['劳动', '社会保障', '社会保险', '工会', '安全生产', '职业病', '未成年',
              '妇女权益', '老年', '残疾', '红十字', '公益', '慈善', '就业',
              '退役军人', '预备役'],
    '诉讼法': ['诉讼', '仲裁', '调解', '司法', '监狱', '社区矫正', '法律援助',
              '律师', '公证', '法官', '检察官'],
    '商法': ['票据', '海商', '专利', '商标', '著作权', '知识产权', '电影产业'],
    '环境法': ['环境', '生态', '污染', '水污染', '大气', '土壤', '森林', '草原',
              '野生', '海洋', '海岛', '放射性', '噪声', '防沙', '黄河',
              '青藏高原', '生物安全', '湿地'],
    '教育科技': ['教育', '科技', '科学', '技术', '学校', '教师', '职业',
               '高等教育', '义务教育', '科普', '图书'],
    '卫生': ['卫生', '医疗', '药品', '食品', '疾病', '健康', '母婴', '疫苗',
            '中医药', '医师', '精神卫生'],
    '交通': ['交通', '道路', '铁路', '航空', '海运', '消防'],
    '信息安全': ['网络', '信息', '数据', '安全', '反电信'],
    '军事法': ['军事', '人民武装', '人民防空', '现役军官', '军官', '海警',
              '放射性污染', '气象', '测绘'],
}


def infer_law_type(law_name: str) -> str:
    """Infer law type category from the law name."""
    for law_type, keywords in _TYPE_KEYWORDS.items():
        for kw in keywords:
            if kw in law_name:
                return law_type
    return '其他'


# ===================================================================
# ChromaDB helpers
# ===================================================================

def get_chroma_client():
    import chromadb
    return chromadb.PersistentClient(path=os.path.join(_PROJECT_ROOT, "chroma_data"))


def get_or_create_collection(client):
    return client.get_or_create_collection(
        CHROMA_COLLECTION,
        metadata={"hnsw:space": "cosine"},
    )


def flush_embeddings(pending: list[dict], collection, model, stats: dict):
    """Generate BGE-M3 embeddings and insert into ChromaDB."""
    if not pending:
        return

    # De-duplicate IDs within this batch (keep last occurrence)
    seen_ids: dict[str, dict] = {}
    for p in pending:
        seen_ids[p['id']] = p
    unique_pending = list(seen_ids.values())

    texts = [p['embed_text'] for p in unique_pending]
    output = model.encode(texts, return_dense=True)
    embeddings = output["dense_vecs"].tolist()

    ids = [p['id'] for p in unique_pending]
    metadatas = [p['metadata'] for p in unique_pending]
    documents = texts

    collection.add(
        ids=ids,
        embeddings=embeddings,
        metadatas=metadatas,
        documents=documents,
    )
    added = len(unique_pending)
    stats['embeddings_created'] += added
    dup_count = len(pending) - added
    dup_msg = f" ({dup_count} intra-batch deduped)" if dup_count else ""
    logger.info(
        f"  [ChromaDB] Flushed {added} embeddings{dup_msg}  "
        f"(total in collection: {collection.count()})"
    )


# ===================================================================
# Phase 1: Import HuggingFace law files
# ===================================================================

async def import_hf_laws():
    """Parse all 117 HF law files and import articles into PostgreSQL + ChromaDB."""
    from app.core.database import async_session_factory
    from app.models.legal_knowledge import Law, LegalArticle
    from app.services.model_registry import ModelRegistry
    from sqlalchemy import select

    # ---- Load embedding model (singleton) ----
    logger.info("Loading BGE-M3 embedding model …")
    model = ModelRegistry.get_embedding_model()
    logger.info("BGE-M3 loaded.")

    # ---- ChromaDB ----
    client = get_chroma_client()
    collection = get_or_create_collection(client)
    logger.info(f"ChromaDB collection '{CHROMA_COLLECTION}' ready "
                f"(existing items: {collection.count()})")

    # ---- Discover HF law files ----
    hf_files = sorted(
        f for f in os.listdir(DATA_DIR)
        if f.startswith('hf_laws_') and f.endswith('.json')
    )
    logger.info(f"Found {len(hf_files)} HuggingFace law files in {DATA_DIR}")

    stats = {
        'files_processed': 0,
        'laws_created': 0,
        'laws_existing': 0,
        'articles_created': 0,
        'articles_skipped': 0,
        'embeddings_created': 0,
    }

    pending_embeddings: list[dict] = []

    for file_idx, fname in enumerate(hf_files):
        filepath = os.path.join(DATA_DIR, fname)
        try:
            with open(filepath, 'r', encoding='utf-8') as fh:
                data = json.load(fh)
        except Exception as exc:
            logger.error(f"Failed to read {fname}: {exc}")
            continue

        if not isinstance(data, list):
            data = [data]

        # Group entries by title (same law may have many entries = articles)
        law_groups: dict[str, list[dict]] = {}
        for entry in data:
            title = (entry.get('title') or '').strip()
            content = (entry.get('content') or '').strip()
            if not title or not content:
                continue
            law_groups.setdefault(title, []).append(entry)

        for law_name, entries in law_groups.items():
            law_type = infer_law_type(law_name)

            # --- Upsert Law record ---
            from app.core.database import async_session_factory as sess_factory
            async with sess_factory() as db:
                result = await db.execute(select(Law).where(Law.name == law_name))
                law_record = result.scalars().first()

                if law_record is None:
                    law_record = Law(
                        name=law_name,
                        short_name=law_name[:60] if len(law_name) > 60 else law_name,
                        law_type=law_type,
                        status='active',
                    )
                    db.add(law_record)
                    await db.flush()
                    stats['laws_created'] += 1
                else:
                    stats['laws_existing'] += 1

                law_id = law_record.id

                # --- Process each entry as one article ---
                # Track article numbers seen for this law to handle duplicates
                art_num_counts: dict[str, int] = {}
                for entry in entries:
                    raw_content = (entry.get('content') or '').strip()
                    art_num, cleaned = extract_article_number(raw_content)

                    if not cleaned or len(cleaned) < 5:
                        stats['articles_skipped'] += 1
                        continue

                    # Build a unique article_number by appending a suffix if needed
                    base_num = art_num if art_num else hashlib.md5(
                        cleaned[:200].encode()
                    ).hexdigest()[:12]

                    if base_num in art_num_counts:
                        art_num_counts[base_num] += 1
                        dedup_num = f"{base_num}_{art_num_counts[base_num]}"
                    else:
                        art_num_counts[base_num] = 0
                        dedup_num = base_num

                    # De-duplicate by (law_id, article_number)
                    result = await db.execute(
                        select(LegalArticle).where(
                            LegalArticle.law_id == law_id,
                            LegalArticle.article_number == dedup_num,
                        )
                    )
                    if result.scalars().first():
                        stats['articles_skipped'] += 1
                        continue

                    article_rec = LegalArticle(
                        law_id=law_id,
                        article_number=dedup_num,
                        content=cleaned[:8000],
                        chapter='',
                        tags=f"{law_type},{law_name}",
                    )
                    db.add(article_rec)
                    stats['articles_created'] += 1

                    # Build embedding payload — use content hash to ensure unique IDs
                    embed_text = f"{law_name} {art_num} {cleaned}"
                    embed_id = hashlib.md5(
                        f"{law_name}:{dedup_num}:{hashlib.md5(cleaned[:200].encode()).hexdigest()[:8]}".encode()
                    ).hexdigest()[:16]

                    pending_embeddings.append({
                        'id': f"hf_{embed_id}",
                        'embed_text': embed_text[:8000],
                        'metadata': {
                            'law_name': law_name,
                            'article_number': art_num or dedup_num,
                            'category': law_type,
                            'source': 'huggingface',
                        },
                    })

                await db.commit()

            # Flush embeddings when batch is full
            if len(pending_embeddings) >= EMBED_BATCH_SIZE:
                flush_embeddings(pending_embeddings, collection, model, stats)
                pending_embeddings = []

        # --- Progress logging ---
        stats['files_processed'] += 1
        if (file_idx + 1) % 10 == 0 or (file_idx + 1) == len(hf_files):
            logger.info(
                f"[HF] Progress {file_idx + 1}/{len(hf_files)} files | "
                f"Laws: {stats['laws_created']} new / {stats['laws_existing']} existing | "
                f"Articles: {stats['articles_created']} new / "
                f"{stats['articles_skipped']} skipped | "
                f"Embeddings pending: {len(pending_embeddings)}"
            )

    # Flush any remaining embeddings
    if pending_embeddings:
        flush_embeddings(pending_embeddings, collection, model, stats)
        pending_embeddings = []

    logger.info("=" * 60)
    logger.info("Phase 1 (HuggingFace) complete:")
    logger.info(f"  Files processed : {stats['files_processed']}")
    logger.info(f"  Laws created    : {stats['laws_created']}")
    logger.info(f"  Laws already ex.: {stats['laws_existing']}")
    logger.info(f"  Articles created: {stats['articles_created']}")
    logger.info(f"  Articles skipped: {stats['articles_skipped']}")
    logger.info(f"  Embeddings made : {stats['embeddings_created']}")
    logger.info("=" * 60)
    return stats


# ===================================================================
# Phase 2: Import law metadata from parquet_laws.json
# ===================================================================

async def import_parquet_metadata():
    """Import 22,552 law metadata records (no full text, no embeddings)."""
    from app.core.database import async_session_factory
    from app.models.legal_knowledge import Law
    from sqlalchemy import select

    metadata_path = os.path.join(DATA_DIR, 'parquet_laws.json')
    if not os.path.exists(metadata_path):
        logger.warning("parquet_laws.json not found — skipping metadata import")
        return 0

    with open(metadata_path, 'r', encoding='utf-8') as fh:
        data = json.load(fh)

    laws_data = data.get('laws', [])
    logger.info(f"Processing {len(laws_data)} law metadata records from parquet_laws.json")

    created = 0
    skipped = 0
    async with async_session_factory() as db:
        for idx, item in enumerate(laws_data):
            name = (item.get('name') or item.get('title') or '').strip()
            if not name:
                skipped += 1
                continue

            # Skip if already exists
            result = await db.execute(select(Law).where(Law.name == name))
            if result.scalars().first():
                skipped += 1
            else:
                law = Law(
                    name=name,
                    short_name=(item.get('short_name') or name[:60]),
                    law_type=item.get('law_type') or infer_law_type(name),
                    status=item.get('status') or 'active',
                    effective_date=item.get('effective_date') or '',
                    issuing_authority=item.get('issuing_authority') or '',
                    category=item.get('category') or '',
                    abstract=item.get('abstract') or '',
                )
                db.add(law)
                created += 1

            # Periodic commit to keep memory bounded
            if (idx + 1) % PG_COMMIT_EVERY == 0:
                await db.commit()
                logger.info(
                    f"  [Metadata] {idx + 1}/{len(laws_data)} processed — "
                    f"{created} created, {skipped} skipped"
                )

        # Final commit
        await db.commit()

    logger.info(f"Metadata import done: {created} created, {skipped} skipped/existing")
    return created


# ===================================================================
# Phase 3: Final statistics
# ===================================================================

async def print_final_stats():
    """Print summary counts from PostgreSQL and ChromaDB."""
    from app.core.database import async_session_factory
    from app.models.legal_knowledge import Law, LegalArticle
    from sqlalchemy import select, func

    async with async_session_factory() as db:
        r = await db.execute(select(func.count(Law.id)))
        total_laws = r.scalar()
        r = await db.execute(select(func.count(LegalArticle.id)))
        total_articles = r.scalar()

    client = get_chroma_client()
    try:
        col = client.get_collection(CHROMA_COLLECTION)
        total_embeddings = col.count()
    except Exception:
        total_embeddings = 0

    logger.info("=" * 60)
    logger.info("FINAL DATABASE STATISTICS")
    logger.info("=" * 60)
    logger.info(f"  PostgreSQL — Laws           : {total_laws}")
    logger.info(f"  PostgreSQL — Articles        : {total_articles}")
    logger.info(f"  ChromaDB   — Embeddings      : {total_embeddings}")
    logger.info("=" * 60)
    logger.info("Import complete!")


# ===================================================================
# Main
# ===================================================================

async def main():
    logger.info("=" * 60)
    logger.info("Starting comprehensive local law data import")
    logger.info(f"Data directory: {DATA_DIR}")
    logger.info("=" * 60)

    t0 = datetime.now()

    # Phase 1 — HuggingFace law files (full text + embeddings)
    logger.info("\n--- Phase 1: Import HuggingFace law files (117 files) ---")
    try:
        await import_hf_laws()
    except Exception as exc:
        logger.exception(f"Phase 1 failed: {exc}")

    # Phase 2 — Metadata from parquet_laws.json
    logger.info("\n--- Phase 2: Import law metadata (parquet_laws.json) ---")
    try:
        await import_parquet_metadata()
    except Exception as exc:
        logger.exception(f"Phase 2 failed: {exc}")

    # Phase 3 — Summary
    logger.info("\n--- Phase 3: Final statistics ---")
    await print_final_stats()

    elapsed = datetime.now() - t0
    logger.info(f"Total elapsed time: {elapsed}")


if __name__ == "__main__":
    asyncio.run(main())
