# -*- coding: utf-8 -*-
"""Probe Milvus ID-space composition and law coverage vs PostgreSQL (v2).

Improvements over v1:
  - name normalization: strip (YYYY-MM-DD) date suffix and 中华人民共和国 prefix
  - variants also include the last dash-segment (民法典-合同编 -> 合同编)
  - quantifies pg_* vs art_* dual coverage (duplicate-content suspicion)
"""
from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

import psycopg2

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[2]
PG_PORT = 5433
MILVUS_HOST = "localhost"
MILVUS_PORT = "19530"
COLLECTION = "legal_articles"
PREFIX = "中华人民共和国"
DATE_SUFFIX = re.compile(r"[（(]\d{4}-\d{2}-\d{2}[)）]\s*$")


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


def main() -> None:
    from pymilvus import Collection, connections

    connections.connect(host=MILVUS_HOST, port=MILVUS_PORT)
    coll = Collection(COLLECTION)
    coll.load()

    it = coll.query_iterator(
        batch_size=2048,
        expr="",
        output_fields=["id", "law_name"],
    )
    raw_names: set[str] = set()
    art_names: set[str] = set()
    pg_by_law: Counter = Counter()
    id_prefix = Counter()
    n = 0
    while True:
        rows = it.next()
        if not rows:
            it.close()
            break
        n += len(rows)
        for r in rows:
            name = r["law_name"]
            raw_names.add(name)
            if r["id"].startswith("art_"):
                art_names.add(name)
            else:
                pg_by_law[name] += 1
            id_prefix[r["id"].split("_", 1)[0]] += 1

    print(f"entities={n} prefixes={dict(id_prefix)} distinct_names={len(raw_names)}")

    # normalized lookup: raw Milvus names + their normalized forms
    lookup = set(raw_names) | {norm(x) for x in raw_names}

    # dual coverage: pg_* rows whose (normalized) law also has art_* rows
    art_norm = {norm(x) for x in art_names} | art_names
    dual_rows = sum(c for name, c in pg_by_law.items()
                    if name in art_names or norm(name) in art_norm)
    print(f"pg_* rows under laws ALSO covered by art_*: {dual_rows} / {sum(pg_by_law.values())}")

    conn = psycopg2.connect(
        host="localhost", port=PG_PORT, dbname="legal_assistant",
        user="postgres", password=_pg_password(),
    )
    cur = conn.cursor()
    cur.execute(
        """
        SELECT l.id::text, l.name, l.created_at,
               (SELECT count(*) FROM legal_articles a WHERE a.law_id = l.id) AS arts
        FROM laws l
        """
    )
    laws = cur.fetchall()
    conn.close()

    absent = []
    for lid, name, created, arts in laws:
        if arts > 0 and not (variants(name) & lookup):
            absent.append((name, arts, created))

    total_arts = sum(a for _, a, _ in absent)
    batch = [(n_, a) for n_, a, c in absent
             if c.strftime("%Y-%m-%d") in ("2026-08-28", "2026-08-29")]

    print(f"\nPG laws={len(laws)}  ZERO-coverage laws={len(absent)} (articles={total_arts})")
    print(f"  of which 8/28-29 FLK batch: {len(batch)} laws (articles={sum(a for _, a in batch)})")
    print("\nALL absent laws:")
    for name, arts, _ in sorted(absent, key=lambda x: -x[1]):
        print(f"  {name}: {arts}")

    with open(ROOT / "backend" / "scripts" / "_absent_laws.txt", "w", encoding="utf-8") as f:
        for name, arts, _ in sorted(absent, key=lambda x: -x[1]):
            f.write(f"{name}\t{arts}\n")
    print("\nabsent law list written to backend/scripts/_absent_laws.txt")


if __name__ == "__main__":
    main()
