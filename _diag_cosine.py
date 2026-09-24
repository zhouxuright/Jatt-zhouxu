# -*- coding: utf-8 -*-
"""直接余弦相似度测试：区分 embedding 质量问题 vs ANN 索引召回问题"""
import warnings

warnings.filterwarnings("ignore")
import math

from pymilvus import connections, Collection

connections.connect(host="milvus", port="19530")
c = Collection("legal_articles")

# 取出 340 及相邻 339/341 的存储 embedding
res = c.query(
    expr='law_name == "中华人民共和国民法典" and article_number in ["第三百三十九条", "第三百四十条", "第三百四十二条"]',
    output_fields=["id", "article_number", "content", "embedding"],
    limit=10,
)
stored = {r["article_number"]: r for r in res}
for k, v in stored.items():
    print(f"{k}: id={v['id']} content={v['content'][:30]}...")

from FlagEmbedding import BGEM3FlagModel

model = BGEM3FlagModel("BAAI/bge-m3", use_fp16=True)
q = "民法典第三百四十条 土地经营权"
qemb = model.encode([q])["dense_vecs"][0].tolist()


def cos(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb)


print(f"\n查询: {q}")
for k in ["第三百三十九条", "第三百四十条", "第三百四十二条"]:
    if k in stored:
        sim = cos(qemb, stored[k]["embedding"])
        print(f"直接余弦相似度 {k}: {sim:.4f}")
