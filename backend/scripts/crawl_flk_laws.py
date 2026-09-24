# -*- coding: utf-8 -*-
"""FLK (国家法律法规数据库) Crawler & Importer.

Crawls the public API at flk.npc.gov.cn to fetch real Chinese legal text,
splits laws into individual articles, and imports them into PostgreSQL
and ChromaDB with BGE-M3 embeddings.

Usage:
    cd backend
    python scripts/crawl_flk_laws.py
"""

import asyncio
import json
import logging
import os
import re
import sys
import time
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
os.chdir(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

import httpx
from sqlalchemy import select, func

from app.core.database import async_session_factory, engine, Base
from app.models.legal_knowledge import Law, LegalArticle
from app.services.model_registry import ModelRegistry

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# FLK API configuration
# ---------------------------------------------------------------------------
FLK_LIST_API = "https://flk.npc.gov.cn/api/list"
FLK_DETAIL_API = "https://flk.npc.gov.cn/api/detail"

# Categories mapped to FLK API codes
CATEGORY_MAP = {
    "宪法": "xianfa",
    "民法商法": "minfa",
    "行政法": "xingzhengfa",
    "经济法": "jingjifa",
    "社会法": "shehuifa",
    "刑法": "xingfa",
    "诉讼与非诉讼程序法": "susongfa",
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://flk.npc.gov.cn/",
    "Origin": "https://flk.npc.gov.cn",
}

MAX_RETRIES = 3
RETRY_DELAY = 2.0  # seconds


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------
async def _request_with_retry(
    session: httpx.AsyncClient,
    url: str,
    params: dict,
    retries: int = MAX_RETRIES,
) -> dict:
    """GET with retry and exponential backoff. Returns parsed JSON or {}."""
    last_exc = None
    for attempt in range(retries):
        try:
            resp = await session.get(
                url, params=params, headers=HEADERS, timeout=30
            )
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 429:
                wait = RETRY_DELAY * (2 ** attempt)
                logger.warning("Rate limited (429), waiting %.1fs", wait)
                await asyncio.sleep(wait)
                continue
            logger.warning("HTTP %s from %s", resp.status_code, url)
        except httpx.ReadTimeout:
            last_exc = "ReadTimeout"
            await asyncio.sleep(RETRY_DELAY * (2 ** attempt))
        except Exception as exc:
            last_exc = str(exc)
            await asyncio.sleep(RETRY_DELAY * (2 ** attempt))
    logger.error("Failed after %d retries: %s", retries, last_exc)
    return {}


# ---------------------------------------------------------------------------
# API calls
# ---------------------------------------------------------------------------
async def fetch_law_list(session: httpx.AsyncClient, category: str, page: int = 1) -> dict:
    """Fetch one page of law listings for a category."""
    params = {
        "sortTr": "fffbrq_s",
        "searchType": "title",
        "searchWord": "",
        "catPul": category,
        "pcodeJie": "",
        "pcodeLei": "",
        "times": "",
        "page": page,
        "pageSize": 20,
    }
    return await _request_with_retry(session, FLK_LIST_API, params)


async def fetch_law_detail(session: httpx.AsyncClient, law_id: str) -> dict:
    """Fetch the full text of a single law."""
    return await _request_with_retry(session, FLK_DETAIL_API, {"id": law_id})


# ---------------------------------------------------------------------------
# Text processing
# ---------------------------------------------------------------------------
def _strip_html(text: str) -> str:
    """Remove HTML tags and entities."""
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'&[a-zA-Z]+;', '', text)
    text = re.sub(r'&#\d+;', '', text)
    return text.strip()


def split_articles(content: str) -> list[dict]:
    """Split law content into individual articles.

    Returns list of {"number": "第X条", "content": "..."} dicts.
    """
    content = _strip_html(content)
    content = re.sub(r'\s+', ' ', content).strip()

    if not content:
        return []

    chinese_nums = '零一二三四五六七八九十百千万'
    pattern = rf'(第[{chinese_nums}\d]+条)'
    parts = re.split(pattern, content)

    articles: list[dict] = []

    # parts looks like: [preamble, "第X条", text, "第Y条", text, ...]
    # Collect any preamble text (before the first article)
    preamble = parts[0].strip() if parts else ""

    i = 1
    while i < len(parts) - 1:
        article_num = parts[i].strip()
        article_text = parts[i + 1].strip() if i + 1 < len(parts) else ""

        # Remove the next article marker from the end of text
        article_text = re.sub(rf'\s*{pattern}\s*$', '', article_text).strip()

        if article_text:
            articles.append({
                "number": article_num,
                "content": article_text,
            })
        i += 2

    # If no articles were found, return the whole content as a single entry
    if not articles and content:
        articles.append({"number": "全文", "content": content[:50000]})

    return articles


def _extract_content_from_detail(detail: dict) -> str:
    """Pull the law body text out of the detail API response.

    The API has returned several different JSON shapes over time;
    we try all known keys.
    """
    for wrapper_key in ("result", "data"):
        wrapper = detail.get(wrapper_key)
        if not isinstance(wrapper, dict):
            continue
        for content_key in ("body", "content", "txt", "html", "fullText"):
            val = wrapper.get(content_key)
            if val:
                return _strip_html(str(val))
    # Some responses put content at top level
    for content_key in ("body", "content", "txt"):
        val = detail.get(content_key)
        if val:
            return _strip_html(str(val))
    return ""


def _extract_law_meta(item: dict) -> tuple[str, str]:
    """Return (title, id) from a list-item dict."""
    title = (
        item.get("title")
        or item.get("lawName")
        or item.get("name")
        or ""
    )
    law_id = (
        item.get("id")
        or item.get("lawId")
        or item.get("strId")
        or ""
    )
    return title, law_id


def _extract_list_from_response(data: dict) -> list[dict]:
    """The list API wraps results differently; normalise to a list."""
    result = data.get("result")
    if isinstance(result, dict):
        inner = result.get("data")
        if isinstance(inner, list):
            return inner
    if isinstance(data.get("data"), list):
        return data["data"]
    return []


# ---------------------------------------------------------------------------
# Database import
# ---------------------------------------------------------------------------
async def _ensure_law(db, title: str, category: str, issuing: str) -> Law:
    """Find or create a Law row."""
    result = await db.execute(select(Law).where(Law.name == title))
    law = result.scalar_one_or_none()
    if law:
        return law

    law = Law(
        name=title,
        short_name=title[:20],
        law_type=category,
        status="active",
        issuing_authority=issuing or "全国人民代表大会",
    )
    db.add(law)
    await db.flush()
    return law


async def _import_articles_to_pg(
    db,
    law: Law,
    articles: list[dict],
    category: str,
    law_title: str,
) -> tuple[list[dict], int]:
    """Insert new articles; return (new_article_dicts, count)."""
    new_arts: list[dict] = []
    for art in articles:
        result = await db.execute(
            select(LegalArticle).where(
                LegalArticle.law_id == law.id,
                LegalArticle.article_number == art["number"],
            )
        )
        if result.scalar_one_or_none():
            continue

        row = LegalArticle(
            law_id=law.id,
            article_number=art["number"],
            content=art["content"],
            tags=f"{category},{law_title}",
        )
        db.add(row)
        new_arts.append(art)

    return new_arts, len(new_arts)


# ---------------------------------------------------------------------------
# ChromaDB embedding
# ---------------------------------------------------------------------------
def _get_chroma_collection():
    import chromadb
    client = chromadb.PersistentClient(path="./chroma_data")
    return client.get_or_create_collection(
        "legal_articles", metadata={"hnsw:space": "cosine"}
    )


def _embed_and_store(
    collection,
    model,
    law_title: str,
    articles: list[dict],
    category: str,
):
    """Generate BGE-M3 embeddings and add to ChromaDB."""
    if not articles:
        return 0

    texts = [f"{law_title} {a['number']} {a['content']}" for a in articles]
    # ChromaDB IDs must be unique; use a deterministic hash-like string
    ids = []
    for a in articles:
        safe_num = a["number"].replace(" ", "_")
        raw_id = f"flk_{law_title}_{safe_num}"
        # ChromaDB ids max 256 chars; truncate if needed
        ids.append(raw_id[:256])

    metadatas = [
        {
            "law_name": law_title[:200],
            "article_number": a["number"][:100],
            "category": category[:50],
        }
        for a in articles
    ]
    # ChromaDB metadata values must be str/int/float/bool
    documents = [t[:30000] for t in texts]

    # Deduplicate ids: if any already exist in the collection, skip them
    # ChromaDB will error on duplicate ids in add(), so use upsert instead
    collection.upsert(
        ids=ids,
        metadatas=metadatas,
        documents=documents,
    )

    # Generate embeddings separately and set them
    BATCH = 32
    for start in range(0, len(articles), BATCH):
        batch_texts = texts[start : start + BATCH]
        batch_ids = ids[start : start + BATCH]
        output = model.encode(batch_texts, return_dense=True)
        embeddings = output["dense_vecs"].tolist()
        collection.upsert(ids=batch_ids, embeddings=embeddings)

    return len(articles)


# ---------------------------------------------------------------------------
# Main crawl loop
# ---------------------------------------------------------------------------
async def main():
    logger.info("=" * 70)
    logger.info("FLK Law Crawler - Starting")
    logger.info("=" * 70)

    # Ensure tables exist
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    collection = _get_chroma_collection()
    model = ModelRegistry.get_embedding_model()

    total_laws = 0
    total_articles = 0
    total_embeddings = 0

    async with httpx.AsyncClient(http2=False) as session:
        for cat_name, cat_code in CATEGORY_MAP.items():
            logger.info("--- Category: %s (code=%s) ---", cat_name, cat_code)
            page = 1
            consecutive_empty = 0

            while True:
                data = await fetch_law_list(session, cat_code, page)
                laws_list = _extract_list_from_response(data)

                if not laws_list:
                    consecutive_empty += 1
                    if consecutive_empty >= 2:
                        break
                    page += 1
                    await asyncio.sleep(0.5)
                    continue

                consecutive_empty = 0

                for law_item in laws_list:
                    law_title, law_id = _extract_law_meta(law_item)
                    if not law_title or not law_id:
                        continue

                    logger.info("  [%s] %s", page, law_title)

                    # Fetch full text
                    await asyncio.sleep(0.3)  # rate limit
                    detail = await fetch_law_detail(session, law_id)
                    content = _extract_content_from_detail(detail)

                    if not content:
                        logger.warning("    No content, skipping")
                        await asyncio.sleep(0.3)
                        continue

                    # Split into articles
                    articles = split_articles(content)
                    if not articles:
                        logger.warning("    No articles after splitting, skipping")
                        await asyncio.sleep(0.3)
                        continue

                    # PostgreSQL import
                    async with async_session_factory() as db_session:
                        issuing = (
                            law_item.get("office")
                            or law_item.get("department")
                            or law_item.get("publishOffice")
                            or ""
                        )
                        law_record = await _ensure_law(
                            db_session, law_title, cat_name, issuing
                        )
                        new_arts, count = await _import_articles_to_pg(
                            db_session, law_record, articles, cat_name, law_title
                        )
                        await db_session.commit()

                    total_laws += 1
                    total_articles += count

                    # ChromaDB embedding (run in thread via model.encode)
                    loop = asyncio.get_running_loop()
                    n_emb = await loop.run_in_executor(
                        None, _embed_and_store,
                        collection, model, law_title, new_arts, cat_name,
                    )
                    total_embeddings += n_emb

                    logger.info("    %d articles, %d new, %d embedded",
                                len(articles), count, n_emb)
                    await asyncio.sleep(0.3)

                page += 1
                await asyncio.sleep(0.5)

    # Final stats
    logger.info("=" * 70)
    logger.info("Import complete")
    logger.info("  Laws processed: %d", total_laws)
    logger.info("  New articles:   %d", total_articles)
    logger.info("  New embeddings: %d", total_embeddings)
    logger.info("=" * 70)

    async with async_session_factory() as db_session:
        r = await db_session.execute(select(func.count(Law.id)))
        logger.info("Total laws in DB:      %d", r.scalar())
        r = await db_session.execute(select(func.count(LegalArticle.id)))
        logger.info("Total articles in DB:  %d", r.scalar())
    logger.info("Total in ChromaDB:     %d", collection.count())


if __name__ == "__main__":
    asyncio.run(main())
