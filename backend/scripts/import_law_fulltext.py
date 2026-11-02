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

Idempotency
-----------
Re-running must not duplicate anything.  The two upsert targets therefore get
dedicated unique indexes that this script creates up front:

    laws           (name, effective_date)
    legal_articles (law_id, article_number)

``laws.effective_date`` is nullable in the model but PostgreSQL does **not**
de-duplicate NULLs in a unique index, so every row written here uses ``""``
instead of NULL and existing NULLs are normalised to ``""`` before the index
is created.

Batch semantics: laws are inserted with a multi-row
``ON CONFLICT ... DO UPDATE ... RETURNING id`` — ``DO UPDATE`` is a no-op that
still makes RETURNING emit a row for already-existing laws, so the batch keeps
the *original* law id instead of inventing a new UUID (which previously made
every re-run append a duplicate set of articles).

Usage (inside the backend container):

    # 默认只导入权威、现行有效的核心类别（约 4 万条法条）
    python scripts/import_law_fulltext.py --zip /data/laws.json.zip
    # 冒烟：只处理前 200 篇
    python scripts/import_law_fulltext.py --zip /data/laws.json.zip --limit 200
    # 只解析不写库
    python scripts/import_law_fulltext.py --zip /data/laws.json.zip --dry-run
    # 全量（含地方性法规，约 77 万条法条）
    python scripts/import_law_fulltext.py --zip /data/laws.json.zip --all
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

#: 默认导入的权威类别。地方性法规（49.6 万条）体量最大但权威性/复用价值最低，
#: 需要显式 ``--all`` 才会导入。依据 laws.json 实测统计。
DEFAULT_LAW_TYPES = ("宪法", "法律", "行政法规", "司法解释", "监察法规", "法律解释")
DEFAULT_STATUS = "有效"

# 第X条 / 第X条之一 / 第X条之X
ARTICLE_RE = re.compile(r"^\s*(第[〇零一二三四五六七八九十百千万两0-9]+条(?:之[〇零一二三四五六七八九十]+)?)")
# 第X章 / 第X节 / 第X编
CHAPTER_RE = re.compile(r"^\s*(第[〇零一二三四五六七八九十百千0-9]+[章编])(.*)$")
SECTION_RE = re.compile(r"^\s*(第[〇零一二三四五六七八九十百千0-9]+节)(.*)$")
# 裸标题：``附则`` 或 ``八、票据保证`` 这类中文数字编号的章节名。
# 国家法律法规数据库的全文里，司法解释/部门规章大量使用这种写法，而上面的
# CHAPTER_RE / SECTION_RE 只认「第X章/节/编」。不识别它们，标题就会被当成
# 上一条的正文拼进去（历史上有 41 条法条因此只剩一个章节名）。
# 只匹配短、无标点的行，并在调用处额外要求「下一行是第X条」，避免把条文内部
# 的「一、……」枚举误判为章节标题。
BARE_HEADING_RE = re.compile(r"^(附则|[一二三四五六七八九十]+、[^。，；：、]{1,20})$")

ARTIFACT_LINE_RE = re.compile(r"^\s*(?:page\s*\d+|\d+\s*/\s*\d+)\s*$", re.IGNORECASE)

#: 单条 SQL 的参数上限（asyncpg 为 32767），laws 每行 9 个参数，
#: 因此一批最多 3000 行；这里远低于上限，留足余量。
MAX_UPSERT_ROWS = 2000


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
        title = cur_title
        if not body and title:
            # 条文只有一句、被方案里的「短首句即标题」规则吃掉正文时，不能整条
            # 丢弃（历史上 37 条法条因此只剩章节标题）：把标题降级为正文。
            body, title = title, None
        if body:
            articles.append(
                {
                    "article_number": cur_number,
                    "title": title,
                    "content": body,
                    "chapter": cur_chapter,
                    "section": cur_section,
                }
            )
        cur_number = None
        cur_title = None
        cur_lines = []

    # 先剔除空行与页码伪影，再按索引遍历：裸章节标题必须确认「下一行是第X条」
    # 才认作标题，否则条文内部的「一、……」枚举会被误判。
    clean_lines = [
        ln.strip()
        for ln in lines
        if ln.strip() and not ARTIFACT_LINE_RE.match(ln.strip())
    ]

    def next_is_article(idx: int) -> bool:
        return idx + 1 < len(clean_lines) and bool(ARTICLE_RE.match(clean_lines[idx + 1]))

    def is_list_lead_in(idx: int) -> bool:
        """上一行以「：」结尾 → 紧跟的「一、……」是条文内枚举，不是章节标题。

        真实语料里条文枚举多用「（一）」写法，裸「一、」几乎只作章节标题；
        但仍有文档写成「……按照以下情形分别处理：
        一、……」这种枚举，用引导句结尾的冒号把这类情况排除掉。
        """
        return idx > 0 and clean_lines[idx - 1].endswith(("：", ":"))

    for idx, stripped in enumerate(clean_lines):
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

        if (
            BARE_HEADING_RE.match(stripped)
            and next_is_article(idx)
            and not is_list_lead_in(idx)
        ):
            flush()
            chapter = stripped[:128]
            section = None
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


async def ensure_indexes(conn: asyncpg.Connection) -> None:
    """Create the unique indexes the upserts rely on (idempotent)."""
    async with conn.transaction():
        # NULL 在唯一索引里不去重 → 统一折叠为空字符串
        await conn.execute("UPDATE laws SET effective_date = '' WHERE effective_date IS NULL")
        await conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_laws_name_effective_date "
            "ON laws (name, effective_date)"
        )
        await conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_articles_law_article_number "
            "ON legal_articles (law_id, article_number)"
        )
    logger.info("unique indexes ready")


async def flush_batch(
    conn: asyncpg.Connection,
    docs: list[dict],
) -> None:
    """Upsert one batch of laws, then insert their articles."""
    if not docs:
        return

    # --- 1) upsert laws -----------------------------------------------------
    values: list[str] = []
    params: list = []
    for i, d in enumerate(docs):
        b = i * 9
        values.append(
            f"(${b + 1},${b + 2},${b + 3},${b + 4},${b + 5},"
            f"${b + 6},${b + 7},${b + 8},${b + 9})"
        )
        params.extend(d["row"])

    sql = (
        "INSERT INTO laws (id, name, short_name, law_type, category, "
        "effective_date, status, issuing_authority, abstract) "
        f"VALUES {','.join(values)} "
        # no-op update so RETURNING also returns rows that already existed
        "ON CONFLICT (name, effective_date) DO UPDATE SET name = EXCLUDED.name "
        "RETURNING id, name, effective_date"
    )
    returned = await conn.fetch(sql, *params)
    id_map = {(r["name"], r["effective_date"]): r["id"] for r in returned}

    # --- 2) insert articles -------------------------------------------------
    article_rows: list[tuple] = []
    for d in docs:
        law_id = id_map.get((d["name"], d["effective_date"]))
        if not law_id:
            logger.warning("law id missing after upsert: %s", d["name"][:80])
            continue
        for a in d["articles"]:
            article_rows.append(
                (
                    str(uuid.uuid4()),
                    law_id,
                    str(a["article_number"])[:64],
                    a["title"],
                    a["content"],
                    a["chapter"],
                    a["section"],
                    d["status"],
                    d["law_type"],
                )
            )

    if article_rows:
        await conn.executemany(
            """
            INSERT INTO legal_articles (id, law_id, article_number, title, content,
                                        chapter, section, effective_status, tags)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
            ON CONFLICT (law_id, article_number) DO NOTHING
            """,
            article_rows,
        )


def _matches_filters(doc: dict, types: set[str], status: str) -> bool:
    if status and (doc.get("status") or "").strip() != status:
        return False
    if types:
        if (doc.get("type") or "").strip() not in types:
            return False
    return True


async def import_laws(
    conn: asyncpg.Connection,
    documents: list[dict],
    *,
    types: set[str],
    status: str,
    batch_size: int = 200,
    dry_run: bool = False,
) -> dict[str, int]:
    stats = {
        "docs_considered": 0,
        "docs_imported": 0,
        "docs_filtered": 0,
        "docs_without_articles": 0,
        "articles_parsed": 0,
    }

    batch_size = max(1, min(batch_size, MAX_UPSERT_ROWS))
    buffer: list[dict] = []

    async def flush() -> None:
        nonlocal buffer
        if not buffer:
            return
        docs, buffer = buffer, []
        if not dry_run:
            await flush_batch(conn, docs)
        stats["docs_imported"] += len(docs)
        logger.info(
            "progress: docs=%d articles=%d",
            stats["docs_imported"],
            stats["articles_parsed"],
        )

    for doc in documents:
        stats["docs_considered"] += 1
        title = (doc.get("title") or "").strip()
        content = doc.get("content") or ""
        if not title:
            stats["docs_filtered"] += 1
            continue
        if not _matches_filters(doc, types, status):
            stats["docs_filtered"] += 1
            continue

        name = title[:512]
        # "" 而非 None：唯一索引对 NULL 不去重
        effective_date = normalize_date(doc.get("publish") or doc.get("publish_date")) or ""
        law_type = (doc.get("type") or "法律").strip()[:64]
        doc_status = (doc.get("status") or "").strip()[:32]
        office = (doc.get("office") or "").strip()[:256]

        arts = split_articles(content)
        if not arts:
            stats["docs_without_articles"] += 1
            continue

        buffer.append(
            {
                "name": name,
                "effective_date": effective_date,
                "law_type": law_type,
                "status": doc_status,
                "articles": arts,
                "row": (
                    str(uuid.uuid4()),
                    name,
                    short_name_of(title),
                    law_type,
                    law_type,
                    effective_date,
                    doc_status,
                    office,
                    content[:1000],
                ),
            }
        )
        stats["articles_parsed"] += len(arts)

        if len(buffer) >= batch_size:
            await flush()

    await flush()
    return stats


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--zip", dest="zip_path", required=True, help="path to laws.json.zip")
    ap.add_argument("--dsn", default=DEFAULT_DSN)
    ap.add_argument("--limit", type=int, default=0, help="import only first N documents")
    ap.add_argument("--batch-size", type=int, default=200)
    ap.add_argument("--types", default=",".join(DEFAULT_LAW_TYPES),
                    help="comma separated law types to import (empty = all)")
    ap.add_argument("--status", default=DEFAULT_STATUS,
                    help="only import documents with this status (empty = all)")
    ap.add_argument("--all", action="store_true",
                    help="import every document regardless of type/status")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not os.path.exists(args.zip_path):
        logger.error("zip not found: %s", args.zip_path)
        return 2

    types: set[str] = set()
    status = args.status
    if args.all:
        types = set()
        status = ""
    else:
        types = {t.strip() for t in args.types.split(",") if t.strip()}
    logger.info(
        "filters: types=%s status=%r",
        sorted(types) or "ALL",
        status or "ALL",
    )

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

        await ensure_indexes(conn)
        stats = await import_laws(
            conn,
            documents,
            types=types,
            status=status,
            batch_size=args.batch_size,
            dry_run=args.dry_run,
        )

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
            "stats: considered=%d imported=%d filtered=%d without_articles=%d parsed_articles=%d",
            stats["docs_considered"],
            stats["docs_imported"],
            stats["docs_filtered"],
            stats["docs_without_articles"],
            stats["articles_parsed"],
        )
    finally:
        await conn.close()

    logger.info("done in %.1fs", time.time() - t0)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
