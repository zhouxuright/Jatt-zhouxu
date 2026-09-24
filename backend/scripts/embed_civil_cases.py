# -*- coding: utf-8 -*-
"""向量化新导入的 ClaimGen-CN 民事案例 -> Milvus legal_cases 集合。

独立脚本：显式连接 docker 库(localhost:5433) 与 Milvus(localhost:19530)，
避免 app 配置里 DATABASE_URL 指向本机遗留库(5432) 的歧义。
BGE-M3 (GPU first) 编码 title+cause_of_action+summary[:800] -> 1024 维，
upsert 主键=id，幂等可重跑。
"""
import argparse
import asyncio

import asyncpg

DSN = "postgresql://postgres:postgres@localhost:5433/legal_assistant"
MILVUS_URI = "http://localhost:19530"
COLLECTION = "legal_cases"
BATCH = 128


def cuda_available():
    try:
        import torch
        return bool(torch.cuda.is_available())
    except Exception:
        return False


async def fetch_ids(conn, limit=0):
    q = (
        "SELECT id, case_number, title, court_name, case_type, cause_of_action, "
        "decision_date, summary, tags FROM court_cases "
        "WHERE tags LIKE '%claimgen%' ORDER BY id"
    )
    if limit and limit > 0:
        q += f" LIMIT {int(limit)}"
    return await conn.fetch(q)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="debug: embed only N rows")
    args = ap.parse_args()
    from FlagEmbedding import BGEM3FlagModel
    from pymilvus import MilvusClient

    device = "cuda" if cuda_available() else "cpu"
    print(f"[embed_civil] device={device}, loading BGE-M3 ...", flush=True)
    model = BGEM3FlagModel("BAAI/bge-m3", use_fp16=(device == "cuda"), device=device)

    async def run():
        conn = await asyncpg.connect(DSN)
        rows = await fetch_ids(conn, args.limit)
        await conn.close()
        print(f"[embed_civil] total civil rows to embed = {len(rows)}", flush=True)

        client = MilvusClient(uri=MILVUS_URI)
        before = client.get_collection_stats(COLLECTION)["row_count"]
        print(f"[embed_civil] collection={COLLECTION} start_entities={before}", flush=True)

        done = 0
        for i in range(0, len(rows), BATCH):
            batch = rows[i:i + BATCH]
            texts = [
                (r["title"] or "") + " " + (r["cause_of_action"] or "") + " "
                + (r["summary"] or "")[:800]
                for r in batch
            ]
            out = model.encode(texts, batch_size=BATCH, max_length=1024, return_dense=True)
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
            client.upsert(collection_name=COLLECTION, data=data)
            done += len(batch)
            if done % (BATCH * 10) == 0:
                client.flush(COLLECTION)
                print(f"[embed_civil] {done}/{len(rows)}", flush=True)

        client.flush(COLLECTION)
        after = client.get_collection_stats(COLLECTION)["row_count"]
        print(f"[embed_civil] DONE: embedded={done}  collection {before} -> {after}", flush=True)

    asyncio.run(run())


if __name__ == "__main__":
    main()