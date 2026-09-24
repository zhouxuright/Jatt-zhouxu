# -*- coding: utf-8 -*-
"""Empirically test whether Milvus VARCHAR max_length counts chars or bytes."""
from pymilvus import Collection, connections

connections.connect(host="milvus", port="19530")
c = Collection("legal_articles")

long_zh = "法" * 8000  # 8000 chars = 24000 UTF-8 bytes
data = [
    ["__len_probe__"],           # id
    ["长度测试"],                 # law_name
    ["第一条"],                   # article_number
    [long_zh],                   # content
    [""],                        # tags
    ["测试"],                     # category
    [[0.0] * 1024],              # embedding
]
try:
    c.insert(data)
    print("INSERT OK: 8000 chars (24000 bytes) accepted -> max_length counts chars")
except Exception as exc:
    print(f"INSERT FAILED: {exc}")
