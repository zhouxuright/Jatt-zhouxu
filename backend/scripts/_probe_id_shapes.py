"""一次性探查：legal_articles 集合里 id 的形态分布。

目的：确认同一业务键下的重复实体究竟来自几个 ID 空间，从而决定去重时
"保留哪一份"（保留的向量必须是按权威正文计算的那份，否则检索质量下降）。

用法::
    docker compose --profile tools run --rm backfill python scripts/_probe_id_shapes.py
"""

from __future__ import annotations

import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.rag.milvus_schema import business_key  # noqa: E402

PATTERNS = [
    ("art_<8位数字>(位置型)", re.compile(r"^art_\d{8}$")),
    ("art_<uuid36>", re.compile(r"^art_[0-9a-fA-F-]{36}$")),
    ("pg_<uuid36>", re.compile(r"^pg_[0-9a-fA-F-]{36}$")),
    ("<uuid36>(裸UUID)", re.compile(r"^[0-9a-fA-F-]{36}$")),
    ("legal_<3位数字>(种子)", re.compile(r"^legal_\d{3}$")),
]


def shape_of(value: str) -> str:
    v = value or ""
    for label, pat in PATTERNS:
        if pat.match(v):
            return label
    return f"其他({v[:12]}…)"


def main() -> None:
    from pymilvus import Collection, connections

    connections.connect(host="milvus", port="19530")
    collection = Collection("legal_articles")
    collection.load()

    shapes: Counter[str] = Counter()
    # 每个业务键 -> {shape: 条数}
    key_shapes: defaultdict[str, Counter[str]] = defaultdict(Counter)

    iterator = collection.query_iterator(
        batch_size=8000, expr="", output_fields=["id", "law_name", "article_number"]
    )
    total = 0
    while True:
        batch = iterator.next()
        if not batch:
            iterator.close()
            break
        for item in batch:
            s = shape_of(item.get("id"))
            shapes[s] += 1
            key_shapes[
                business_key(item.get("law_name"), item.get("article_number"))
            ][s] += 1
        total += len(batch)
        print(f"    已扫描 {total:,} …", end="\r", flush=True)
    print()

    print(f"\n实体总数: {total:,}\n")
    print("=== ID 形态分布（全体） ===")
    for label, cnt in shapes.most_common():
        print(f"  {label:<28} {cnt:>9,}  ({cnt / total * 100:5.2f}%)")

    dup_keys = {k: v for k, v in key_shapes.items() if sum(v.values()) > 1}
    print(f"\n=== 重复业务键 {len(dup_keys):,} 个，其内部 ID 形态组合 TOP 10 ===")
    combo = Counter()
    for v in dup_keys.values():
        combo[" + ".join(f"{k}×{n}" for k, n in sorted(v.items()))] += 1
    for label, cnt in combo.most_common(10):
        print(f"  {cnt:>7,} 个键:  {label}")


if __name__ == "__main__":
    main()
