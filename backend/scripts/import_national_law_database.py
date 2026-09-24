# -*- coding: utf-8 -*-
"""Import 22,552 real Chinese laws from the open-source twang2218/law-datasets
GitHub repository into PostgreSQL and ChromaDB.

Dataset: https://github.com/twang2218/law-datasets
File: law-and-regulations/laws.json.zip → laws.json (378 MB, 22,552 laws)

Each law entry has:
  id, title, office, publish, expiry, type, status, url, content

The script:
  1. Reads laws.json
  2. For each law, creates a Law record in PostgreSQL
  3. Splits content into individual articles (法条) by detecting "第X条" patterns
  4. Creates LegalArticle records for each split article
  5. Generates BGE-M3 embeddings and stores in ChromaDB
  6. Supports resuming from interruption (saves progress checkpoint)

Usage:
    cd backend
    python scripts/import_national_law_database.py
"""

import asyncio
import hashlib
import json
import logging
import os
import re
import sys
import time
import traceback
from datetime import datetime
from typing import Any

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
        logging.FileHandler('scripts/import_national_law_database.log', encoding='utf-8'),
    ],
)
logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(_PROJECT_ROOT, 'data', 'datasets')
LAWS_JSON_PATH = os.path.join(DATA_DIR, 'laws.json')
CHECKPOINT_PATH = os.path.join(DATA_DIR, '_import_national_law_checkpoint.json')

# ChromaDB collection name
CHROMA_COLLECTION = "legal_articles"
# Embedding batch size (tune for GPU/CPU memory)
EMBED_BATCH_SIZE = 32
# How often to commit PostgreSQL rows
PG_COMMIT_EVERY = 100
# Progress log interval
PROGRESS_EVERY = 100


# ===================================================================
# Status mapping from Chinese to English
# ===================================================================

_STATUS_MAP = {
    '有效': 'active',
    '已修改': 'amended',
    '已废止': 'repealed',
    '尚未生效': 'pending',
    '7': 'unknown',  # anomalous value in dataset
}


# ===================================================================
# Law type mapping from dataset type field
# ===================================================================

_TYPE_MAP = {
    '宪法': '宪法',
    '法律': '法律',
    '行政法规': '行政法规',
    '地方性法规': '地方性法规',
    '司法解释': '司法解释',
    '法律解释': '法律解释',
    '监察法规': '监察法规',
    '修改、废止的决定': '决定',
    '有关法律问题和重大问题的决定': '决定',
}

# Infer law_type sub-category from the law name
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
    '军事法': ['军事', '人民武装', '人民防空', '现役军官', '军官', '海警', '气象', '测绘'],
}


def infer_category(law_name: str) -> str:
    """Infer a sub-category from the law name."""
    for cat, keywords in _TYPE_KEYWORDS.items():
        for kw in keywords:
            if kw in law_name:
                return cat
    return '其他'


# ===================================================================
# Content cleaning
# ===================================================================

def clean_content(text: str) -> str:
    """Strip markdown formatting, HTML artifacts, and normalise whitespace."""
    # Remove HTML tags
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'&[a-zA-Z]+;', ' ', text)
    # Remove markdown block-quote markers ("> " at start of lines)
    text = re.sub(r'^\s*>\s*', '', text, flags=re.MULTILINE)
    # Remove markdown heading markers
    text = re.sub(r'^#+\s*', '', text, flags=re.MULTILINE)
    # Remove backslash-newline continuations
    text = text.replace('\\\n', '\n')
    # Collapse multiple blank lines
    text = re.sub(r'\n{3,}', '\n\n', text)
    # Remove full-width and half-width whitespace-only lines
    text = re.sub(r'^[ \t　]+$', '', text, flags=re.MULTILINE)
    return text.strip()


# ===================================================================
# Article splitting
# ===================================================================

_CN_DIGITS = r'[零一二三四五六七八九十百千万亿\d]+'


def split_articles(content: str) -> list[dict]:
    """Split law content into individual articles (法条).

    Detects patterns like:
      第一条、第一百二十三条、第1条
    and splits the content at each occurrence.

    Returns a list of dicts: [{'number': '第一条', 'content': '...'}, ...]
    """
    pattern = rf'(第{_CN_DIGITS}条[  　]*)'
    parts = re.split(pattern, content)

    articles = []
    i = 1  # Skip any preamble before first article
    while i < len(parts) - 1:
        article_num = parts[i].strip()
        article_content = parts[i + 1].strip()
        # Remove trailing article markers from content (next article bleeds in)
        article_content = re.sub(
            rf'\s*第{_CN_DIGITS}条\s*$', '', article_content,
        )
        if article_content:
            articles.append({
                'number': article_num,
                'content': article_content,
            })
        i += 2

    return articles


def split_chapters_and_articles(content: str) -> list[dict]:
    """Split content into articles, also tracking chapter/section context.

    Returns list of dicts:
      [{'number': '第一条', 'content': '...', 'chapter': '第一编 总则', 'section': '第一章 基本原则'}, ...]
    """
    cn = _CN_DIGITS
    # Pattern for chapter/section/编 markers
    chapter_pattern = rf'(第{cn}[编章节][  　]*[^\n]*)'

    # First, split into structural sections
    chap_parts = re.split(chapter_pattern, content)

    current_chapter = ''
    current_section = ''
    articles = []

    for part in chap_parts:
        part_stripped = part.strip()
        if not part_stripped:
            continue

        # Check if this part is a chapter/section marker
        marker_match = re.match(rf'^(第{cn}[编章节])', part_stripped)
        if marker_match:
            marker_text = part_stripped
            kind = marker_match.group(1)
            # Determine if it's 编, 章, or 节
            if kind.endswith('编'):
                current_chapter = marker_text
                current_section = ''
            elif kind.endswith('章'):
                current_section = marker_text
            # 节 goes into section too
            elif kind.endswith('节'):
                current_section = marker_text
            continue

        # This part contains article text; split into individual articles
        sub_articles = split_articles(part_stripped)
        for art in sub_articles:
            art['chapter'] = current_chapter
            art['section'] = current_section
            articles.append(art)

    return articles


# ===================================================================
# Checkpoint (resume support)
# ===================================================================

def load_checkpoint() -> dict:
    """Load checkpoint from disk, or return empty state."""
    if os.path.exists(CHECKPOINT_PATH):
        try:
            with open(CHECKPOINT_PATH, 'r', encoding='utf-8') as f:
                data = json.load(f)
            logger.info(f"Loaded checkpoint: last_index={data.get('last_index', 0)}, "
                        f"laws_created={data.get('stats', {}).get('laws_created', 0)}")
            return data
        except Exception as e:
            logger.warning(f"Failed to load checkpoint: {e}")
    return {'last_index': 0, 'stats': {}, 'processed_law_names': []}


def save_checkpoint(last_index: int, stats: dict, processed_names_sample: list[str]):
    """Save checkpoint to disk for resume."""
    data = {
        'last_index': last_index,
        'stats': stats,
        'timestamp': datetime.now().isoformat(),
        # Save last 500 names as a rough set for duplicate detection on resume
        'processed_law_names': processed_names_sample[-500:],
    }
    with open(CHECKPOINT_PATH, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False)


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

    # Generate embeddings in sub-batches to avoid OOM
    all_embeddings = []
    sub_batch = 64
    for i in range(0, len(texts), sub_batch):
        batch_texts = texts[i:i+sub_batch]
        output = model.encode(batch_texts, return_dense=True)
        all_embeddings.extend(output["dense_vecs"].tolist())

    embeddings = all_embeddings
    ids = [p['id'] for p in unique_pending]
    metadatas = [p['metadata'] for p in unique_pending]
    documents = texts

    # ChromaDB limits batch to ~4166 items
    for j in range(0, len(unique_pending), 4000):
        end = min(j + 4000, len(unique_pending))
        collection.add(
            ids=ids[j:end],
            embeddings=embeddings[j:end],
            metadatas=metadatas[j:end],
            documents=documents[j:end],
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
# Main import
# ===================================================================

async def main():
    from app.core.database import async_session_factory, engine, Base
    from app.models.legal_knowledge import Law, LegalArticle
    from app.services.model_registry import ModelRegistry
    from sqlalchemy import select, func

    logger.info("=" * 70)
    logger.info("National Law Database Import")
    logger.info("Source: twang2218/law-datasets (GitHub)")
    logger.info(f"Data file: {LAWS_JSON_PATH}")
    logger.info("=" * 70)

    # ---- Ensure tables exist ----
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # ---- Check data file exists ----
    if not os.path.exists(LAWS_JSON_PATH):
        logger.error(f"Laws JSON not found at {LAWS_JSON_PATH}")
        logger.error("Please download the dataset first. See instructions in the script header.")
        return

    # ---- Load checkpoint ----
    checkpoint = load_checkpoint()
    start_index = checkpoint.get('last_index', 0)
    default_stats = {
        'laws_created': 0,
        'laws_existing': 0,
        'articles_created': 0,
        'articles_skipped': 0,
        'embeddings_created': 0,
        'laws_with_no_articles': 0,
        'parse_errors': 0,
    }
    saved_stats = checkpoint.get('stats', {})
    # Merge saved stats with defaults so all keys are always present
    stats = dict(default_stats)
    stats.update(saved_stats)
    processed_names = set(checkpoint.get('processed_law_names', []))

    # ---- Load the dataset ----
    logger.info("Loading laws.json (this may take a while for 378MB)...")
    t0 = time.time()
    with open(LAWS_JSON_PATH, 'r', encoding='utf-8') as f:
        laws_data = json.load(f)
    load_time = time.time() - t0
    total_count = len(laws_data)
    logger.info(f"Loaded {total_count} laws in {load_time:.1f}s")

    if start_index > 0:
        logger.info(f"Resuming from index {start_index} (checkpoint)")

    # ---- Load embedding model ----
    logger.info("Loading BGE-M3 embedding model...")
    model = ModelRegistry.get_embedding_model()
    logger.info("BGE-M3 loaded.")

    # ---- ChromaDB ----
    client = get_chroma_client()
    collection = get_or_create_collection(client)
    logger.info(f"ChromaDB collection '{CHROMA_COLLECTION}' ready "
                f"(existing items: {collection.count()})")

    pending_embeddings: list[dict] = []
    recent_names: list[str] = []

    t_start = time.time()

    for idx in range(start_index, total_count):
        item = laws_data[idx]
        raw_title = (item.get('title') or '').strip()
        raw_content = (item.get('content') or '').strip()
        raw_office = (item.get('office') or '').strip()
        raw_type = (item.get('type') or '').strip()
        raw_status = (item.get('status') or '').strip()
        raw_publish = (item.get('publish') or '').strip()
        raw_expiry = (item.get('expiry') or '').strip()

        if not raw_title:
            stats['parse_errors'] += 1
            continue

        # Skip if we already processed this law name (resume de-dup)
        if raw_title in processed_names:
            stats['laws_existing'] += 1
            continue

        # Clean content
        content = clean_content(raw_content)
        if not content or len(content) < 10:
            stats['parse_errors'] += 1
            continue

        # Map status
        status = _STATUS_MAP.get(raw_status, 'active')

        # Map law_type from dataset type field
        law_type = _TYPE_MAP.get(raw_type, '其他')
        # Infer sub-category
        category = infer_category(raw_title)

        # Parse effective date
        effective_date = ''
        if raw_publish and raw_publish != '0001-01-01 00:00:00':
            effective_date = raw_publish.split(' ')[0] if ' ' in raw_publish else raw_publish

        # Split content into articles
        articles = split_chapters_and_articles(content)

        # If no articles found, treat the entire content as a single article
        if not articles:
            articles = [{
                'number': '',
                'content': content,
                'chapter': '',
                'section': '',
            }]
            stats['laws_with_no_articles'] += 1

        # ---- Import to PostgreSQL ----
        try:
            async with async_session_factory() as db:
                # Check if law already exists by name
                result = await db.execute(select(Law).where(Law.name == raw_title))
                law_record = result.scalars().first()

                if law_record is None:
                    # Build short_name (truncate if too long)
                    short_name = raw_title[:60] if len(raw_title) > 60 else raw_title

                    law_record = Law(
                        name=raw_title,
                        short_name=short_name,
                        law_type=law_type,
                        category=category,
                        effective_date=effective_date,
                        status=status,
                        issuing_authority=raw_office,
                        abstract=content[:500] if content else '',
                    )
                    db.add(law_record)
                    await db.flush()
                    stats['laws_created'] += 1
                else:
                    stats['laws_existing'] += 1

                law_id = law_record.id

                # Track article numbers to handle duplicates within the same law
                art_num_counts: dict[str, int] = {}

                for art in articles:
                    art_num = art.get('number', '').strip()
                    art_content = art.get('content', '').strip()
                    art_chapter = art.get('chapter', '').strip()
                    art_section = art.get('section', '').strip()

                    if not art_content or len(art_content) < 5:
                        stats['articles_skipped'] += 1
                        continue

                    # Build unique article_number
                    base_num = art_num if art_num else hashlib.md5(
                        art_content[:200].encode()
                    ).hexdigest()[:12]

                    if base_num in art_num_counts:
                        art_num_counts[base_num] += 1
                        dedup_num = f"{base_num}_{art_num_counts[base_num]}"
                    else:
                        art_num_counts[base_num] = 0
                        dedup_num = base_num

                    # Check for existing article (law_id + article_number)
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
                        content=art_content[:8000],
                        chapter=art_chapter,
                        section=art_section,
                        effective_status=status,
                        tags=f"{law_type},{category},{raw_office}",
                    )
                    db.add(article_rec)
                    stats['articles_created'] += 1

                    # Build embedding payload
                    embed_text = f"{raw_title} {art_num} {art_content}"
                    # Unique ID based on law title + article number
                    embed_id = hashlib.md5(
                        f"national:{raw_title}:{dedup_num}".encode()
                    ).hexdigest()[:16]

                    # Truncate metadata values to ChromaDB limits
                    meta_law_name = raw_title[:120]
                    meta_content = art_content[:500]

                    pending_embeddings.append({
                        'id': f"nl_{embed_id}",
                        'embed_text': embed_text[:8000],
                        'metadata': {
                            'law_name': meta_law_name,
                            'article_number': art_num or dedup_num,
                            'category': category,
                            'law_type': law_type,
                            'status': status,
                            'source': 'national_law_dataset',
                            'issuing_authority': raw_office[:120] if raw_office else '',
                            'content': meta_content,
                        },
                    })

                await db.commit()

        except Exception as e:
            logger.error(f"Error processing law at index {idx} ({raw_title}): {e}")
            traceback.print_exc()
            stats['parse_errors'] += 1

        processed_names.add(raw_title)
        recent_names.append(raw_title)

        # ---- Flush embeddings when batch is full ----
        if len(pending_embeddings) >= EMBED_BATCH_SIZE:
            flush_embeddings(pending_embeddings, collection, model, stats)
            pending_embeddings = []

        # ---- Progress logging ----
        if (idx + 1) % PROGRESS_EVERY == 0 or (idx + 1) == total_count:
            elapsed = time.time() - t_start
            rate = (idx + 1 - start_index) / elapsed if elapsed > 0 else 0
            eta = (total_count - idx - 1) / rate / 60 if rate > 0 else 0
            logger.info(
                f"Progress {idx + 1}/{total_count} ({(idx+1)/total_count*100:.1f}%) | "
                f"Laws: {stats['laws_created']} new / {stats['laws_existing']} existing | "
                f"Articles: {stats['articles_created']} new / {stats['articles_skipped']} skipped | "
                f"Embed pending: {len(pending_embeddings)} | "
                f"Rate: {rate:.1f} laws/s | ETA: {eta:.1f} min"
            )

        # ---- Periodic checkpoint ----
        if (idx + 1) % 500 == 0:
            save_checkpoint(idx + 1, stats, recent_names)

    # ---- Flush remaining embeddings ----
    if pending_embeddings:
        flush_embeddings(pending_embeddings, collection, model, stats)
        pending_embeddings = []

    # ---- Final checkpoint ----
    save_checkpoint(total_count, stats, recent_names)

    # ---- Final statistics ----
    elapsed_total = time.time() - t_start
    logger.info("=" * 70)
    logger.info("IMPORT COMPLETE")
    logger.info("=" * 70)
    logger.info(f"  Total laws in dataset    : {total_count}")
    logger.info(f"  Laws created (new)       : {stats['laws_created']}")
    logger.info(f"  Laws already existing    : {stats['laws_existing']}")
    logger.info(f"  Articles created (new)   : {stats['articles_created']}")
    logger.info(f"  Articles skipped         : {stats['articles_skipped']}")
    logger.info(f"  Embeddings created       : {stats['embeddings_created']}")
    logger.info(f"  Laws with no articles    : {stats['laws_with_no_articles']}")
    logger.info(f"  Parse errors             : {stats['parse_errors']}")
    logger.info(f"  Total elapsed time       : {elapsed_total/60:.1f} minutes")
    logger.info("=" * 70)

    # ---- Database stats ----
    try:
        async with async_session_factory() as db:
            r = await db.execute(select(func.count(Law.id)))
            total_laws = r.scalar()
            r = await db.execute(select(func.count(LegalArticle.id)))
            total_articles = r.scalar()

        logger.info(f"  PostgreSQL — Total Laws       : {total_laws}")
        logger.info(f"  PostgreSQL — Total Articles   : {total_articles}")
        logger.info(f"  ChromaDB   — Total Embeddings : {collection.count()}")
    except Exception as e:
        logger.warning(f"Could not query final stats: {e}")

    logger.info("=" * 70)
    logger.info("Done!")


if __name__ == "__main__":
    asyncio.run(main())
