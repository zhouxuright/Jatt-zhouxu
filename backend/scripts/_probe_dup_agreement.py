"""一次性探查：重复业务键内部各副本的"内容是否一致"。

去重是**不可逆**操作，动手前必须回答一个前置问题：
同一业务键（法名+条号）下的多份实体，是"同一条法条的格式差异副本"，
还是"内容实质不同的两条法条"？

- 若只是格式差异（空白/全角）→ 折叠空白后 md5 相同 → 可安全去重。
- 若内容实质不同 → **不能**自动删，必须先查清来源。

另外统计"哪个 ID 形态的正文最长"，用于确定保留优先级（正文越长，
相对越是未截断版本，其向量所依据的文本也越完整）。

用法::
    docker compose --profile tools run --rm backfill
      python scripts/_probe_dup_agreement.py
"""

from __future__ import annotations

import hashlib
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.rag.milvus_schema import business_key, normalize_text  # noqa: E402

PATTERNS = [
    ("art_<uuid36>", re.compile(r"^art_[0-9a-fA-F-]{36}$")),
    ("pg_<uuid36>", re.compile(r"^pg_[0-9a-fA-F-]{36}$")),
    ("art_<8位数字>", re.compile(r"^art_\d{8}$")),
    ("<裸uuid36>", re.compile(r"^[0-9a-fA-F-]{36}$")),
]
FETCH_BATCH = 300


def shape_of(value: str) -> str:
    v = value or ""
    for label, pat in PATTERNS:
        if pat.match(v):
            return label
    return "其他"


def main() -> None:
    from pymilvus import Collection, connections

    connections.connect(host="milvus", port="19530")
    coll = Collection("legal_articles")
    coll.load()

    # ---- Pass 1: 找出重复业务键及其成员 id ----
    groups: defaultdict[str, list[str]] = defaultdict(list)
    it = coll.query_iterator(
        batch_size=8000, expr="", output_fields=["id", "law_name", "article_number"]
    )
    total = 0
    while True:
        batch = it.next()
        if not batch:
            it.close()
            break
        for r in batch:
            groups[business_key(r.get("law_name"), r.get("article_number"))].append(r.get("id"))
        total += len(batch)
    dup = {k: v for k, v in groups.items() if len(v) > 1}
    involved = sorted({i for v in dup.values() for i in v})
    print(f"实体总数 {total:,}｜重复键 {len(dup):,}｜涉及实体 {len(involved):,}\n")

    # ---- Pass 2: 取这些实体的正文 ----
    content: dict[str, str] = {}
    for s in range(0, len(involved), FETCH_BATCH):
        chunk = involved[s:s + FETCH_BATCH]
        expr = "id in [" + ",".join(f'"{x}"' for x in chunk) + "]"
        for r in coll.query(expr=expr, output_fields=["id", "content"], limit=len(chunk)):
            content[r["id"]] = r.get("content") or ""
        print(f"    取回正文 {min(s + FETCH_BATCH, len(involved)):,}/{len(involved):,}",
              end="\r", flush=True)
    print()

    def _strict(c: str) -> str:
        """删除**全部**空白后再比对。

        注意不能用 ``normalize_text``（它只把连续空白折叠成一个空格）：
        "第三十七条 国家完善…" 与 "第三十七条国家完善…" 折叠后仍不相等，
        会被误判成"内容实质不同"，从而把同一法条的格式差异算进风险桶。
        """
        return re.sub(r"\s+", "", c or "")

    eq_nows: list[str] = []       # 忽略空白后完全一致 → 纯副本，可安全去重
    prefix_only: list[str] = []   # 忽略空白后互为前缀 → 仅截断差异，保留长者可安全去重
    true_diff: list[str] = []     # 仍有实质差异 → 严禁盲删
    win_by_shape: Counter[str] = Counter()   # 每个键中"正文最长"的形态
    shape_max: defaultdict[str, int] = defaultdict(int)

    for key, ids in dup.items():
        stripped = {i: _strict(content.get(i, "")) for i in ids}
        uniq = set(stripped.values())
        if len(uniq) == 1:
            eq_nows.append(key)
        else:
            vals = sorted(uniq, key=len)
            if all(vals[k + 1].startswith(vals[k]) for k in range(len(vals) - 1)):
                prefix_only.append(key)
            else:
                true_diff.append(key)
        for i in ids:
            shape_max[shape_of(i)] = max(
                shape_max[shape_of(i)], len(content.get(i, "").encode("utf-8"))
            )
        longest = max(ids, key=lambda i: len(content.get(i, "").encode("utf-8")))
        win_by_shape[shape_of(longest)] += 1

    print("\n=== 重复键内容等价性（忽略**全部空白**后比对） ===")
    print(f"  完全一致（纯格式差异）  : {len(eq_nows):,}  ({len(eq_nows) / len(dup) * 100:5.2f}%)  → 可安全去重")
    print(f"  互为前缀（仅截断差异）  : {len(prefix_only):,}  ({len(prefix_only) / len(dup) * 100:5.2f}%)  → 保留长者可安全去重")
    print(f"  内容**实质不同**        : {len(true_diff):,}  ({len(true_diff) / len(dup) * 100:5.2f}%)  → 严禁盲删")
    safe = len(eq_nows) + len(prefix_only)
    print(f"\n  可安全去重合计: {safe:,} 个键 / {len(dup):,}")

    print("\n=== 每个重复键中「正文最长」的 ID 形态（保留候选） ===")
    for label, cnt in win_by_shape.most_common():
        print(f"  {label:<16} {cnt:>8,} 个键")

    print("\n=== 各 ID 形态的正文最大字节数 ===")
    for label, mx in sorted(shape_max.items(), key=lambda x: -x[1]):
        print(f"  {label:<16} {mx:>8,} 字节")

    if true_diff:
        print(f"\n=== 实质不同的样例（前 5 个） ===")
        for key in true_diff[:5]:
            name, art = key.split("\u0001")
            ids = dup[key]
            print(f"  《{name[:26]}》{art}")
            for i in sorted(ids, key=lambda x: -len(content.get(x, "")))[:4]:
                c = content.get(i, "")
                print(f"     {shape_of(i):<14} {i[:46]:<48} {len(c.encode('utf-8')):>7,}B  {_strict(c)[:46]}")


if __name__ == "__main__":
    main()
