# -*- coding: utf-8 -*-
"""Targeted Milvus gap fill: insert articles of PG laws with ZERO Milvus coverage.

Why: a full pg_* sync would duplicate ~640k art_* (dataset-native) vectors.
This script first recomputes the absent-law set (name normalization: date
suffix, 中华人民共和国 prefix, last dash-segment), then embeds and inserts
ONLY those laws' articles with deterministic pg_{uuid} ids.

Usage (host, GPU venv):
    set HF_HUB_OFFLINE=1 & set TRANSFORMERS_OFFLINE=1
    python sync_milvus_gap.py [--dry-run]
"""
from __future__ import annotations

import argparse
import logging
import re
import sys
import time
from pathlib import Path

import psycopg2

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("milvus_gap")

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
PG_PORT = 5433
MILVUS_HOST = "localhost"
MILVUS_PORT = "19530"
COLLECTION = "legal_articles"
PREFIX = "中华人民共和国"
DATE_SUFFIX = re.compile(r"[（(]\d{4}-\d{2}-\d{2}[)）]\s*$")

EMBED_BATCH = 16
MILVUS_BATCH = 500
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
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("POSTGRES_PASSWORD="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError("POSTGRES_PASSWORD not found")


def norm(name: str) -> str:
    n = DATE_SUFFIX.sub("", name or "").strip()
    if n.startswith(PREFIX):
        n = n[len(PREFIX):]
    return n


def variants(name: str) -> set[str]:
    base = norm(name)
    v = {name, base, PREFIX + base}
    if "-" in base:
        seg = base.rsplit("-", 1)[-1]
        v.add(seg)
        v.add(PREFIX + seg)
    return {x for x in v if x}


def _utf8_truncate(s: str | None, max_bytes: int) -> str:
    if not s:
        return ""
    b = s.encode("utf-8")
    if len(b) <= max_bytes:
        return s
    return b[:max_bytes].decode("utf-8", errors="ignore")


def _clean(text: str | None) -> str:
    return re.sub(r"\s+", "", text or "").strip()


def _embed(model, texts: list[str]) -> list[list[float]]:
    out: list[list[float]] = []
    for j in range(0, len(texts), EMBED_BATCH):
        res = model.encode(texts[j:j + EMBED_BATCH], return_dense=True)
        out.extend(e.tolist() for e in res["dense_vecs"])
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    from pymilvus import Collection, connections

    connections.connect(host=MILVUS_HOST, port=MILVUS_PORT)
    coll = Collection(COLLECTION)
    coll.load()

    it = coll.query_iterator(batch_size=2048, expr="", output_fields=["law_name"])
    lookup: set[str] = set()
    while True:
        rows = it.next()
        if not rows:
            it.close()
            break
        for r in rows:
            lookup.add(r["law_name"])
            lookup.add(norm(r["law_name"]))
    logger.info("Milvus distinct law names (raw+norm): %d", len(lookup) // 2)

    conn = psycopg2.connect(
        host="localhost", port=PG_PORT, dbname="legal_assistant",
        user="postgres", password=_pg_password(),
    )
    cur = conn.cursor()
    cur.execute(
        """
        SELECT l.id::text, l.name FROM laws l
        WHERE EXISTS (SELECT 1 FROM legal_articles a WHERE a.law_id = l.id)
        """
    )
    absent_ids = [lid for lid, name in cur.fetchall()
                  if not (variants(name) & lookup)]
    logger.info("absent laws: %d", len(absent_ids))
    if not absent_ids:
        logger.info("nothing to do")
        return

    cur.execute(
        """
        SELECT a.id::text, a.article_number, a.content, COALESCE(a.tags, ''),
               l.name, COALESCE(l.law_type, '其他')
        FROM legal_articles a
        JOIN laws l ON l.id = a.law_id
        WHERE a.law_id = ANY(%s)
        ORDER BY a.id
        """,
        (absent_ids,),
    )
    rows = cur.fetchall()
    logger.info("articles to insert: %d", len(rows))

    import torch
    device = "cuda:0" if torch.cuda.is_available() else "cpu"
    logger.info("torch %s, device=%s", torch.__version__, device)
    from FlagEmbedding import BGEM3FlagModel
    model = BGEM3FlagModel("BAAI/bge-m3", use_fp16=device != "cpu", devices=[device])

    # idempotency: skip ids already present
    existing: set[str] = set()
    all_ids = [f"pg_{r[0]}" for r in rows]
    for i in range(0, len(all_ids), 500):
        chunk = all_ids[i:i + 500]
        expr = "id in [" + ",".join(f'"{x}"' for x in chunk) + "]"
        res = coll.query(expr=expr, output_fields=["id"], limit=len(chunk))
        existing.update(x["id"] for x in res)
    pending = [(rid, r) for rid, r in zip(all_ids, rows) if rid not in existing]
    logger.info("already present: %d, pending: %d", len(existing), len(pending))

    inserted = 0
    started = time.time()
    for i in range(0, len(pending), MILVUS_BATCH):
        batch = pending[i:i + MILVUS_BATCH]
        texts = [_clean(r[2]) for _, r in batch]
        keep = [(rid, r, t) for (rid, r), t in zip(batch, texts) if len(t) >= 5]
        if not keep:
            continue
        embeddings = _embed(model, [t for _, _, t in keep])
        data = [
            [rid for rid, _, _ in keep],
            [_utf8_truncate(r[4], B_LAW_NAME) for _, r, _ in keep],
            [_utf8_truncate(r[1], B_ARTICLE_NUM) for _, r, _ in keep],
            [_utf8_truncate(t, B_CONTENT) for _, _, t in keep],
            [_utf8_truncate(r[3], B_TAGS) for _, r, _ in keep],
            [_utf8_truncate(r[5], B_CATEGORY) for _, r, _ in keep],
            embeddings,
        ]
        if not args.dry_run:
            coll.insert(data)
        inserted += len(keep)
        logger.info("progress: %d/%d", inserted, len(pending))

    if not args.dry_run:
        coll.flush()
    cur.close()
    conn.close()
    logger.info("done: inserted=%d in %.1fs%s", inserted, time.time() - started,
                " [DRY RUN]" if args.dry_run else "")


if __name__ == "__main__":
    main()
