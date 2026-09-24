# -*- coding: utf-8 -*-
"""GPU-accelerated incremental sync of new PG legal_articles into Milvus.

Host-side replacement for the in-container CPU sync: connects to the exposed
PostgreSQL (localhost:5433) and Milvus (localhost:19530), embeds new articles
with BGE-M3 on the local GPU, and inserts them with deterministic pg_{pk} ids
(idempotent, resumable).

Byte-safe truncation: Milvus VARCHAR max_length counts UTF-8 BYTES, so every
string field is truncated by byte budget, not by characters.

Usage (host, GPU venv):
    python sync_milvus_host.py --since 2026-09-11
    python sync_milvus_host.py --since 2026-09-11 --dry-run
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
import psycopg2.extras

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("milvus_sync_host")

ROOT = Path(__file__).resolve().parents[2]  # project root
PG_PORT = 5433
PG_DB = "legal_assistant"
PG_USER = "postgres"

MILVUS_HOST = "localhost"
MILVUS_PORT = "19530"
MILVUS_COLLECTION = "legal_articles"

PG_PAGE = 1000          # rows fetched from PG per page
EMBED_BATCH = 16        # GPU embed batch (halved on OOM down to 1)
MILVUS_BATCH = 500      # rows per Milvus insert
FLUSH_EVERY_N_BATCHES = 20

# Milvus VARCHAR max_length is BYTES; keep a small safety margin.
B_CONTENT = 8000
B_LAW_NAME = 250
B_ARTICLE_NUM = 60
B_TAGS = 500
B_CATEGORY = 60


def _pg_password() -> str:
    import os
    pw = os.environ.get("POSTGRES_PASSWORD")
    if pw:
        return pw
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("POSTGRES_PASSWORD="):
                return line.split("=", 1)[1].strip()
    raise RuntimeError("POSTGRES_PASSWORD not found (env or .env)")


def _utf8_truncate(s: str | None, max_bytes: int) -> str:
    if not s:
        return ""
    b = s.encode("utf-8")
    if len(b) <= max_bytes:
        return s
    return b[:max_bytes].decode("utf-8", errors="ignore")


def _clean(text: str | None) -> str:
    return re.sub(r"\s+", "", text or "").strip()


def _connect_pg():
    conn = psycopg2.connect(
        host="localhost", port=PG_PORT, dbname=PG_DB, user=PG_USER,
        password=_pg_password(),
    )
    return conn


def _connect_milvus():
    from pymilvus import Collection, connections

    connections.connect(host=MILVUS_HOST, port=MILVUS_PORT)
    coll = Collection(MILVUS_COLLECTION)
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
    batch = EMBED_BATCH
    while True:
        try:
            embeddings: list[list[float]] = []
            for j in range(0, len(texts), batch):
                out = model.encode(texts[j:j + batch], return_dense=True)
                embeddings.extend(e.tolist() for e in out["dense_vecs"])
            return embeddings
        except Exception as exc:
            if "out of memory" in str(exc).lower() and batch > 1:
                batch //= 2
                logger.warning("CUDA OOM, retrying with embed batch=%d", batch)
                import torch
                torch.cuda.empty_cache()
                continue
            raise


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", required=True,
                        help="UTC date/datetime, e.g. 2026-09-11 or 2026-09-11T11:00")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    since = datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc)

    import torch
    device = "cuda:0" if torch.cuda.is_available() else "cpu"
    logger.info("torch %s, device=%s", torch.__version__, device)

    from FlagEmbedding import BGEM3FlagModel
    logger.info("loading BGE-M3 (use_fp16=%s)...", device != "cpu")
    model = BGEM3FlagModel("BAAI/bge-m3", use_fp16=device != "cpu", devices=[device])

    coll = _connect_milvus()
    logger.info("Milvus connected, entities=%d", coll.num_entities)

    conn = _connect_pg()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT count(*) FROM legal_articles a
        WHERE a.created_at >= %s
        """,
        (since,),
    )
    total = cur.fetchone()[0]
    logger.info("PG articles since %s: %d", since.isoformat(), total)

    inserted = skipped = 0
    batches_since_flush = 0
    buffer: list[tuple] = []
    started = time.time()

    cur.execute(
        """
        SELECT a.id::text, a.article_number, a.content, COALESCE(a.tags, ''),
               l.name, COALESCE(l.law_type, '其他')
        FROM legal_articles a
        JOIN laws l ON l.id = a.law_id
        WHERE a.created_at >= %s
        ORDER BY a.id
        """,
        (since,),
    )

    def flush(rows: list[tuple]) -> tuple[int, int]:
        nonlocal batches_since_flush
        ids = [f"pg_{r[0]}" for r in rows]
        existing = _existing_ids(coll, ids)
        pending = [(rid, r) for rid, r in zip(ids, rows) if rid not in existing]
        if not pending:
            return 0, len(rows)
        texts = [_clean(r[2]) for _, r in pending]
        keep = [(rid, r, t) for (rid, r), t in zip(pending, texts) if len(t) >= 5]
        if not keep:
            return 0, len(rows)
        embeddings = _embed(model, [t for _, _, t in keep])
        if args.dry_run:
            return len(keep), len(rows) - len(keep)
        data = [
            [rid for rid, _, _ in keep],
            [_utf8_truncate(r[4], B_LAW_NAME) for _, r, _ in keep],
            [_utf8_truncate(r[1], B_ARTICLE_NUM) for _, r, _ in keep],
            [_utf8_truncate(t, B_CONTENT) for _, _, t in keep],
            [_utf8_truncate(r[3], B_TAGS) for _, r, _ in keep],
            [_utf8_truncate(r[5], B_CATEGORY) for _, r, _ in keep],
            embeddings,
        ]
        coll.insert(data)
        batches_since_flush += 1
        if batches_since_flush >= FLUSH_EVERY_N_BATCHES:
            coll.flush()
            batches_since_flush = 0
        return len(keep), len(rows) - len(keep)

    processed = 0
    for row in cur:
        buffer.append(row)
        if len(buffer) >= MILVUS_BATCH:
            n_ins, n_skip = flush(buffer)
            inserted += n_ins
            skipped += n_skip
            processed += len(buffer)
            buffer = []
            rate = processed / max(time.time() - started, 1e-6)
            logger.info("progress: %d/%d inserted=%d skipped=%d (%.1f rows/s)",
                        processed, total, inserted, skipped, rate)
    if buffer:
        n_ins, n_skip = flush(buffer)
        inserted += n_ins
        skipped += n_skip
        processed += len(buffer)

    if not args.dry_run:
        coll.flush()
    cur.close()
    conn.close()
    elapsed = time.time() - started
    logger.info("done: processed=%d inserted=%d skipped=%d in %.1fs (%.1f rows/s)%s",
                processed, inserted, skipped, elapsed,
                processed / max(elapsed, 1e-6), " [DRY RUN]" if args.dry_run else "")


if __name__ == "__main__":
    main()
