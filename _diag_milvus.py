# -*- coding: utf-8 -*-
"""诊断补录条文：存在性 + 向量有效性 + 向量检索排名"""
import warnings

warnings.filterwarnings("ignore")
from pymilvus import connections, Collection

connections.connect(host="milvus", port="19530")
c = Collection("legal_articles")

targets = ["第三百四十条", "第三百四十二条", "第九百六十六条"]

print("=== 1. 存在性 + 向量检查 ===")
res = c.query(
    expr='law_name == "中华人民共和国民法典" and article_number in ["第三百四十条", "第三百四十二条", "第九百六十六条"]',
    output_fields=["id", "article_number", "embedding"],
    limit=10,
)
found = {r["article_number"]: r for r in res}
for t in targets:
    if t in found:
        v = found[t]["embedding"]
        nonzero = any(x != 0.0 for x in v)
        print(f"{t}: id={found[t]['id']}, dim={len(v)}, nonzero={nonzero}")
    else:
        print(f"{t}: NOT FOUND")

print("\n=== 2. 向量检索排名（模拟 /law/search 的召回） ===")
from FlagEmbedding import BGEM3FlagModel

model = BGEM3FlagModel("BAAI/bge-m3", use_fp16=True)
q = "民法典第三百四十条 土地经营权"
emb = model.encode([q])["dense_vecs"][0].tolist()
results = c.search(
    data=[emb],
    anns_field="embedding",
    param={"metric_type": "COSINE", "params": {"nprobe": 16}},
    limit=10,
    output_fields=["law_name", "article_number"],
)
for hit in results[0]:
    ent = hit["entity"]
    mark = " <<<<" if ent["article_number"] in targets and ent["law_name"] == "中华人民共和国民法典" else ""
    print(f"score={hit['distance']:.4f} {ent['law_name']} {ent['article_number']}{mark}")
