import warnings
warnings.filterwarnings("ignore")
from pymilvus import connections, Collection

connections.connect(host="milvus", port="19530")
c = Collection("legal_articles")

res = c.query(
    expr='law_name == "中华人民共和国民法典" and article_number == "第三百三十九条"',
    output_fields=["id", "law_name", "article_number", "tags", "category"],
    limit=3,
)
for r in res:
    print(r)
