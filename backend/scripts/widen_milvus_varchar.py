"""放宽 Milvus ``legal_articles`` 集合的 varchar 字段上限（P2-6 前置修复）。

背景
----
**Milvus 的 ``max_length`` 以 UTF-8 字节数计，不是字符数**——这是本次回填
失败的根因，也是历史导入出现缺口的直接原因之一。中文一个字符占 3 字节，
因此"法名最长 132 字"看似远低于 256，实际可达 396 字节。

实测 PostgreSQL 侧真实字节长度与集合原上限的对比：

======================  ==============  ==============  ========
字段                    集合原上限(字节)  实际最大(字节)  超限行数
======================  ==============  ==============  ========
``law_name``            256             396             19
``content``             8192            42,959          465
``tags``                512             406             0
``category``            64              27              0
``article_number``      64              29              0
======================  ==============  ==============  ========

超大法条会被 Milvus 整批拒绝（``code=1100 ... exceeds max length``），
而拒绝是**批级**的：一条超限就会让整批 256 条都写不进去，表现为"回填永远
差最后一批"。

做法
----
用 ``MilvusClient.alter_collection_field`` 在线放宽（Milvus 2.4+ 支持对
varchar 增加 ``max_length``，属**加宽**类变更，不重写已有数据、不中断检索）。
调整后取值留有充足余量：``law_name → 512``、``content → 65535``
（65535 为 Milvus varchar 硬上限）。

幂等：已达目标值则跳过，可重复执行。

用法::

    docker compose --profile tools run --rm backfill python scripts/widen_milvus_varchar.py
    docker compose --profile tools run --rm backfill python scripts/widen_milvus_varchar.py --dry-run
"""

from __future__ import annotations

import argparse
import sys

MILVUS_URI = "http://milvus:19530"
COLLECTION = "legal_articles"

#: 字段 → 目标 max_length（字节）。仅列出需要放宽的字段。
TARGET_MAX_LENGTH: dict[str, int] = {
    "law_name": 512,
    "content": 65535,  # Milvus varchar 硬上限
}


def _current_limits() -> dict[str, int]:
    from pymilvus import Collection, connections

    connections.connect(host="milvus", port="19530")
    collection = Collection(COLLECTION)
    limits: dict[str, int] = {}
    for field in collection.schema.fields:
        params = getattr(field, "params", None) or {}
        if "max_length" in params:
            limits[field.name] = int(params["max_length"])
    return limits


def main(dry_run: bool) -> None:
    limits = _current_limits()
    print("=== 当前 varchar 上限 ===")
    for name, value in limits.items():
        print(f"  {name:16s} {value}")

    pending = {
        field: target
        for field, target in TARGET_MAX_LENGTH.items()
        if field in limits and limits[field] < target
    }

    if not pending:
        print("\n所有目标字段已达期望上限，无需调整。")
        return

    print("\n=== 待放宽 ===")
    for field, target in pending.items():
        print(f"  {field:16s} {limits[field]} → {target}")

    if dry_run:
        print("\n[dry-run] 未做修改。")
        return

    from pymilvus import MilvusClient

    client = MilvusClient(uri=MILVUS_URI)
    for field, target in pending.items():
        client.alter_collection_field(
            collection_name=COLLECTION,
            field_name=field,
            field_params={"max_length": target},
        )
        print(f"  已放宽 {field} → {target}")

    print("\n=== 调整后 ===")
    for name, value in _current_limits().items():
        print(f"  {name:16s} {value}")

    # 校验：放宽必须真正生效，否则后续回填仍会失败
    after = _current_limits()
    failed = [
        f"{f}({after.get(f)}<{t})"
        for f, t in TARGET_MAX_LENGTH.items()
        if after.get(f, 0) < t
    ]
    if failed:
        print(f"\n[FAIL] 放宽未生效: {', '.join(failed)}")
        sys.exit(1)
    print("\n[OK] 放宽已生效。")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="放宽 Milvus varchar 字段上限")
    parser.add_argument("--dry-run", action="store_true", help="只打印差异，不修改")
    args = parser.parse_args()
    main(args.dry_run)
