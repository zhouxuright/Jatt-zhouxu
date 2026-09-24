"""Milvus-backed RAG service for legal knowledge retrieval.

Provides:
- Collection management (create/load legal knowledge collections)
- Document ingestion (embed + insert legal articles)
- Vector similarity search with optional category filtering
"""

import logging
from typing import Any

from app.core.config import settings
from app.rag.milvus_schema import (
    LEGAL_ARTICLE_ARTICLE_NUMBER_MAX,
    LEGAL_ARTICLE_CATEGORY_MAX,
    LEGAL_ARTICLE_CONTENT_MAX,
    LEGAL_ARTICLE_ID_MAX,
    LEGAL_ARTICLE_LAW_NAME_MAX,
    LEGAL_ARTICLE_TAGS_MAX,
    LEGAL_ARTICLES_COLLECTION,
    business_key,
)

logger = logging.getLogger(__name__)

# 集合名与字段长度统一来自 app.rag.milvus_schema（此处保留名称便于旧代码导入）
LEGAL_CASES_COLLECTION = "legal_cases"
EMBEDDING_DIM = 1024  # BAAI/BGE-M3 dense embedding dimension

# =========================================================================
# Seed data: real Chinese legal articles
# =========================================================================

SEED_LEGAL_ARTICLES: list[dict[str, Any]] = [
    {"law_name": "中华人民共和国劳动合同法", "article_number": "第十条", "content": "建立劳动关系，应当订立书面劳动合同。已建立劳动关系，未同时订立书面劳动合同的，应当自用工之日起一个月内订立书面劳动合同。", "tags": "劳动法,劳动合同,书面合同", "category": "劳动法"},
    {"law_name": "中华人民共和国劳动合同法", "article_number": "第十四条", "content": "无固定期限劳动合同，是指用人单位与劳动者约定无确定终止时间的劳动合同。用人单位与劳动者协商一致，可以订立无固定期限劳动合同。有下列情形之一，劳动者提出或者同意续订、订立劳动合同的，除劳动者提出订立固定期限劳动合同外，应当订立无固定期限劳动合同：（一）劳动者在该用人单位连续工作满十年的。", "tags": "劳动法,无固定期限,劳动合同", "category": "劳动法"},
    {"law_name": "中华人民共和国劳动合同法", "article_number": "第三十八条", "content": "用人单位有下列情形之一的，劳动者可以解除劳动合同：（一）未按照劳动合同约定提供劳动保护或者劳动条件的；（二）未及时足额支付劳动报酬的；（三）未依法为劳动者缴纳社会保险费的；（四）用人单位的规章制度违反法律、法规的规定，损害劳动者权益的。", "tags": "劳动法,解除,劳动者权利", "category": "劳动法"},
    {"law_name": "中华人民共和国劳动合同法", "article_number": "第三十九条", "content": "劳动者有下列情形之一的，用人单位可以解除劳动合同：（一）在试用期间被证明不符合录用条件的；（二）严重违反用人单位的规章制度的；（三）严重失职，营私舞弊，给用人单位造成重大损害的。", "tags": "劳动法,解除,用人单位权利", "category": "劳动法"},
    {"law_name": "中华人民共和国劳动合同法", "article_number": "第四十六条", "content": "有下列情形之一的，用人单位应当向劳动者支付经济补偿：（一）劳动者依照本法第三十八条规定解除劳动合同的；（二）用人单位依照本法第三十六条规定向劳动者提出解除劳动合同并与劳动者协商一致解除劳动合同的；（三）用人单位依照本法第四十条规定解除劳动合同的；（四）用人单位依照本法第四十一条第一款规定解除劳动合同的；（五）除用人单位维持或者提高劳动合同约定条件续订劳动合同，劳动者不同意续订的情形外，依照本法第四十四条第一项规定终止固定期限劳动合同的。", "tags": "劳动法,经济补偿,劳动合同", "category": "劳动法"},
    {"law_name": "中华人民共和国劳动合同法", "article_number": "第四十七条", "content": "经济补偿按劳动者在本单位工作的年限，每满一年支付一个月工资的标准向劳动者支付。六个月以上不满一年的，按一年计算；不满六个月的，向劳动者支付半个月工资的经济补偿。", "tags": "劳动法,经济补偿,计算标准", "category": "劳动法"},
    {"law_name": "中华人民共和国劳动合同法", "article_number": "第八十二条", "content": "用人单位自用工之日起超过一个月不满一年未与劳动者订立书面劳动合同的，应当向劳动者每月支付二倍的工资。", "tags": "劳动法,双倍工资,未签合同", "category": "劳动法"},
    {"law_name": "中华人民共和国劳动合同法", "article_number": "第八十七条", "content": "用人单位违反本法规定解除或者终止劳动合同的，应当依照本法第四十七条规定的经济补偿标准的二倍向劳动者支付赔偿金。", "tags": "劳动法,赔偿金,违法解除", "category": "劳动法"},
    {"law_name": "中华人民共和国民法典", "article_number": "第二条", "content": "民法调整平等主体的自然人、法人和非法人组织之间的人身关系和财产关系。", "tags": "民法典,总则,调整对象", "category": "民法"},
    {"law_name": "中华人民共和国民法典", "article_number": "第三条", "content": "民事主体的人身权利、财产权利以及其他合法权益受法律保护，任何组织或者个人不得侵犯。", "tags": "民法典,总则,权益保护", "category": "民法"},
    {"law_name": "中华人民共和国民法典", "article_number": "第四百六十四条", "content": "合同是民事主体之间设立、变更、终止民事法律关系的协议。婚姻、收养、监护等有关身份关系的协议，适用有关该身份关系的法律规定；没有规定的，可以根据其性质参照适用本编规定。", "tags": "民法典,合同,定义", "category": "民法"},
    {"law_name": "中华人民共和国民法典", "article_number": "第五百零九条", "content": "当事人应当按照约定全面履行自己的义务。当事人应当遵循诚信原则，根据合同的性质、目的和交易习惯履行通知、协助、保密等义务。", "tags": "民法典,合同,履行原则,诚信", "category": "民法"},
    {"law_name": "中华人民共和国民法典", "article_number": "第五百七十七条", "content": "当事人一方不履行合同义务或者履行合同义务不符合约定的，应当承担继续履行、采取补救措施或者赔偿损失等违约责任。", "tags": "民法典,合同,违约责任", "category": "民法"},
    {"law_name": "中华人民共和国民法典", "article_number": "第六百六十七条", "content": "借款合同是借款人向贷款人借款，到期返还借款并支付利息的合同。", "tags": "民法典,合同,借款合同", "category": "民法"},
    {"law_name": "中华人民共和国民法典", "article_number": "第六百七十五条", "content": "借款人应当按照约定的期限返还借款。对借款期限没有约定或者约定不明确，依据本法第五百一十条的规定仍不能确定的，借款人可以随时返还；贷款人可以催告借款人在合理期限内返还。", "tags": "民法典,合同,借款,还款", "category": "民法"},
    {"law_name": "中华人民共和国民法典", "article_number": "第一千零四十六条", "content": "结婚应当男女双方完全自愿，禁止任何一方对另一方加以强迫，禁止任何组织或者个人加以干涉。", "tags": "民法典,婚姻家庭,结婚", "category": "民法"},
    {"law_name": "中华人民共和国民法典", "article_number": "第一千零七十九条", "content": "夫妻一方要求离婚的，可以由有关组织进行调解或者直接向人民法院提起离婚诉讼。人民法院审理离婚案件，应当进行调解；如果感情确已破裂，调解无效的，应当准予离婚。", "tags": "民法典,婚姻家庭,离婚", "category": "民法"},
    {"law_name": "中华人民共和国民法典", "article_number": "第一千一百六十五条", "content": "行为人因过错侵害他人民事权益造成损害的，应当承担侵权责任。依照法律规定推定行为人有过错，其不能证明自己没有过错的，应当承担侵权责任。", "tags": "民法典,侵权责任,过错责任", "category": "民法"},
    {"law_name": "中华人民共和国民法典", "article_number": "第一千一百六十七条", "content": "侵权行为危及他人人身、财产安全的，被侵权人有权请求侵权人承担停止侵害、排除妨碍、消除危险等侵权责任。", "tags": "民法典,侵权责任,责任承担", "category": "民法"},
    {"law_name": "中华人民共和国民法典", "article_number": "第一千二百五十四条", "content": "禁止从建筑物中抛掷物品。从建筑物中抛掷物品或者从建筑物上坠落的物品造成他人损害的，由侵权人依法承担侵权责任。", "tags": "民法典,侵权责任,高空抛物", "category": "民法"},
    {"law_name": "中华人民共和国刑法", "article_number": "第十三条", "content": "一切危害国家主权、领土完整和安全，分裂国家、颠覆人民民主专政的政权和推翻社会主义制度，破坏社会秩序和经济秩序，侵犯国有财产或者劳动群众集体所有的财产，侵犯公民私人所有的财产，侵犯公民的人身权利、民主权利和其他权利，以及其他危害社会的行为，依照法律应当受刑罚处罚的，都是犯罪，但是情节显著轻微危害不大的，不认为是犯罪。", "tags": "刑法,犯罪,总则", "category": "刑法"},
    {"law_name": "中华人民共和国刑法", "article_number": "第二百三十二条", "content": "故意杀人的，处死刑、无期徒刑或者十年以上有期徒刑；情节较轻的，处三年以上十年以下有期徒刑。", "tags": "刑法,故意杀人,人身权利", "category": "刑法"},
    {"law_name": "中华人民共和国刑法", "article_number": "第二百三十四条", "content": "故意伤害他人身体的，处三年以下有期徒刑、拘役或者管制。犯前款罪，致人重伤的，处三年以上十年以下有期徒刑。", "tags": "刑法,故意伤害,人身权利", "category": "刑法"},
    {"law_name": "中华人民共和国刑法", "article_number": "第二百六十四条", "content": "盗窃公私财物，数额较大的，或者多次盗窃、入户盗窃、携带凶器盗窃、扒窃的，处三年以下有期徒刑、拘役或者管制，并处或者单处罚金。", "tags": "刑法,盗窃,财产犯罪", "category": "刑法"},
    {"law_name": "中华人民共和国刑法", "article_number": "第二百六十六条", "content": "诈骗公私财物，数额较大的，处三年以下有期徒刑、拘役或者管制，并处或者单处罚金；数额巨大或者有其他严重情节的，处三年以上十年以下有期徒刑，并处罚金。", "tags": "刑法,诈骗,财产犯罪", "category": "刑法"},
    {"law_name": "中华人民共和国刑法", "article_number": "第三百八十二条", "content": "国家工作人员利用职务上的便利，侵吞、窃取、骗取或者以其他手段非法占有公共财物的，是贪污罪。", "tags": "刑法,贪污,职务犯罪", "category": "刑法"},
    {"law_name": "中华人民共和国刑法", "article_number": "第三百八十五条", "content": "国家工作人员利用职务上的便利，索取他人财物的，或者非法收受他人财物，为他人谋取利益的，是受贿罪。", "tags": "刑法,受贿,职务犯罪", "category": "刑法"},
    {"law_name": "中华人民共和国宪法", "article_number": "第五条", "content": "中华人民共和国实行依法治国，建设社会主义法治国家。国家维护社会主义法制的统一和尊严。一切法律、行政法规和地方性法规都不得同宪法相抵触。", "tags": "宪法,法治,依法治国", "category": "宪法"},
    {"law_name": "中华人民共和国宪法", "article_number": "第三十三条", "content": "凡具有中华人民共和国国籍的人都是中华人民共和国公民。中华人民共和国公民在法律面前一律平等。国家尊重和保障人权。", "tags": "宪法,平等权,人权", "category": "宪法"},
    {"law_name": "中华人民共和国宪法", "article_number": "第三十七条", "content": "中华人民共和国公民的人身自由不受侵犯。任何公民，非经人民检察院批准或者决定或者人民法院决定，并由公安机关执行，不受逮捕。", "tags": "宪法,人身自由,人身权利", "category": "宪法"},
    {"law_name": "中华人民共和国宪法", "article_number": "第三十八条", "content": "中华人民共和国公民的人格尊严不受侵犯。禁止用任何方法对公民进行侮辱、诽谤和诬告陷害。", "tags": "宪法,人格尊严,名誉权", "category": "宪法"},
]


def _get_embeddings_sync(texts: list[str]) -> list[list[float]]:
    """Generate embeddings using BAAI/BGE-M3 via the singleton ModelRegistry."""
    from app.services.model_registry import ModelRegistry
    model = ModelRegistry.get_embedding_model()
    output = model.encode(texts, return_dense=True)
    return [e.tolist() for e in output["dense_vecs"]]


def _sanitize_expr_value(value: str, max_len: int = 64) -> str:
    """Sanitize a value used inside a Milvus boolean expression string literal.

    Strips quote/backslash/control characters so user input can never break
    out of the expression's quoted literal (expression-injection defence).
    """
    banned = {'"', "'", "\\", ";", "`", "{", "}", "$", "\n", "\r", "\t"}
    if not value:
        return ""
    cleaned = "".join(
        ch for ch in str(value)
        if ch not in banned and ch.isprintable()
    )
    return cleaned[:max_len]


def _truncate_utf8(value: str, max_bytes: int) -> str:
    """按 UTF-8 字节数截断字符串（Milvus VARCHAR max_length 以字节计）。"""
    if not value:
        return ""
    data = value.encode("utf-8")
    if len(data) <= max_bytes:
        return value
    return data[:max_bytes].decode("utf-8", errors="ignore")


def _content_sig(text: str) -> str:
    """正文判重指纹：删除**全部**空白后截前 2000 字符。

    两个历史导入器写入时都做过空白折叠，因此"同一法条"的两份副本在删空白后
    应当完全一致。截断到 2000 字符只为限制键长，足以区分不同法条。
    """
    return "".join((text or "").split())[:2000]


#: 检索兜底去重的超采样倍数。
#: 向量库若残留冗余实体（历史位置型主键 ``art_{n}`` 与 gap-sync 的 ``pg_{uuid}``
#: 各写一份造成，见 ``scripts/dedup_milvus_articles.py``），同一条法条会白占
#: top_k 名额。检索时先多取若干条、去重后再截到 top_k。
_RETRIEVAL_OVERFETCH = 4


def _dedup_hits(hits: list[dict[str, Any]], key_fn: Any, top_k: int) -> list[dict[str, Any]]:
    """按 ``key_fn`` 去重并截断到 ``top_k``。

    Milvus 返回结果已按相似度降序排列，因此"首次出现"即相似度最高的那条，
    保留首条即语义上的最优保留策略（无需再比较 score）。
    """
    seen: set[Any] = set()
    out: list[dict[str, Any]] = []
    for hit in hits:
        key = key_fn(hit)
        if key in seen:
            continue
        seen.add(key)
        out.append(hit)
        if len(out) >= top_k:
            break
    return out


class MilvusRAGService:
    """Milvus-backed RAG service for legal knowledge retrieval."""

    def __init__(self) -> None:
        self._connected = False
        self._collection_ready = False

    def _ensure_connected(self) -> bool:
        if self._connected:
            return True
        try:
            from pymilvus import connections
            connections.connect(alias="default", host=settings.MILVUS_HOST, port=settings.MILVUS_PORT)
            self._connected = True
            return True
        except Exception as exc:
            logger.warning("Milvus connection failed: %s", exc)
            return False

    def create_collection(self) -> bool:
        """Create the legal_articles collection in Milvus."""
        if not self._ensure_connected():
            return False
        try:
            from pymilvus import CollectionSchema, DataType, FieldSchema, utility, Collection

            if utility.has_collection(LEGAL_ARTICLES_COLLECTION):
                self._collection_ready = True
                return True

            # 字段长度统一取自 milvus_schema（按 UTF-8 字节，中文 1 字 = 3 字节）。
            # 切勿在此硬编码：历史硬编码为 law_name=256 / content=8192，
            # 导致 483 条超长法条永远无法写入向量库。
            fields = [
                FieldSchema(
                    name="id", dtype=DataType.VARCHAR, is_primary=True,
                    max_length=LEGAL_ARTICLE_ID_MAX,
                ),
                FieldSchema(
                    name="law_name", dtype=DataType.VARCHAR,
                    max_length=LEGAL_ARTICLE_LAW_NAME_MAX,
                ),
                FieldSchema(
                    name="article_number", dtype=DataType.VARCHAR,
                    max_length=LEGAL_ARTICLE_ARTICLE_NUMBER_MAX,
                ),
                FieldSchema(
                    name="content", dtype=DataType.VARCHAR,
                    max_length=LEGAL_ARTICLE_CONTENT_MAX,
                ),
                FieldSchema(
                    name="tags", dtype=DataType.VARCHAR,
                    max_length=LEGAL_ARTICLE_TAGS_MAX,
                ),
                FieldSchema(
                    name="category", dtype=DataType.VARCHAR,
                    max_length=LEGAL_ARTICLE_CATEGORY_MAX,
                ),
                FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=EMBEDDING_DIM),
            ]
            schema = CollectionSchema(fields=fields, description="Chinese legal articles for RAG")
            collection = Collection(name=LEGAL_ARTICLES_COLLECTION, schema=schema)

            index_params = {"index_type": "IVF_FLAT", "metric_type": "COSINE", "params": {"nlist": 2048}}
            collection.create_index(field_name="embedding", index_params=index_params)
            collection.load()

            self._collection_ready = True
            logger.info("Created Milvus collection '%s'", LEGAL_ARTICLES_COLLECTION)
            return True
        except Exception as exc:
            logger.error("Failed to create Milvus collection: %s", exc)
            return False

    def ingest_seed_data(self) -> int:
        """Ingest seed legal articles into Milvus."""
        if not self._collection_ready:
            if not self.create_collection():
                return 0
        try:
            from pymilvus import Collection
            collection = Collection(LEGAL_ARTICLES_COLLECTION)
            collection.load()

            if collection.num_entities > 0:
                logger.info("Collection already has %d entities", collection.num_entities)
                return collection.num_entities

            logger.info("Generating embeddings for %d articles...", len(SEED_LEGAL_ARTICLES))
            texts = [art["content"] for art in SEED_LEGAL_ARTICLES]
            embeddings = _get_embeddings_sync(texts)

            ids = [f"legal_{i:03d}" for i in range(len(SEED_LEGAL_ARTICLES))]
            law_names = [art["law_name"] for art in SEED_LEGAL_ARTICLES]
            article_numbers = [art["article_number"] for art in SEED_LEGAL_ARTICLES]
            contents = [art["content"] for art in SEED_LEGAL_ARTICLES]
            tags_list = [art["tags"] for art in SEED_LEGAL_ARTICLES]
            categories = [art["category"] for art in SEED_LEGAL_ARTICLES]

            data = [ids, law_names, article_numbers, contents, tags_list, categories, embeddings]
            collection.insert(data)
            collection.flush()
            collection.load()

            count = collection.num_entities
            logger.info("Ingested %d legal articles into Milvus", count)
            return count
        except Exception as exc:
            logger.error("Failed to ingest seed data: %s", exc)
            return 0

    def search(self, query: str, top_k: int = 5, category_filter: str = "") -> list[dict[str, Any]]:
        """Search for relevant legal articles using vector similarity."""
        if not self._collection_ready:
            if not self.create_collection():
                return []
        try:
            from pymilvus import Collection
            collection = Collection(LEGAL_ARTICLES_COLLECTION)
            collection.load()

            if collection.num_entities == 0:
                return []

            query_embedding = _get_embeddings_sync([query])[0]

            search_params = {"metric_type": "COSINE", "params": {"nprobe": 64}}
            expr = f'category == "{_sanitize_expr_value(category_filter)}"' if category_filter else None

            # 超采样：先多取，去重后截到 top_k，避免重复实体挤占结果位
            fetch_limit = max(top_k * _RETRIEVAL_OVERFETCH, top_k + 10)
            results = collection.search(
                data=[query_embedding],
                anns_field="embedding",
                param=search_params,
                limit=fetch_limit,
                output_fields=["law_name", "article_number", "content", "tags", "category"],
                expr=expr,
            )

            formatted = []
            for hit in results[0]:
                formatted.append({
                    "law_name": hit.entity.get("law_name", ""),
                    "article_number": hit.entity.get("article_number", ""),
                    "content": hit.entity.get("content", ""),
                    "tags": hit.entity.get("tags", ""),
                    "category": hit.entity.get("category", ""),
                    "score": float(hit.distance),
                    "source": "milvus_vector",
                })
            return _dedup_hits(
                formatted,
                lambda h: (
                    business_key(h["law_name"], h["article_number"]),
                    _content_sig(h["content"]),
                ),
                top_k,
            )
        except Exception as exc:
            logger.error("Milvus search failed: %s", exc)
            return []

    def get_stats(self) -> dict[str, Any]:
        if not self._collection_ready:
            # 冷启动时 _collection_ready 尚未置位，但集合可能早就存在。
            # 此处直接返回 not_initialized 会让状态接口在进程首个请求时误报
            # "未初始化"（VectorDB 明明可用）。故先尝试惰性初始化。
            if not self.create_collection():
                return {"status": "not_initialized", "count": 0}
        try:
            from pymilvus import Collection
            collection = Collection(LEGAL_ARTICLES_COLLECTION)
            collection.load()
            return {"status": "ready", "collection": LEGAL_ARTICLES_COLLECTION, "count": collection.num_entities, "dimension": EMBEDDING_DIM}
        except Exception as exc:
            return {"status": "error", "error": str(exc)}


    # =========================================================================
    # Case vectors (案例向量语义检索)
    # =========================================================================

    def ensure_case_collection(self) -> bool:
        """Create the legal_cases collection if it does not exist."""
        if not self._ensure_connected():
            return False
        try:
            from pymilvus import CollectionSchema, DataType, FieldSchema, utility, Collection

            if utility.has_collection(LEGAL_CASES_COLLECTION):
                return True

            fields = [
                FieldSchema(name="id", dtype=DataType.VARCHAR, is_primary=True, max_length=64),
                FieldSchema(name="case_number", dtype=DataType.VARCHAR, max_length=128),
                FieldSchema(name="title", dtype=DataType.VARCHAR, max_length=512),
                FieldSchema(name="court_name", dtype=DataType.VARCHAR, max_length=256),
                FieldSchema(name="case_type", dtype=DataType.VARCHAR, max_length=64),
                FieldSchema(name="cause_of_action", dtype=DataType.VARCHAR, max_length=256),
                FieldSchema(name="decision_date", dtype=DataType.VARCHAR, max_length=32),
                FieldSchema(name="summary", dtype=DataType.VARCHAR, max_length=8192),
                FieldSchema(name="tags", dtype=DataType.VARCHAR, max_length=512),
                FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=EMBEDDING_DIM),
            ]
            schema = CollectionSchema(fields=fields, description="Court cases for semantic similarity search")
            collection = Collection(name=LEGAL_CASES_COLLECTION, schema=schema)

            index_params = {"index_type": "IVF_FLAT", "metric_type": "COSINE", "params": {"nlist": 512}}
            collection.create_index(field_name="embedding", index_params=index_params)
            collection.load()
            logger.info("Created Milvus case collection '%s'", LEGAL_CASES_COLLECTION)
            return True
        except Exception as exc:
            logger.error("Failed to create Milvus case collection: %s", exc)
            return False

    def get_case_vector_ids(self, limit: int = 200000) -> set[str]:
        """Return the set of case ids already embedded (for resumable ingestion)."""
        if not self._ensure_connected():
            return set()
        try:
            from pymilvus import Collection
            if not self.ensure_case_collection():
                return set()
            collection = Collection(LEGAL_CASES_COLLECTION)
            collection.load()
            rows = collection.query(
                expr="", output_fields=["id"], limit=limit
            )
            return {r["id"] for r in rows}
        except Exception as exc:
            logger.warning("Failed to list case vector ids: %s", exc)
            return set()

    def ingest_case_vectors(self, records: list[dict[str, Any]], batch_size: int = 64,
                            progress_cb: Any = None) -> int:
        """Embed + insert case records into the legal_cases collection.

        Each record: {id, case_number, title, court_name, case_type,
                      cause_of_action, decision_date, summary, tags}.
        Embedding text = title + summary[:800]. Returns number inserted.
        """
        if not records:
            return 0
        if not self._ensure_connected() or not self.ensure_case_collection():
            return 0
        try:
            from pymilvus import Collection
            collection = Collection(LEGAL_CASES_COLLECTION)
            collection.load()

            inserted = 0
            for start in range(0, len(records), batch_size):
                batch = records[start:start + batch_size]
                texts = [
                    f"{r.get('title', '')} {r.get('cause_of_action', '')} {(r.get('summary') or '')[:800]}"
                    for r in batch
                ]
                embeddings = _get_embeddings_sync(texts)

                data = [
                    [str(r["id"])[:64] for r in batch],
                    [_truncate_utf8(r.get("case_number") or "", 128) for r in batch],
                    [_truncate_utf8(r.get("title") or "", 512) for r in batch],
                    [_truncate_utf8(r.get("court_name") or "", 256) for r in batch],
                    [_truncate_utf8(r.get("case_type") or "", 64) for r in batch],
                    [_truncate_utf8(r.get("cause_of_action") or "", 256) for r in batch],
                    [_truncate_utf8(r.get("decision_date") or "", 32) for r in batch],
                    [_truncate_utf8(r.get("summary") or "", 8192) for r in batch],
                    [_truncate_utf8(r.get("tags") or "", 512) for r in batch],
                    embeddings,
                ]
                collection.insert(data)
                collection.flush()
                inserted += len(batch)
                if progress_cb:
                    progress_cb(inserted, len(records))
            collection.load()
            logger.info("Ingested %d case vectors into Milvus", inserted)
            return inserted
        except Exception as exc:
            logger.error("Failed to ingest case vectors: %s", exc)
            return inserted

    def search_cases(self, query: str, top_k: int = 10,
                     case_type: str = "", court_name: str = "") -> list[dict[str, Any]]:
        """Semantic similarity search over embedded court cases."""
        if not self._ensure_connected():
            return []
        try:
            from pymilvus import Collection
            if not self.ensure_case_collection():
                return []
            collection = Collection(LEGAL_CASES_COLLECTION)
            collection.load()

            if collection.num_entities == 0:
                return []

            query_embedding = _get_embeddings_sync([query])[0]
            search_params = {"metric_type": "COSINE", "params": {"nprobe": 32}}

            conditions = []
            if case_type:
                conditions.append(f'case_type == "{_sanitize_expr_value(case_type)}"')
            if court_name:
                conditions.append(f'court_name == "{_sanitize_expr_value(court_name)}"')
            expr = " and ".join(conditions) if conditions else None

            results = collection.search(
                data=[query_embedding],
                anns_field="embedding",
                param=search_params,
                limit=top_k,
                output_fields=["id", "case_number", "title", "court_name",
                               "case_type", "cause_of_action", "decision_date", "summary"],
                expr=expr,
            )

            formatted = []
            for hit in results[0]:
                ent = hit.entity
                formatted.append({
                    "id": ent.get("id", ""),
                    "case_number": ent.get("case_number", ""),
                    "title": ent.get("title", ""),
                    "court_name": ent.get("court_name", ""),
                    "case_type": ent.get("case_type", ""),
                    "cause_of_action": ent.get("cause_of_action", ""),
                    "decision_date": ent.get("decision_date", ""),
                    "summary": ent.get("summary", ""),
                    "score": float(hit.distance),
                    "source": "milvus_semantic",
                })
            return formatted
        except Exception as exc:
            logger.error("Milvus case search failed: %s", exc)
            return []

    def get_case_stats(self) -> dict[str, Any]:
        """Stats for the legal_cases collection."""
        if not self._ensure_connected():
            return {"status": "unavailable", "count": 0}
        try:
            from pymilvus import Collection, utility
            if not utility.has_collection(LEGAL_CASES_COLLECTION):
                return {"status": "not_created", "count": 0}
            collection = Collection(LEGAL_CASES_COLLECTION)
            collection.load()
            return {"status": "ready", "collection": LEGAL_CASES_COLLECTION,
                    "count": collection.num_entities, "dimension": EMBEDDING_DIM}
        except Exception as exc:
            return {"status": "error", "error": str(exc)}


_milvus_rag_service: MilvusRAGService | None = None


def get_milvus_rag_service() -> MilvusRAGService:
    global _milvus_rag_service
    if _milvus_rag_service is None:
        _milvus_rag_service = MilvusRAGService()
    return _milvus_rag_service
