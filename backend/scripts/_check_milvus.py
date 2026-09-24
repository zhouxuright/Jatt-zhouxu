from pymilvus import MilvusClient

c = MilvusClient(uri="http://localhost:19530")
print("stats:", c.get_collection_stats("legal_cases"))
res = c.query(
    collection_name="legal_cases",
    filter="tags like 'huggingface,claimgen-cn,civil'",
    output_fields=["id", "case_number", "case_type", "cause_of_action"],
    limit=5,
)
print("claimgen rows in milvus:", len(res))
for r in res:
    print("  ", r)