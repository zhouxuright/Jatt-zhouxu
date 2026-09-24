"""补齐 Milvus 中缺失的法条向量（P2-6）。

背景
----
PostgreSQL ``legal_articles`` 是权威源（每条法条一行），Milvus
``legal_articles`` 集合是检索用向量副本。两者因历史导入中断/新增法条而
产生"差集"——库里有、向量库里没有，表现为"这条法条明明收录了却检索不到"。

本脚本：
1. 从 PostgreSQL 读出全部法条（含法名、标签、分类）；
2. 用 ``query_iterator`` 流式拉取 Milvus 已有 ID 集合（不把全量 ID 读进内存两次）；
3. 求差集，分批向量化并写入 Milvus；
4. 结束后回读校验，输出补齐条数与最终一致率。

用法（用 ``backfill`` 一次性高内存服务，避免挤占线上 backend 的 4G 限额）::

    docker compose --profile tools run --rm backfill python scripts/backfill_milvus_articles.py --dry-run
    docker compose --profile tools run --rm backfill python scripts/backfill_milvus_articles.py
    docker compose --profile tools run --rm backfill python scripts/backfill_milvus_articles.py --max 5000

字段上限（重要）
----------------
Milvus 的 varchar ``max_length`` 按 **UTF-8 字节**计，不是字符数（中文 1 字 = 3
字节）。集合 ``legal_articles`` 现为 ``law_name=256B`` / ``content=8192B``，
而 PostgreSQL 侧实际最大值为 ``law_name=396B`` / ``content=42,959B``——共 **483
条**法条因此无法写入，且 Milvus 的拒绝是**批级**的（一条超限整批失败，表现为
"怎么补都补不齐"）。

脚本默认 fail-fast 报出这些行；给 ``--skip-overflow`` 时改为跳过并输出清单，
其余部分照常补齐。

性能说明
--------
本机为纯 CPU 推理（无 CUDA）。BGE-M3 逐个编码时的耗时与"批内最长序列"
成正比，而法条平均仅 ~127 字、p90 ~226 字——若不排序，一个大 batch 会被
批内极少数长条文（p99 ~456 字）拖成"全体按最长补齐"，浪费 2~4 倍算力。

因此本脚本会先按 ``len(content)`` 排序再分批，使每个 batch 的长度高度一致，
配合较小的 ``--batch-size``（默认 64）把无效 padding 降到最低。实测在
4 CPU 下可从 ~0.73 篇/秒 提升到 ~1.3 篇/秒。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

MILVUS_HOST = "milvus"
MILVUS_PORT = "19530"
COLLECTION = "legal_articles"

#: PostgreSQL 侧查询：法条 + 所属法律名称/分类
_SQL = text(
    """
    SELECT a.id            AS id,
           l.name          AS law_name,
           a.article_number AS article_number,
           a.content        AS content,
           COALESCE(a.tags, '')      AS tags,
           COALESCE(l.category, '')  AS category
    FROM legal_articles a
    JOIN laws l ON l.id = a.law_id
    WHERE a.content IS NOT NULL AND a.content <> ''
    ORDER BY a.id
    """
)


async def _load_postgres_rows() -> list[dict]:
    from app.core.database import async_session_factory

    async with async_session_factory() as db:
        result = await db.execute(_SQL)
        rows = [
            {
                "id": r.id,
                "law_name": r.law_name or "",
                "article_number": r.article_number or "",
                "content": r.content or "",
                "tags": r.tags or "",
                "category": r.category or "",
            }
            for r in result
        ]
    print(f"[1/4] PostgreSQL 法条总数: {len(rows):,}")
    return rows


# 业务键归一化收敛到 app.rag.milvus_schema，与去重脚本共用同一实现，
# 避免"回填用一套键、去重用另一套"造成互不认可。
from app.rag.milvus_schema import normalize_text as _norm  # noqa: E402
from app.rag.milvus_schema import business_key as _key  # noqa: E402


def _load_milvus_keys() -> set[str]:
    from pymilvus import Collection, connections

    connections.connect(host=MILVUS_HOST, port=MILVUS_PORT)
    collection = Collection(COLLECTION)
    collection.load()

    keys: set[str] = set()
    try:
        iterator = collection.query_iterator(
            expr='id != ""',
            output_fields=["law_name", "article_number"],
            batch_size=5000,
        )
        while True:
            batch = iterator.next()
            if not batch:
                iterator.close()
                break
            keys.update(_key(item.get("law_name", ""), item.get("article_number", "")) for item in batch)
    except Exception as exc:  # noqa: BLE001 - 回退到分页 query
        print(f"      query_iterator 不可用({exc})，回退分页查询")
        offset = 0
        while True:
            batch = collection.query(
                expr='id != ""',
                output_fields=["law_name", "article_number"],
                limit=5000, offset=offset,
            )
            if not batch:
                break
            keys.update(_key(item.get("law_name", ""), item.get("article_number", "")) for item in batch)
            offset += len(batch)

    print(f"[2/4] Milvus 已有向量键数: {len(keys):,}")
    return keys


def _insert_missing(missing: list[dict], batch_size: int) -> int:
    """向量化并写入缺失法条。

    先按文本长度排序：让每个 batch 的序列长度高度一致，避免"批内最长补齐"
    造成的成倍无效计算（纯 CPU 环境下这是最大的性能杠杆）。
    """
    from pymilvus import Collection, connections

    from app.services.model_registry import ModelRegistry

    connections.connect(host=MILVUS_HOST, port=MILVUS_PORT)
    collection = Collection(COLLECTION)
    collection.load()

    model = ModelRegistry.get_embedding_model()

    # 载荷字段的字节上限：写入时按 UTF-8 字节截断（与历史导入器一致）。
    # 注意嵌入用的是**未截断**的原文，与历史行为一致；检索质量不受影响。
    from app.rag.milvus_schema import truncate_utf8

    payload_limits = {
        field.name: int(field.params["max_length"])
        for field in collection.schema.fields
        if getattr(field, "params", None) and "max_length" in field.params
        and field.name in ("content", "tags", "category")
    }

    # 长度排序 → 批内 padding 最小化（不改变写入内容，仅改变处理顺序）
    missing = sorted(missing, key=lambda c: len(c["content"]))

    inserted = 0
    total = len(missing)
    t0 = time.time()
    n_batches = (total + batch_size - 1) // batch_size

    for bi, start in enumerate(range(0, total, batch_size), start=1):
        chunk = missing[start:start + batch_size]
        texts = [f"{c['law_name']} {c['article_number']}\n{c['content']}" for c in chunk]

        # 只算稠密向量：稀疏/ColBERT 本仓库不使用，显式关闭避免多余前向计算
        output = model.encode(
            texts,
            batch_size=len(texts),
            max_length=512,
            return_dense=True,
            return_sparse=False,
            return_colbert_vecs=False,
        )
        embeddings = [e.tolist() for e in output["dense_vecs"]]

        data = [
            [f"art_{c['id']}" for c in chunk],
            [c["law_name"] for c in chunk],
            [c["article_number"] for c in chunk],
            [truncate_utf8(c["content"], payload_limits.get("content", 8192)) for c in chunk],
            [truncate_utf8(c["tags"], payload_limits.get("tags", 512)) for c in chunk],
            [truncate_utf8(c["category"], payload_limits.get("category", 64)) for c in chunk],
            embeddings,
        ]
        collection.insert(data)
        inserted += len(chunk)

        elapsed = time.time() - t0
        rate = inserted / elapsed if elapsed else 0.0
        eta_min = (total - inserted) / rate / 60 if rate else 0.0
        print(
            f"      [{bi}/{n_batches}] {inserted:,}/{total:,} "
            f"({inserted / total * 100:5.1f}%) | {rate:5.2f} 篇/秒 | "
            f"已用 {elapsed / 60:5.1f}min | 剩余约 {eta_min:5.1f}min",
            flush=True,
        )

    collection.flush()
    collection.load()
    return inserted


def _preflight_byte_limits(
    missing: list[dict], skip_overflow: bool, skip_report: str | None
) -> list[dict]:
    """写入前校验待插值不会超出 Milvus varchar 的**字节**上限。

    为什么必须单独校验：Milvus 的 ``max_length`` 按 UTF-8 字节计（中文 1 字
    = 3 字节），而 PostgreSQL 侧我们习惯按字符看；且超限报错是**批级**的——
    一条超限会让整批 256 条全部写不进去，只在最后暴露成"怎么补都补不齐"。

    区分两类字段，处理方式不同：

    - **业务键字段**（``id`` / ``law_name`` / ``article_number``）：不能截断。
      截断后与 PostgreSQL 侧不再相等，差集校验会永远认为"缺失"，并可能让不同
      法律塌缩成同一个键。这类行只能放宽集合 schema 才能写入 → 默认 fail-fast，
      ``--skip-overflow`` 时跳过并输出清单。
    - **载荷字段**（``content`` / ``tags`` / ``category``）：按字节截断后写入。
      这与历史 ``OptimizedMilvusImporter`` 的做法一致（现状索引里本就有截断值），
      且嵌入只取前 512 token（≈512 汉字），8192B（≈2730 汉字）远大于嵌入窗口，
      **对检索质量无影响**；权威全文始终在 PostgreSQL，溯源接口读的是 PostgreSQL。

    返回过滤后的待写入列表。
    """
    from pymilvus import Collection, connections

    connections.connect(host=MILVUS_HOST, port=MILVUS_PORT)
    collection = Collection(COLLECTION)

    limits: dict[str, int] = {}
    for field in collection.schema.fields:
        params = getattr(field, "params", None) or {}
        if "max_length" in params:
            limits[field.name] = int(params["max_length"])

    if not limits:
        return missing

    key_fields = {"id", "law_name", "article_number"}

    def _key_over(r: dict) -> list[str]:
        return [
            f"{f}={len(str(r[f]).encode('utf-8'))}B>{limits[f]}B"
            for f in key_fields & limits.keys()
            if len(str(r[f]).encode("utf-8")) > limits[f]
        ]

    print("[3.5/4] 字节上限预检 …")
    payload_truncated = 0
    for field, limit in limits.items():
        worst = max(missing, key=lambda r: len(str(r[field]).encode("utf-8")), default=None)
        if worst is None:
            continue
        size = len(str(worst[field]).encode("utf-8"))
        kind = "键" if field in key_fields else "载荷"
        over = size > limit
        if over and field not in key_fields:
            payload_truncated = sum(
                1 for r in missing if len(str(r[field]).encode("utf-8")) > limit
            )
        print(
            f"        {field:16s}[{kind}] max={size:>6d}B / 上限 {limit:>6d}B  "
            f"[{'超限' if over else 'OK'}"
            f"{'→写入时截断' if over and field not in key_fields else ''}]"
        )

    if payload_truncated:
        print(f"        载荷字段超限 {payload_truncated} 条，将在写入时按字节截断（不影响嵌入/检索）")

    overflow = [(r, _key_over(r)) for r in missing]
    overflow = [(r, why) for r, why in overflow if why]

    if not overflow:
        print("        业务键字段全部在限内，无需跳过。")
        return missing

    if not skip_overflow:
        print(
            f"\n[FAIL] {len(overflow)} 条的业务键字段超出 Milvus varchar 上限，"
            "已中止以免整批写入失败（截断键字段会让差集校验永远对不上）："
        )
        for r, why in overflow[:20]:
            print(f"  - {r['law_name'][:24]}… {r['article_number']} id={r['id']} :: {', '.join(why)}")
        if len(overflow) > 20:
            print(f"  … 其余 {len(overflow) - 20} 条略")
        print(
            "\n处理方式：\n"
            "  a) 放宽集合 schema（scripts/widen_milvus_varchar.py 需 Milvus ≥ 2.5；\n"
            "     2.4.x 需重建集合后搬迁向量）；或\n"
            "  b) 加 --skip-overflow 先补完其余部分，这批另案处理。"
        )
        raise SystemExit(2)

    print(
        f"\n[WARN] 跳过 {len(overflow)} 条（业务键字段超限，"
        f"其余 {len(missing) - len(overflow):,} 条继续回填）："
    )
    for r, why in overflow:
        print(f"  - {r['id']} | {r['law_name'][:40]} | {r['article_number']} | {', '.join(why)}")

    if skip_report:
        import csv

        with open(skip_report, "w", newline="", encoding="utf-8-sig") as fh:
            writer = csv.writer(fh)
            writer.writerow(["id", "law_name", "article_number", "reason",
                             "law_name_bytes", "content_bytes"])
            for r, why in overflow:
                writer.writerow([
                    r["id"], r["law_name"], r["article_number"], "; ".join(why),
                    len(r["law_name"].encode("utf-8")), len(r["content"].encode("utf-8")),
                ])
        print(f"        跳过清单已写入: {skip_report}")

    return [r for r in missing if not _key_over(r)]


async def main(
    dry_run: bool,
    batch_size: int,
    max_items: int | None,
    skip_overflow: bool = False,
    skip_report: str | None = None,
) -> None:
    t0 = time.time()
    rows = await _load_postgres_rows()
    milvus_keys = _load_milvus_keys()

    missing = [r for r in rows if _key(r["law_name"], r["article_number"]) not in milvus_keys]
    print(f"[3/4] 缺失向量条数: {len(missing):,}")
    if max_items is not None:
        missing = missing[:max_items]
        print(f"      --max 截断为: {len(missing):,}")

    if dry_run:
        for r in missing[:5]:
            print(f"      e.g. {r['law_name']} {r['article_number']} ({r['id'][:12]}…)")
        print("[4/4] dry-run 结束，未写入。")
        return

    if not missing:
        print("[4/4] 无需补齐，向量库与权威库已一致。")
        return

    missing = _preflight_byte_limits(missing, skip_overflow, skip_report)
    if not missing:
        print("[4/4] 预检后无可写入条目。")
        return

    inserted = _insert_missing(missing, batch_size)

    # 回读校验
    verify_keys = _load_milvus_keys()
    still_missing = [
        r for r in rows if _key(r["law_name"], r["article_number"]) not in verify_keys
    ]
    coverage = (len(rows) - len(still_missing)) / len(rows) * 100 if rows else 100.0
    print(
        f"[4/4] 已写入 {inserted:,} 条 | 剩余缺失 {len(still_missing):,} | "
        f"覆盖率 {coverage:.2f}% | 耗时 {time.time() - t0:.1f}s"
    )
    if still_missing:
        field_overflow = [
            r for r in still_missing
            if len(str(r["law_name"]).encode("utf-8")) > 256
            or len(str(r["content"]).encode("utf-8")) > 8192
        ]
        other = len(still_missing) - len(field_overflow)
        print(
            f"      其中：字段超限不可写 {len(field_overflow):,} 条，"
            f"其他原因 {other:,} 条（其他原因应为 0，否则需复查）"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="补齐 Milvus 缺失法条向量")
    parser.add_argument("--dry-run", action="store_true", help="只统计差集，不写入")
    parser.add_argument(
        "--batch-size",
        type=int,
        default=64,
        help="向量化批大小（已按长度排序，64 在 CPU 上性价比最高）",
    )
    parser.add_argument("--max", type=int, default=None, help="最多补齐条数")
    parser.add_argument(
        "--skip-overflow",
        action="store_true",
        help="跳过超出 Milvus varchar 字节上限的法条（默认 fail-fast，不静默丢数据）",
    )
    parser.add_argument(
        "--skip-report",
        default=None,
        help="把被跳过的超限法条清单写成 CSV（建议指向挂载出来的目录）",
    )
    args = parser.parse_args()
    asyncio.run(
        main(args.dry_run, args.batch_size, args.max, args.skip_overflow, args.skip_report)
    )
