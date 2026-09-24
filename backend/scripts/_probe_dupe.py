import os
from pymilvus import Collection, connections

connections.connect(host="localhost", port="19530")
coll = Collection("legal_articles")
coll.load()

probes = [
    "中华人民共和国民法典-合同编",
    "中华人民共和国刑法-总则",
    "中华人民共和国劳动合同法",
    "中华人民共和国刑法",
]

for name in probes:
    res = coll.query(
        expr=f'law_name == "{name}"',
        output_fields=["id", "article_number"],
        limit=100,
    )
    art_ids = [r["id"] for r in res if r["id"].startswith("art_")]
    pg_ids = [r["id"] for r in res if r["id"].startswith("pg_")]
    print(f"{name}: total={len(res)} art_*={len(art_ids)} pg_*={len(pg_ids)} sample={res[0]['id'] if res else 'NONE'}")
