# -*- coding: utf-8 -*-
"""向量化 court_cases -> Milvus legal_cases 集合。

设计要点：
- **不再硬编码** `localhost:5433` / `localhost:19530`：DSN 与 Milvus 地址一律
  从环境变量读取（``DATABASE_URL`` / ``MILVUS_HOST`` / ``MILVUS_PORT``），
  否则脚本只能在当年那台开发机上跑，进容器就必然失败。
- 默认处理 **全部** court_cases；``--tag`` 可按 tags 过滤（早期只导入了
  ClaimGen-CN 民事案例，用 ``--tag claimgen`` 可复现旧行为）。
- 断点续跑：先读 Milvus 已存在的 id 集合，跳过已入库行，重复执行安全。
- 文本口径与线上检索侧保持一致：``title + cause_of_action + summary[:800]``，
  保证写入向量与查询向量同分布。
- BGE-M3 固定 1024 维（与集合 schema 的 dim 一致，见 MILVUS_DIMENSION）。
"""
import argparse
import asyncio
import os
import re

import asyncpg

COLLECTION = "legal_cases"
BATCH = 128

DSN_FALLBACK = "postgresql://postgres:postgres@localhost:5433/legal_assistant"
MILVUS_URI_FALLBACK = "http://localhost:19530"


def resolve_dsn() -> str:
    raw = os.environ.get("DATABASE_URL") or DSN_FALLBACK
    # asyncpg 不认识 SQLAlchemy 的驱动前缀
    return re.sub(r"^postgresql\+\w+://", "postgresql://", raw, count=1)


def resolve_milvus_uri() -> str:
    uri = os.environ.get("MILVUS_URI")
    if uri:
        return uri
    host = os.environ.get("MILVUS_HOST", "localhost")
    port = os.environ.get("MILVUS_PORT", "19530")
    return f"http://{host}:{port}"


def cuda_available() -> bool:
    try:
        import torch

        return bool(torch.cuda.is_available())
    except Exception:
        return False


def build_text(row) -> str:
    return (
        (row["title"] or "")
        + " "
        + (row["cause_of_action"] or "")
        + " "
        + (row["summary"] or "")[:800]
    )


async def fetch_rows(limit: int, tag: str, skip_ids: set[str]) -> list:
    dsn = resolve_dsn()
    conn = await asyncpg.connect(dsn)
    try:
        sql = (
            "SELECT id, case_number, title, court_name, case_type, cause_of_action, "
            "decision_date, summary, tags FROM court_cases"
        )
        where = []
        args = []
        if tag:
            args.append(f"%{tag}%")
            where.append(f"tags LIKE ${len(args)}")
        sql += (" WHERE " + " AND ".join(where)) if where else ""
        sql += " ORDER BY id"
        rows = await conn.fetch(sql, *args)
    finally:
        await conn.close()
    if skip_ids:
        rows = [r for r in rows if str(r["id"]) not in skip_ids]
    if limit and limit > 0:
        rows = rows[:limit]
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="debug: embed only N rows")
    ap.add_argument("--tag", default="", help="only rows whose tags contain this substring")
    ap.add_argument("--batch", type=int, default=BATCH)
    args = ap.parse_args()

    from FlagEmbedding import BGEM3FlagModel
    from pymilvus import MilvusClient

    device = "cuda" if cuda_available() else "cpu"
    uri = resolve_milvus_uri()
    # BGE_M3_PATH 可指向本地快照目录，用于离线/无 HF cache 的环境（容器外的
    # 一次性任务就靠它，避免再次触发 huggingface_hub 的下载与写缓存逻辑）。
    model_path = os.environ.get("BGE_M3_PATH") or "BAAI/bge-m3"
    print(f"[embed_cases] milvus={uri} device={device} model={model_path}", flush=True)
    model = BGEM3FlagModel(model_path, use_fp16=(device == "cuda"), device=device)

    client = MilvusClient(uri=uri)
    if not client.has_collection(COLLECTION):
        raise SystemExit(f"collection {COLLECTION} does not exist -- start the backend once so it creates it")

    try:
        client.load_collection(COLLECTION)
    except Exception as exc:
        print(f"[embed_cases] load_collection warn: {exc}", flush=True)

    before = int(client.get_collection_stats(COLLECTION).get("row_count", 0))
    existing: set[str] = set()
    try:
        # Milvus 单次 query 的 offset+limit 上限是 16384，不能一次拉 200k id。
        # query_iterator 会按 batch_size 自动翻页，用于判例量级增长后的续跑判断。
        for page in client.query_iterator(
            collection_name=COLLECTION,
            batch_size=4096,
            filter="",
            output_fields=["id"],
        ):
            existing.update(r["id"] for r in page)
    except Exception as exc:
        print(f"[embed_cases] cannot list existing ids (fresh collection?): {exc}", flush=True)
    print(f"[embed_cases] start rows={before} existing_ids={len(existing)}", flush=True)

    rows = asyncio.run(fetch_rows(args.limit, args.tag, existing))
    print(f"[embed_cases] to embed = {len(rows)} (tag filter={args.tag or '<none>'})", flush=True)
    if not rows:
        print("[embed_cases] nothing to do", flush=True)
        return

    done = 0
    bs = args.batch
    for i in range(0, len(rows), bs):
        batch = rows[i : i + bs]
        out = model.encode(
            [build_text(r) for r in batch],
            # CPU 环境下 encode batch 直接决定峰值内存：4 核机器上 8 条 × 1024
            # token 的激活约 1.5GB，与 2.2GB 权重一起刚好落在 5GB 配额内。
            batch_size=min(bs, 8),
            max_length=1024,
            return_dense=True,
        )
        vecs = out["dense_vecs"].tolist()
        data = [
            {
                "id": str(r["id"])[:64],
                "case_number": (r["case_number"] or "")[:128],
                "title": (r["title"] or "")[:512],
                "court_name": (r["court_name"] or "")[:256],
                "case_type": (r["case_type"] or "")[:64],
                "cause_of_action": (r["cause_of_action"] or "")[:256],
                "decision_date": (r["decision_date"] or "")[:32],
                "summary": (r["summary"] or "")[:8192],
                "tags": (r["tags"] or "")[:512],
                "embedding": v,
            }
            for r, v in zip(batch, vecs)
        ]
        client.upsert(COLLECTION, data)
        done += len(batch)
        print(f"[embed_cases] {done}/{len(rows)}", flush=True)

    client.flush(COLLECTION)
    after = int(client.get_collection_stats(COLLECTION).get("row_count", 0))
    print(f"[embed_cases] DONE: embedded={done} collection {before} -> {after}", flush=True)


if __name__ == "__main__":
    main()
