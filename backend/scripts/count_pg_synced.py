# -*- coding: utf-8 -*-
"""Count pg_-prefixed (incrementally synced) articles in the Milvus collection."""
from pymilvus import Collection, connections

connections.connect(host="milvus", port="19530")
c = Collection("legal_articles")
print("total entities:", c.num_entities)
r = c.query(expr='id like "pg_%"', output_fields=["count(*)"])
print("pg_ synced:", r)
