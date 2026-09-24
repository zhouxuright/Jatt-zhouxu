# -*- coding: utf-8 -*-
"""Probe max content length stored in Milvus to determine max_length semantics."""
from pymilvus import Collection, connections

connections.connect(host="milvus", port="19530")
c = Collection("legal_articles")

max_chars = 0
max_bytes = 0
n = 0
for offset in (0, 4000, 8000, 12000, 16000):
    res = c.query(expr='id != ""', output_fields=["content"], limit=200,
                  offset=offset)
    for row in res:
        t = row["content"] or ""
        max_chars = max(max_chars, len(t))
        max_bytes = max(max_bytes, len(t.encode("utf-8")))
        n += 1
print(f"rows={n} max_chars={max_chars} max_bytes={max_bytes}")
