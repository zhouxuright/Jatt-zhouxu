"""补录民法典 3 条缺失条文到 Milvus legal_articles 集合（与 milvus_full_import 同格式）。

用法（backend 容器内）: python /tmp/insert_articles.py
"""
import sys
import warnings

warnings.filterwarnings("ignore")
sys.path.insert(0, "/app")

from pymilvus import connections, Collection

ARTICLES = [
    {
        "id": "art_99999901",
        "law_name": "中华人民共和国民法典",
        "article_number": "第三百四十条",
        "content": "土地经营权人有权在合同约定的期限内占有农村土地，自主开展农业生产经营并取得收益。",
        "tags": "民法,民法典",
        "category": "民法",
    },
    {
        "id": "art_99999902",
        "law_name": "中华人民共和国民法典",
        "article_number": "第三百四十二条",
        "content": "通过招标、拍卖、公开协商等方式承包农村土地，经依法登记取得权属证书的，可以依法采取出租、入股、抵押或者其他方式流转土地经营权。",
        "tags": "民法,民法典",
        "category": "民法",
    },
    {
        "id": "art_99999903",
        "law_name": "中华人民共和国民法典",
        "article_number": "第九百六十六条",
        "content": "本章没有规定的，参照适用委托合同的有关规定。",
        "tags": "民法,民法典",
        "category": "民法",
    },
]


def main():
    connections.connect(host="milvus", port="19530")
    c = Collection("legal_articles")
    c.load()

    existing = c.query(
        expr='id in ["art_99999901", "art_99999902", "art_99999903"]',
        output_fields=["id"],
    )
    if existing:
        print("already inserted:", [r["id"] for r in existing])
        return

    from app.services.model_registry import ModelRegistry
    model = ModelRegistry.get_embedding_model()
    texts = [a["content"] for a in ARTICLES]
    output = model.encode(texts, return_dense=True)
    embeddings = [e.tolist() for e in output["dense_vecs"]]

    data = [
        [a["id"] for a in ARTICLES],
        [a["law_name"] for a in ARTICLES],
        [a["article_number"] for a in ARTICLES],
        [a["content"] for a in ARTICLES],
        [a["tags"] for a in ARTICLES],
        [a["category"] for a in ARTICLES],
        embeddings,
    ]
    c.insert(data)
    c.flush()
    c.load()

    verify = c.query(
        expr='law_name == "中华人民共和国民法典" and article_number in ["第三百四十条", "第三百四十二条", "第九百六十六条"]',
        output_fields=["id", "article_number"],
        limit=10,
    )
    print("inserted, verification:")
    for r in verify:
        print(" ", r)


if __name__ == "__main__":
    main()
