# -*- coding: utf-8 -*-
"""Sync PostgreSQL ``legal_articles`` rows into the Milvus collection.

Unlike ``app/rag/milvus_full_import.py`` (full re-import with positional ids),
this script uses deterministic ``pg_{article_pk}`` ids, making re-runs
idempotent: rows already present in Milvus are skipped, only missing ones are
embedded and inserted.

Field limits come from :mod:`app.rag.milvus_schema` (the single source of
truth) and are then clamped to whatever the *live* collection actually
declares, so a stale collection cannot silently truncate the business key
(law name / article number) or blow up an entire batch with a size error.

Usage (inside the backend container):

    # 首轮全量
    python /app/scripts/sync_new_articles_to_milvus.py --all
    # 增量（按 legal_articles.created_at）
    python /app/scripts/sync_new_articles_to_milvus.py --since 2026-10-01
    # 冒烟 / 只看差集
    python /app/scripts/sync_new_articles_to_milvus.py --all --limit 500 --dry-run
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "/app")
if not Path("/app").exists():  # host checkout fallback
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.database import async_session_factory  # noqa: E402
from app.models.legal_knowledge import Law, LegalArticle  # noqa: E402
from app.rag.milvus_schema import (  # noqa: E402
    LEGAL_ARTICLES_COLLECTION,
    LEGAL_ARTICLE_ARTICLE_NUMBER_MAX,
    LEGAL_ARTICLE_CATEGORY_MAX,
    LEGAL_ARTICLE_CONTENT_MAX,
    LEGAL_ARTICLE_ID_MAX,
    LEGAL_ARTICLE_LAW_NAME_MAX,
    LEGAL_ARTICLE_TAGS_MAX,
    truncate_utf8,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("milvus_sync")

MILVUS_COLLECTION = LEGAL_ARTICLES_COLLECTION
#: 每多少次 PG 行触发一轮「差集 + 向量化 + 写入」。数值越小内存峰值越低。
MILVUS_BATCH = 500
#: encode 的 batch_size。BGE-M3 会按批内最长句 padding，批越小 padding 浪费越少。
EMBED_BATCH = int(os.environ.get("SYNC_EMBED_BATCH", "8"))
#: 离线任务允许截断到 512 token：绝大多数法条远短于此，尾部超长条款被截掉也
#: 只影响该条的向量表示，不影响 PG 中的权威原文。
EMBED_MAX_LENGTH = int(os.environ.get("SYNC_EMBED_MAX_LENGTH", "512"))
#: 离线批量任务用多线程压满 CPU（在线服务才必须 1 线程以免原生层竞态）。
EMBED_THREADS = int(os.environ.get("SYNC_EMBED_THREADS", "2"))
FLUSH_EVERY_BATCHES = 20

#: 业务键字段：截断会让 Milvus 与 PG 的同一条法条不再相等，差集永远补不齐，
#: 因此这些字段一旦小于 schema 常量就直接失败，而不是静默截断。
KEY_LIMITS: dict[str, int] = {
    "id": LEGAL_ARTICLE_ID_MAX,
    "law_name": LEGAL_ARTICLE_LAW_NAME_MAX,
    "article_number": LEGAL_ARTICLE_ARTICLE_NUMBER_MAX,
}


def _clean(text: str) -> str:
    """Normalise whitespace while keeping paragraph structure.

    旧实现用 ``re.sub(r"\\s+", "", text)`` 删除了**所有**空白，把整条法条压成
    一行，既损失可读性，也让引文校验无法按行比对。这里只折叠行内多余空白、
    丢弃空行。
    """
    t = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[ \t\u3000\u00a0]+", " ", ln).strip() for ln in t.split("\n")]
    return "\n".join(ln for ln in lines if ln)


def _embed_text(title: str, content: str) -> str:
    """向量编码文本 = 条文首句（title）+ 正文。

    ``import_law_fulltext.split_articles()`` 会把条文第一句单独存进 ``title``，
    正文不含这一句。只编码 ``content`` 会让首句完全不参与检索，因此拼接后再
    encode；Milvus 的 ``content`` 字段仍只存正文，保持与 PG 可逐行比对。
    """
    t = (title or "").strip()
    c = (content or "").strip()
    if t and c:
        return t + "\n" + c
    return t or c


def _connect():
    from pymilvus import Collection, connections

    connections.connect(alias="sync", host=settings.MILVUS_HOST, port=settings.MILVUS_PORT)
    coll = Collection(MILVUS_COLLECTION, using="sync")
    coll.load()
    return coll


def _live_limits(coll) -> dict[str, int]:
    """Read the VARCHAR ``max_length`` values the live collection declares."""
    limits: dict[str, int] = {}
    for field in coll.schema.fields:
        params = getattr(field, "params", None) or {}
        if "max_length" in params:
            limits[field.name] = int(params["max_length"])
    return limits


def _effective_limits(coll) -> dict[str, int]:
    live = _live_limits(coll)
    declared = {
        "id": LEGAL_ARTICLE_ID_MAX,
        "law_name": LEGAL_ARTICLE_LAW_NAME_MAX,
        "article_number": LEGAL_ARTICLE_ARTICLE_NUMBER_MAX,
        "content": LEGAL_ARTICLE_CONTENT_MAX,
        "tags": LEGAL_ARTICLE_TAGS_MAX,
        "category": LEGAL_ARTICLE_CATEGORY_MAX,
    }
    eff = {name: min(declared[name], live.get(name, declared[name])) for name in declared}

    for name, limit in KEY_LIMITS.items():
        if eff[name] < limit:
            raise RuntimeError(
                f"Milvus collection '{MILVUS_COLLECTION}' declares {name} "
                f"max_length={eff[name]} bytes, below required {limit}. "
                "Truncating a key field would break cross-store matching — "
                "run scripts/widen_milvus_varchar.py (or recreate the empty "
                "collection) before syncing."
            )
    if eff["content"] < declared["content"]:
        logger.warning(
            "collection content max_length=%d < canonical %d; long articles will be truncated",
            eff["content"],
            declared["content"],
        )
    logger.info("effective varchar limits (bytes): %s", eff)
    return eff


def _existing_ids(coll, ids: list[str]) -> set[str]:
    found: set[str] = set()
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        expr = "id in [" + ",".join(f'"{x}"' for x in chunk) + "]"
        res = coll.query(expr=expr, output_fields=["id"], limit=len(chunk))
        found.update(item["id"] for item in res)
    return found


def _embed(model, texts: list[str]) -> list[list[float]]:
    embeddings: list[list[float]] = []
    for j in range(0, len(texts), EMBED_BATCH):
        out = model.encode(
            texts[j:j + EMBED_BATCH],
            return_dense=True,
            max_length=EMBED_MAX_LENGTH,
            batch_size=EMBED_BATCH,
        )
        embeddings.extend(e.tolist() for e in out["dense_vecs"])
    return embeddings


def _flush_batch(coll, model, limits, rows: list[tuple], dry_run: bool) -> tuple[int, int]:
    ids = [f"pg_{r[0]}" for r in rows]
    existing = _existing_ids(coll, ids)
    pending = [(rid, r) for rid, r in zip(ids, rows) if rid not in existing]

    if dry_run:
        return len(pending), len(rows) - len(pending)

    if not pending:
        return 0, len(rows)

    stored = [_clean(r[2]) for _, r in pending]
    to_embed = [_clean(_embed_text(r[6], r[2])) for _, r in pending]
    keep = [
        (rid, r, s, e)
        for (rid, r), s, e in zip(pending, stored, to_embed)
        if len(s) >= 5
    ]
    if not keep:
        return 0, len(rows)

    embeddings = _embed(model, [e for _, _, _, e in keep])
    data = [
        [truncate_utf8(rid, limits["id"]) for rid, _, _, _ in keep],
        [truncate_utf8(r[4], limits["law_name"]) for _, r, _, _ in keep],
        [truncate_utf8(r[1], limits["article_number"]) for _, r, _, _ in keep],
        [truncate_utf8(s, limits["content"]) for _, _, s, _ in keep],
        [truncate_utf8(r[3], limits["tags"]) for _, r, _, _ in keep],
        [truncate_utf8(r[5], limits["category"]) for _, r, _, _ in keep],
        embeddings,
    ]
    coll.insert(data)
    return len(keep), len(rows) - len(keep)


def _configure_torch_threads() -> None:
    """让离线批量任务跑满 CPU 核。

    ``ModelRegistry._pin_torch_threads()`` 会把进程压到单线程——那是在线服务
    避免原生层并发崩溃的必要条件。这里是独立进程、且 backend 已停，不存在
    并发推理，因此解除限制以换取吞吐（约 N 倍加速）。
    """
    try:
        import torch
        torch.set_num_threads(EMBED_THREADS)
        try:
            torch.set_num_interop_threads(1)
        except Exception:
            pass
        logger.info("torch intra-op threads set to %d (cpus=%s)", EMBED_THREADS, os.cpu_count())
    except Exception as exc:  # pragma: no cover - best effort
        logger.warning("could not widen torch threads: %s", exc)


async def run(since: datetime | None, dry_run: bool, limit: int) -> None:
    from app.services.model_registry import ModelRegistry

    coll = _connect()
    limits = _effective_limits(coll)
    logger.info("Milvus connected, entities=%d", coll.num_entities)

    model = ModelRegistry.get_embedding_model()
    _configure_torch_threads()

    inserted = skipped = 0
    batches = 0
    buffer: list = []

    stmt = (
        select(
            LegalArticle.id, LegalArticle.article_number, LegalArticle.content,
            LegalArticle.tags, Law.name, Law.law_type, LegalArticle.title,
        )
        .join(Law, Law.id == LegalArticle.law_id)
        .order_by(LegalArticle.created_at)
    )
    if since is not None:
        stmt = stmt.where(LegalArticle.created_at >= since)
    if limit:
        stmt = stmt.limit(limit)

    async with async_session_factory() as db:
        result = await db.stream(stmt)
        async for pk, art_num, content, tags, law_name, law_type, art_title in result:
            buffer.append((
                str(pk), art_num or "", content or "", tags or "",
                law_name or "未知法律", law_type or "其他", art_title or "",
            ))
            if len(buffer) >= MILVUS_BATCH:
                inserted_n, skipped_n = await asyncio.to_thread(
                    _flush_batch, coll, model, limits, buffer, dry_run,
                )
                inserted += inserted_n
                skipped += skipped_n
                buffer = []
                batches += 1
                logger.info(
                    "progress: batch=%d inserted=%d skipped=%d", batches, inserted, skipped,
                )
                if not dry_run and batches % FLUSH_EVERY_BATCHES == 0:
                    coll.flush()
                    logger.info("flushed to disk at batch %d", batches)

    if buffer:
        inserted_n, skipped_n = await asyncio.to_thread(
            _flush_batch, coll, model, limits, buffer, dry_run,
        )
        inserted += inserted_n
        skipped += skipped_n
        batches += 1

    if not dry_run:
        coll.flush()
    logger.info(
        "done: inserted=%d skipped=%d batches=%d (dry_run=%s)",
        inserted, skipped, batches, dry_run,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", default=None,
                        help="UTC date or datetime, e.g. 2026-09-11 or 2026-09-11T11:00")
    parser.add_argument("--all", action="store_true", help="sync every row (ignore created_at)")
    parser.add_argument("--limit", type=int, default=0, help="only read first N rows")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not args.all and not args.since:
        parser.error("either --all or --since is required")

    since = (
        datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc)
        if args.since else None
    )
    asyncio.run(run(since, args.dry_run, args.limit))


if __name__ == "__main__":
    main()
