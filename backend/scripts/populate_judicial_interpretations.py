"""Populate `judicial_interpretations` from the `laws` table.

Why this exists
---------------
`judicial_interpretations` had **0 rows** while the `laws` table already carried
229 司法解释 / 法律解释 (218 + 10 + 1 typo'd "司法解") with 4,981 full-text
articles (703K chars). The `government_regulation` MCP tool queried the empty
table, so every judicial-interpretation lookup returned nothing — the tool
looked broken, and the system told users it could not retrieve documents it
actually held.

This script copies those rows across and assembles each interpretation's full
text from its articles, so the table becomes the single place that tool reads.

Idempotent: rows are keyed by `name`, and existing rows are updated in place.

Usage
-----
    python scripts/populate_judicial_interpretations.py --dry-run
    python scripts/populate_judicial_interpretations.py
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import re
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from app.core.database import async_session_factory  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("populate_ji")

# `law_type` values that mean "judicial interpretation". "司法解" is a typo
# present in the source data for at least one row and is included deliberately.
INTERPRETATION_TYPES = ("司法解释", "司法解", "法律解释")

# `laws.status` -> human-readable 效力状态, kept for the tags column.
STATUS_LABELS = {
    "active": "现行有效",
    "amended": "已修改",
    "repealed": "已废止",
    "not_yet_effective": "尚未生效",
    "unknown": "效力待定",
}

# Column width limits, so truncation is explicit rather than a DB error.
MAX_NAME = 512
MAX_DOC_NUMBER = 128
MAX_COURT = 256
MAX_EFFECTIVE_DATE = 32
MAX_RELATED_LAW = 512
MAX_TAGS = 512

# Guard the assembled content against pathological sizes.
MAX_CONTENT_CHARS = 400_000

# 1,875 rows in `legal_articles` carry an internal identifier (`__<hex>`) in
# `article_number` instead of a 条号 — the real text is in `content`. Emitting it
# would print a leaked ID into the body of a legal document, and it would sort
# ahead of 第一条, so it is treated as "no number" rather than as a heading.
INTERNAL_ID_ARTICLE_NUMBER = re.compile(r"^__[0-9a-f]{8,}$")


def _clip(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    return value if len(value) <= limit else value[: limit - 1] + "…"


async def _fetch_source_rows(session: AsyncSession) -> list[dict]:
    """Read interpretations plus their articles in one pass."""
    sql = text(
        """
        SELECT l.id            AS law_id,
               l.name          AS name,
               l.issuing_authority,
               l.effective_date,
               l.status,
               l.category,
               l.abstract,
               a.article_number,
               a.content       AS article_content,
               a.chapter,
               a.section
        FROM laws l
        LEFT JOIN legal_articles a ON a.law_id = l.id
        WHERE l.law_type = ANY(:types)
        ORDER BY l.name, a.article_number
        """
    )
    result = await session.execute(sql, {"types": list(INTERPRETATION_TYPES)})
    return [dict(row._mapping) for row in result.fetchall()]


def _group_by_law(rows: list[dict]) -> list[dict]:
    """Collapse the join result into one record per interpretation."""
    grouped: dict[str, dict] = {}
    order: list[str] = []

    for row in rows:
        law_id = row["law_id"]
        if law_id not in grouped:
            grouped[law_id] = {
                "law_id": law_id,
                "name": row["name"],
                "issuing_authority": row["issuing_authority"],
                "effective_date": row["effective_date"],
                "status": row["status"],
                "category": row["category"],
                "abstract": row["abstract"],
                "articles": [],
            }
            order.append(law_id)

        if row["article_content"]:
            grouped[law_id]["articles"].append(
                {
                    "number": row["article_number"] or "",
                    "content": row["article_content"],
                    "chapter": row["chapter"],
                    "section": row["section"],
                }
            )

    return [grouped[law_id] for law_id in order]


def _dedupe_by_name(records: list[dict]) -> list[dict]:
    """Keep one record per name, preferring the richest text.

    `laws` holds near-duplicate rows for the same interpretation (same name,
    different `law_type`/version) whose bodies differ by a few percent. The
    target table is keyed by name, so without this the winner would depend on
    row order. Pick the largest body explicitly instead.
    """
    best: dict[str, dict] = {}
    for record in records:
        size = sum(len(a["content"] or "") for a in record["articles"])
        current = best.get(record["name"])
        if current is None or size > current[0]:
            best[record["name"]] = (size, record)
    return [record for _, record in best.values()]


def _compose_content(record: dict) -> str:
    """Assemble full text: abstract first, then every article in order.

    Articles arrive ordered by `article_number`, which is a varchar and so sorts
    lexicographically ("第十一条" before "第十条"). Chapter/section headers are
    emitted only when they change, to keep the text readable.
    """
    parts: list[str] = []

    abstract = (record.get("abstract") or "").strip()
    if abstract:
        parts.append(abstract)

    last_chapter = None
    last_section = None
    # The SQL orders by `article_number` as text, which puts 第十一条 before 第十条.
    # Re-sort numerically so the document reads in statute order.
    for article in sorted(record["articles"], key=_article_sort_key):
        chapter = (article.get("chapter") or "").strip()
        section = (article.get("section") or "").strip()
        if chapter and chapter != last_chapter:
            parts.append(f"\n{chapter}")
            last_chapter = chapter
        if section and section != last_section:
            parts.append(f"\n{section}")
            last_section = section

        number = article["number"].strip()
        if INTERNAL_ID_ARTICLE_NUMBER.match(number):
            number = ""  # leaked internal ID, not a 条号
        body = (article["content"] or "").strip()
        parts.append(f"\n{number}\n{body}" if number else f"\n{body}")

    content = "\n".join(parts).strip()
    if len(content) > MAX_CONTENT_CHARS:
        content = content[:MAX_CONTENT_CHARS] + "\n…（正文过长已截断）"
    return content


_CN_DIGITS = "零一二三四五六七八九"


def _cn_to_int(value: str) -> int | None:
    """Parse an article number such as 第四十七条 / 第一千零七十九条 into an int.

    Article numbers are stored as text, so a plain ORDER BY sorts them
    lexicographically — 第十一条 before 第十条, and 第一条 before 第七条. Since the
    MCP tool shows only the opening of the assembled text, that wrong order
    would surface directly to users.

    Returns None when the value is not an article number, so the caller can
    fall back. Internal-ID values (`__<hex>`) must return None: a bare
    `re.search(r"\\d+")` would happily read 324314 out of
    `__fe324314bad733fa` and sort that document to the end of a 1,000-article
    statute.
    """
    text = (value or "").strip()
    if not text or INTERNAL_ID_ARTICLE_NUMBER.match(text):
        return None

    match = re.search(r"第\s*([零一二三四五六七八九十百千]+)\s*条", text)
    if not match:
        # Arabic form: "第47条", or a bare "47".
        arabic = re.fullmatch(r"第?\s*(\d+)\s*条?", text)
        return int(arabic.group(1)) if arabic else None

    total = 0
    digit = 0
    for char in match.group(1):
        if char == "零":
            continue
        if char in _CN_DIGITS:
            digit = _CN_DIGITS.index(char)
        elif char == "十":
            # A leading 十 is 10 ("十七" = 17); otherwise it scales the digit
            # before it ("四十七" = 47).
            total += (digit or 1) * 10
            digit = 0
        elif char == "百":
            total += (digit or 1) * 100
            digit = 0
        elif char == "千":
            total += (digit or 1) * 1000
            digit = 0
    return total + digit


def _article_sort_key(article: dict) -> tuple[int, str]:
    """Order articles by their numeric 条号, falling back to the raw text.

    Unnumbered articles (including the 1,875 rows whose `article_number` holds a
    leaked internal id) sort last, in stable text order.
    """
    number = article.get("number") or ""
    parsed = _cn_to_int(number)
    if parsed is None:
        return (10**9, number)
    return (parsed, number)


_CONTENT_CACHE: dict[str, str] = {}


def _compose_content_cached(record: dict) -> str:
    """`_compose_content` is O(articles) and called from two places per record."""
    key = record["law_id"]
    if key not in _CONTENT_CACHE:
        _CONTENT_CACHE[key] = _compose_content(record)
    return _CONTENT_CACHE[key]


def _related_laws(articles: list[dict]) -> str | None:
    """Collect law names cited inside `《…》` across the articles.

    Gives the tool a cheap "which statutes does this interpretation construe"
    answer without a second query.
    """
    seen: list[str] = []
    for article in articles:
        for match in re.findall(r"《([^》]{2,60})》", article["content"] or ""):
            name = match.strip()
            if name and name not in seen:
                seen.append(name)
    if not seen:
        return None
    return _clip("、".join(seen), MAX_RELATED_LAW)


def _tags(record: dict) -> str | None:
    labels = [f"law_type:{record['category']}" if record.get("category") else None]
    status_label = STATUS_LABELS.get(record.get("status") or "")
    if status_label:
        labels.append(f"效力:{status_label}")
    labels.append(f"law_id:{record['law_id']}")  # provenance back to the source row
    return _clip(",".join(label for label in labels if label), MAX_TAGS)


def _document_number(record: dict) -> str | None:
    """Best-effort 文号 extraction, e.g. 法释〔2020〕26号.

    `laws.abstract` is populated for only 131 of 18,580 rows, so in practice the
    number has to come from the body: 司法解释 open with a 通知 that cites it
    ("…高检发释字〔1998〕2号…"). Search the abstract first, then the opening of
    the assembled text.
    """
    # Search the whole body, not a prefix: the 文号 sits in the opening 通知, whose
    # position shifts with article ordering. A prefix window made the extracted
    # value depend on how the articles happened to be sorted.
    body = _compose_content_cached(record)
    candidates = [record.get("abstract") or "", body]

    # 法释/法发/高检发释字 + 〔年〕序号 + 号
    pattern = re.compile(
        r"(?:法释|法发|高检发释字|法办|法函)\s*[〔\[（(]\s*\d{4}\s*[〕\]）)]\s*\d+\s*号"
    )
    for candidate in candidates:
        match = pattern.search(candidate)
        if match:
            # Normalise full-width brackets so the value is stable to cite.
            return _clip(re.sub(r"\s+", "", match.group(0)), MAX_DOC_NUMBER)
    return None


async def populate(dry_run: bool = False) -> dict[str, int]:
    stats = {"scanned": 0, "upserted": 0, "skipped_no_content": 0, "articles": 0}

    async with async_session_factory() as session:
        rows = await _fetch_source_rows(session)
        records = _dedupe_by_name(_group_by_law(rows))
        stats["scanned"] = len(records)

        existing = await session.execute(text("SELECT name FROM judicial_interpretations"))
        known = {row[0] for row in existing.fetchall()}
        logger.info(
            "source: %d interpretations (%d rows); target currently holds %d",
            len(records), len(rows), len(known),
        )

        for record in records:
            content = _compose_content_cached(record)
            if not content:
                # `_dedupe_by_name` already preferred the richest sibling, so
                # reaching here means no row for this name carried any text.
                stats["skipped_no_content"] += 1
                logger.warning(
                    "no article text anywhere for %r — excluded from the target table",
                    record["name"],
                )
                continue

            params = {
                "name": _clip(record["name"], MAX_NAME),
                "doc_number": _document_number(record),
                "issuing_court": _clip(record["issuing_authority"], MAX_COURT),
                "effective_date": _clip(record["effective_date"], MAX_EFFECTIVE_DATE),
                "content": content,
                "related_law": _related_laws(record["articles"]),
                "tags": _tags(record),
            }

            if dry_run:
                if stats["upserted"] < 5:
                    logger.info(
                        "[dry-run] %s | %d articles | %d chars | 文号=%s | 关联=%s",
                        record["name"][:60], len(record["articles"]),
                        len(content), params["doc_number"], params["related_law"],
                    )
                stats["upserted"] += 1
                stats["articles"] += len(record["articles"])
                continue

            # `name` has no unique constraint, so upsert by hand: UPDATE first,
            # INSERT only when nothing matched.
            updated = await session.execute(
                text(
                    """
                    UPDATE judicial_interpretations
                       SET doc_number = :doc_number,
                           issuing_court = :issuing_court,
                           effective_date = :effective_date,
                           content = :content,
                           related_law = :related_law,
                           tags = :tags,
                           updated_at = now()
                     WHERE name = :name
                    """
                ),
                params,
            )
            if updated.rowcount == 0:
                await session.execute(
                    text(
                        """
                        INSERT INTO judicial_interpretations
                            (id, name, doc_number, issuing_court, effective_date,
                             content, related_law, tags, created_at, updated_at)
                        VALUES
                            (gen_random_uuid()::text, :name, :doc_number, :issuing_court,
                             :effective_date, :content, :related_law, :tags, now(), now())
                        """
                    ),
                    params,
                )
            stats["upserted"] += 1
            stats["articles"] += len(record["articles"])

        if not dry_run:
            await session.commit()

    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Assemble and report without writing to the database.",
    )
    args = parser.parse_args()

    stats = asyncio.run(populate(dry_run=args.dry_run))
    logger.info(
        "%s scanned=%d upserted=%d articles=%d skipped_no_content=%d",
        "[dry-run]" if args.dry_run else "[done]",
        stats["scanned"], stats["upserted"], stats["articles"],
        stats["skipped_no_content"],
    )


if __name__ == "__main__":
    main()
