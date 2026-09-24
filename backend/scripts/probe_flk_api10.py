# -*- coding: utf-8 -*-
"""Probe 10: sxx status distribution + Milvus count + embedding speed."""
import time

import httpx

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/152.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://flk.npc.gov.cn/search",
    "Origin": "https://flk.npc.gov.cn",
    "Content-Type": "application/json;charset=UTF-8",
}
BASE = "https://flk.npc.gov.cn"


def payload(code_ids, page=1, size=50):
    return {
        "searchRange": 1, "sxrq": [], "gbrq": [], "searchType": 2, "sxx": [],
        "gbrqYear": [], "flfgCodeId": code_ids, "zdjgCodeId": [],
        "searchContent": "", "orderByParam": {"order": "-1", "sort": ""},
        "pageNum": page, "pageSize": size,
    }


def check_sxx(client):
    cats = {
        "法律": [101, 102, 110, 120, 130, 140, 150, 155, 160, 170, 180, 190, 195, 200],
        "行政法规": [201, 210, 215],
        "司法解释": [311, 320, 330, 340, 350],
    }
    for name, ids in cats.items():
        dist = {}
        samples = {}
        for page in range(1, 6):
            r = client.post(f"{BASE}/law-search/search/list", json=payload(ids, page, 50))
            rows = r.json().get("rows") or []
            for row in rows:
                s = row.get("sxx")
                dist[s] = dist.get(s, 0) + 1
                if s not in samples:
                    samples[s] = row["title"]
        print(f"{name} sxx dist (first 250): {dist}")
        for s, t in samples.items():
            print(f"  sxx={s}: {t}")


def check_milvus():
    try:
        from pymilvus import Collection, connections
        connections.connect(host="legal_milvus", port="19530")
        c = Collection("legal_articles")
        print("milvus legal_articles entities:", c.num_entities)
    except Exception as exc:
        print("milvus check failed:", str(exc)[:200])


def check_embedding_speed():
    try:
        from app.services.model_registry import ModelRegistry
        model = ModelRegistry.get_embedding_model()
        texts = ["中华人民共和国民法典第一条"] * 16
        t0 = time.time()
        out = model.encode(texts, return_dense=True)
        dt = time.time() - t0
        print(f"embedding 16 texts took {dt:.2f}s -> {16/dt:.1f} texts/s, dim={out['dense_vecs'].shape}")
    except Exception as exc:
        print("embedding check failed:", str(exc)[:300])


if __name__ == "__main__":
    with httpx.Client(headers=HEADERS, timeout=60, follow_redirects=True) as client:
        check_sxx(client)
    print()
    check_milvus()
    check_embedding_speed()
