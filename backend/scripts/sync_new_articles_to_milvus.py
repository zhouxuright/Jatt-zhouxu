# -*- coding: utf-8 -*-
"""Incrementally sync new PG legal_articles into the Milvus collection.

Unlike app/rag/milvus_full_import.py (full re-import with positional ids),
this script targets rows created since a given date and uses deterministic
`pg_{article_pk}` ids, making re-runs idempotent: rows already present in
Milvus are skipped, only missing ones are embedded and inserted.

Usage (inside the backend container):
    python /app/scripts/sync_new_articles_to_milvus.py --since 2026-09-11
    python /app/scripts/sync_new_articles_to_milvus.py --since 2026-09-11 --dry-run
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "/app")
if not Path("/app").exists():  # host checkout fallback
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.core.config import settings
from app.core.database import async_session_factory
from app.models.legal_knowledge import Law, LegalArticle

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("milvus_sync")

MILVUS_COLLECTION = "legal_articles"
MILVUS_BATCH = 500
EMBED_BATCH = 64
CONTENT_MAX = 8000


def _clean(text: str) -> str:
    return re.sub(r"\s+", "", text or "").strip()[:CONTENT_MAX]


def _connect():
    from pymilvus import Collection, connections

    connections.connect(
        alias="sync", host=settings.MILVUS_HOST, port=settings.MILVUS_PORT,
    )
    coll = Collection(MILVUS_COLLECTION, using="sync")
    coll.load()
    return coll


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
        out = model.encode(texts[j:j + EMBED_BATCH], return_dense=True)
        embeddings.extend(e.tolist() for e in out["dense_vecs"])
    return embeddings


async def run(since: datetime, dry_run: bool) -> None:
    from app.services.model_registry import ModelRegistry

    coll = _connect()
    logger.info("Milvus connected, entities=%d", coll.num_entities)

    model = ModelRegistry.get_embedding_model()

    inserted = skipped = 0
    buffer: list = []

    async with async_session_factory() as db:
        result = await db.stream(
            select(
                LegalArticle.id, LegalArticle.article_number, LegalArticle.content,
                LegalArticle.tags, Law.name, Law.law_type,
            )
            .join(Law, Law.id == LegalArticle.law_id)
            .where(LegalArticle.created_at >= since)
            .order_by(LegalArticle.created_at)
        )
        async for pk, art_num, content, tags, law_name, law_type in result:
            buffer.append((str(pk), art_num or "", content or "", tags or "",
                           law_name or "未知法律", law_type or "其他"))
            if len(buffer) >= MILVUS_BATCH:
                inserted_n, skipped_n = await asyncio.to_thread(
                    _flush_batch, coll, model, buffer, dry_run,
                )
                inserted += inserted_n
                skipped += skipped_n
                buffer = []
                logger.info("progress: inserted=%d skipped=%d", inserted, skipped)

    if buffer:
        inserted_n, skipped_n = await asyncio.to_thread(
            _flush_batch, coll, model, buffer, dry_run,
        )
        inserted += inserted_n
        skipped += skipped_n

    if not dry_run:
        coll.flush()
    logger.info("done: inserted=%d skipped=%d (dry_run=%s)", inserted, skipped, dry_run)


def _flush_batch(coll, model, rows: list[tuple], dry_run: bool) -> tuple[int, int]:
    ids = [f"pg_{r[0]}" for r in rows]
    existing = _existing_ids(coll, ids)
    pending = [(rid, r) for rid, r in zip(ids, rows) if rid not in existing]

    if dry_run:
        return len(pending), len(rows) - len(pending)

    if not pending:
        return 0, len(rows)

    texts = [_clean(r[2]) for _, r in pending]
    keep = [(rid, r, t) for (rid, r), t in zip(pending, texts) if len(t) >= 5]
    if not keep:
        return 0, len(rows)

    embeddings = _embed(model, [t for _, _, t in keep])
    data = [
        [rid for rid, _, _ in keep],
        [r[4][:256] for _, r, _ in keep],
        [r[1][:64] for _, r, _ in keep],
        [t for _, _, t in keep],
        [r[3][:512] for _, r, _ in keep],
        [r[5][:64] for _, r, _ in keep],
        embeddings,
    ]
    coll.insert(data)
    return len(keep), len(rows) - len(keep)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", required=True,
                        help="UTC date or datetime, e.g. 2026-09-11 or 2026-09-11T11:00")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    since = datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc)
    asyncio.run(run(since, args.dry_run))


if __name__ == "__main__":
    main()
