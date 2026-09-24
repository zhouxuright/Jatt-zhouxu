# -*- coding: utf-8 -*-
"""Import authoritative Chinese law/regulation full text into PostgreSQL.

Source: ``laws.json.zip`` (国家法律法规数据库 snapshot, ~22.5k documents with
full text).  For each document this importer

  1. upserts a row into ``laws`` (deduplicated by name + effective date), and
  2. splits the full text into individual articles (第X条) and inserts them
     into ``legal_articles``, tracking 章/节 context.

Why this matters: every record produced here is **real, authoritative and
citable** — unlike the template-generated synthetic corpus, it can safely be
surfaced to users as legal authority.  This is the only legitimate way to grow
the citation-grade corpus without buying a commercial data licence.

The script is idempotent (``ON CONFLICT DO NOTHING``) and therefore safe to
re-run; it also supports ``--limit`` for a bounded smoke run.

Usage (inside the backend container):

    python scripts/import_law_fulltext.py --zip /data/laws.json.zip
    python scripts/import_law_fulltext.py --zip /data/laws.json.zip --limit 200
    python scripts/import_law_fulltext.py --zip /data/laws.json.zip --dry-run
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
import time
import uuid
import zipfile

import asyncpg

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("import_law_fulltext")

DEFAULT_DSN = os.environ.get(
    "DSN",
    "postgresql://postgres:postgres@postgres:5432/legal_assistant",
)

# 第X条 / 第X条之一 / 第X条之X
ARTICLE_RE = re.compile(r"^\s*(第[〇零一二三四五六七八九十百千万两0-9]+条(?:之[〇零一二三四五六七八九十]+)?)")
# 第X章 / 第X节 / 第X编
CHAPTER_RE = re.compile(r"^\s*(第[〇零一二三四五六七八九十百千0-9]+[章编])(.*)$")
SECTION_RE = re.compile(r"^\s*(第[〇零一二三四五六七八九十百千0-9]+节)(.*)$")

ARTIFACT_LINE_RE = re.compile(r"^\s*(?:page\s*\d+|\d+\s*/\s*\d+)\s*$", re.IGNORECASE)


def short_name_of(title: str) -> str:
    """Derive a short name from a law title."""
    t = title.strip()
    # Drop the trailing parenthesised amendment marker
    t = re.sub(r"[（(][^）)]*[）)]\s*$", "", t).strip()
    return t[:256] or title[:256]


def normalize_date(raw: str | None) -> str | None:
    if not raw:
        return None
    s = str(raw).strip()
    m = re.search(r"(\d{4})[-/年.](\d{1,2})[-/月.](\d{1,2})", s)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = re.search(r"(\d{4})", s)
    if m:
        return f"{int(m.group(1)):04d}-01-01"
    return s[:32]


def split_articles(content: str) -> list[dict[str, str | None]]:
    """Split a document's full text into article records.

    Returns a list of dicts: ``{article_number, title, content, chapter, section}``.
    Text that appears before the first 第X条 is kept as a preamble article.
    """
    if not content:
        return []

    lines = content.replace("\r\n", "\n").replace("\r", "\n").split("\n")

    chapter: str | None = None
    section: str | None = None
    articles: list[dict[str, str | None]] = []

    cur_number: str | None = None
    cur_title: str | None = None
    cur_chapter: str | None = None
    cur_section: str | None = None
    cur_lines: list[str] = []
    preamble: list[str] = []

    def flush() -> None:
        nonlocal cur_number, cur_title, cur_lines, cur_chapter, cur_section
        if cur_number is None:
            return
        body = "\n".join(cur_lines).strip()
        if body:
            articles.append(
                {
                    "article_number": cur_number,
                    "title": cur_title,
                    "content": body,
                    "chapter": cur_chapter,
                    "section": cur_section,
                }
            )
        cur_number = None
        cur_title = None
        cur_lines = []

    for raw_line in lines:
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped or ARTIFACT_LINE_RE.match(stripped):
            continue

        m_ch = CHAPTER_RE.match(stripped)
        if m_ch and not ARTICLE_RE.match(stripped):
            flush()
            chapter = (m_ch.group(1) + (m_ch.group(2) or "")).strip()[:128]
            section = None
            continue

        m_sec = SECTION_RE.match(stripped)
        if m_sec and not ARTICLE_RE.match(stripped):
            flush()
            section = (m_sec.group(1) + (m_sec.group(2) or "")).strip()[:128]
            continue

        m_art = ARTICLE_RE.match(stripped)
        if m_art:
            flush()
            cur_number = m_art.group(1)
            # Some documents put a heading right after the article number.
            rest = stripped[m_art.end():].strip()
            if rest and len(rest) <= 60 and not rest.startswith(("　", " ")):
                cur_title = rest[:256]
                cur_lines = []
            else:
                cur_title = None
                cur_lines = [rest] if rest else []
            cur_chapter = chapter
            cur_section = section
            continue

        if cur_number is None:
            preamble.append(stripped)
        else:
            cur_lines.append(stripped)

    flush()

    # Keep a short preamble (title/ratification info) when present.
    preamble_text = "\n".join(preamble).strip()
    if preamble_text and len(articles) > 0:
        articles.insert(
            0,
            {
                "article_number": "序言",
                "title": None,
                "content": preamble_text[:8000],
                "chapter": None,
                "section": None,
            },
        )

    return articles


async def import_laws(
    conn: asyncpg.Connection,
    documents: list[dict],
    batch_size: int = 500,
    dry_run: bool = False,
) -> dict[str, int]:
    stats = {
        "laws_seen": 0,
        "laws_inserted": 0,
        "laws_matched": 0,
        "articles_inserted": 0,
        "articles_skipped": 0,
        "docs_without_articles": 0,
    }

    law_rows: list[tuple] = []
    pending_articles: list[tuple] = []

    async def flush_batch() -> None:
        nonlocal law_rows, pending_articles
        if dry_run:
            law_rows = []
            pending_articles = []
            return

        if law_rows:
            await conn.executemany(
                """
                INSERT INTO laws (id, name, short_name, law_type, category,
                                  effective_date, status, issuing_authority, abstract)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
                ON CONFLICT (name, effective_date) DO NOTHING
                """,
                law_rows,
            )
        if pending_articles:
            await conn.executemany(
                """
                INSERT INTO legal_articles (id, law_id, article_number, title, content,
                                            chapter, section, effective_status, tags)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
                ON CONFLICT (law_id, article_number) DO NOTHING
                """,
                pending_articles,
            )
        law_rows = []
        pending_articles = []

    for doc in documents:
        stats["laws_seen"] += 1
        title = (doc.get("title") or "").strip()
        content = doc.get("content") or ""
        if not title:
            continue

        name = title[:512]
        effective_date = normalize_date(doc.get("publish") or doc.get("publish_date"))
        law_type = (doc.get("type") or "法律").strip()[:64]
        status = (doc.get("status") or "有效").strip()[:32]
        office = (doc.get("office") or "").strip()[:256]
        law_id = str(uuid.uuid4())

        law_rows.append(
            (
                law_id,
                name,
                short_name_of(title),
                law_type,
                law_type,
                effective_date,
                status,
                office,
                content[:1000],
            )
        )
        stats["laws_inserted"] += 1

        arts = split_articles(content)
        if not arts:
            stats["docs_without_articles"] += 1

        for a in arts:
            pending_articles.append(
                (
                    str(uuid.uuid4()),
                    law_id,
                    str(a["article_number"])[:64],
                    a["title"],
                    a["content"],
                    a["chapter"],
                    a["section"],
                    status,
                    law_type,
                )
            )
        stats["articles_inserted"] += len(arts)

        if len(law_rows) >= batch_size:
            await flush_batch()
            logger.info(
                "progress: %d/%d docs, %d articles",
                stats["laws_seen"],
                len(documents),
                stats["articles_inserted"],
            )

    await flush_batch()
    return stats


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--zip", dest="zip_path", required=True, help="path to laws.json.zip")
    ap.add_argument("--dsn", default=DEFAULT_DSN)
    ap.add_argument("--limit", type=int, default=0, help="import only first N documents")
    ap.add_argument("--batch-size", type=int, default=500)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not os.path.exists(args.zip_path):
        logger.error("zip not found: %s", args.zip_path)
        return 2

    t0 = time.time()
    logger.info("reading %s ...", args.zip_path)
    with zipfile.ZipFile(args.zip_path) as z:
        inner = next(n for n in z.namelist() if n.endswith(".json"))
        documents = json.loads(z.read(inner).decode("utf-8"))
    logger.info("loaded %d documents in %.1fs", len(documents), time.time() - t0)

    if args.limit:
        documents = documents[: args.limit]
        logger.info("limited to %d documents", len(documents))

    conn = await asyncpg.connect(args.dsn)
    try:
        before_laws = await conn.fetchval("SELECT count(*) FROM laws")
        before_articles = await conn.fetchval("SELECT count(*) FROM legal_articles")
        logger.info("before: laws=%d articles=%d", before_laws, before_articles)

        stats = await import_laws(conn, documents, batch_size=args.batch_size, dry_run=args.dry_run)

        after_laws = await conn.fetchval("SELECT count(*) FROM laws")
        after_articles = await conn.fetchval("SELECT count(*) FROM legal_articles")
        logger.info(
            "after: laws=%d (+%d) articles=%d (+%d)",
            after_laws,
            after_laws - before_laws,
            after_articles,
            after_articles - before_articles,
        )
        logger.info(
            "stats: parsed_laws=%d parsed_articles=%d docs_without_articles=%d",
            stats["laws_inserted"],
            stats["articles_inserted"],
            stats["docs_without_articles"],
        )
    finally:
        await conn.close()

    logger.info("done in %.1fs", time.time() - t0)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
