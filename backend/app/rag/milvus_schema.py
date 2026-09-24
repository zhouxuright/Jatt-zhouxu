"""Milvus 集合 schema 字段长度的**单一事实来源**。

为什么需要这个模块
------------------
Milvus 的 VARCHAR ``max_length`` 按 **UTF-8 字节数**计，不是字符数——中文 1 个
字符占 3 字节。历史上按"字符"的直觉把 ``legal_articles`` 设成
``law_name=256`` / ``content=8192``，而 PostgreSQL 侧实测最大值为
``law_name=396B`` / ``content=42,959B``，于是 **483 条**法条永远写不进向量库。

更隐蔽的是：Milvus 的长度校验是**批级**的——一条超限会让整批（256 条）全部
写入失败，且异常只在整批提交时抛出。表现是"回填跑了很久，缺口却怎么都补不齐"，
极易被误判为网络/性能问题。

因此把字段长度集中到这里，所有建集合的地方一律引用，避免多处漂移。
取值依据为 PostgreSQL 侧实测最大值 + 余量（见各常量注释）。
"""

from __future__ import annotations

#: 法条向量集合名（此前在多处硬编码，收敛到这里）
LEGAL_ARTICLES_COLLECTION = "legal_articles"

# --- legal_articles 集合的 varchar 上限（单位：UTF-8 字节） -----------------
LEGAL_ARTICLE_ID_MAX = 64  # 主键 "art_<uuid>"，约 40B
LEGAL_ARTICLE_LAW_NAME_MAX = 512  # 实测最大 396B（如"民法典\n侵权责任编"等复合法名）
LEGAL_ARTICLE_ARTICLE_NUMBER_MAX = 64  # 实测最大 29B
LEGAL_ARTICLE_CONTENT_MAX = 65535  # 实测最大 42,959B；65535 为 Milvus varchar 硬上限
LEGAL_ARTICLE_TAGS_MAX = 512  # 实测最大 406B
LEGAL_ARTICLE_CATEGORY_MAX = 64  # 实测最大 27B

#: 字节上限（供预检脚本复用，避免"文档说一套、代码写一套"）
LEGAL_ARTICLE_BYTE_LIMITS: dict[str, int] = {
    "id": LEGAL_ARTICLE_ID_MAX,
    "law_name": LEGAL_ARTICLE_LAW_NAME_MAX,
    "article_number": LEGAL_ARTICLE_ARTICLE_NUMBER_MAX,
    "content": LEGAL_ARTICLE_CONTENT_MAX,
    "tags": LEGAL_ARTICLE_TAGS_MAX,
    "category": LEGAL_ARTICLE_CATEGORY_MAX,
}

#: 参与**业务键匹配**的字段。这些字段不能截断——截断后与 PostgreSQL 侧
#: 的键不再相等，差集校验会永远认为"缺失"，等于自欺欺人。
LEGAL_ARTICLE_KEY_FIELDS: frozenset[str] = frozenset(
    {"id", "law_name", "article_number"}
)


def normalize_text(value: str | None) -> str:
    """折叠内部空白。

    库中存在形如 ``"中华人民共和国民法典\\n侵权责任编"`` 的法名（含换行），若不折叠
    空白，同一条法条会被判成两条，进而产生重复向量与"永远补不齐"的假缺口。
    """
    return " ".join(str(value or "").split())


def business_key(law_name: str | None, article_number: str | None) -> str:
    """跨库匹配用的业务键。

    Milvus 的 ``id`` 与 PostgreSQL 的 UUID **不是同一 ID 空间**（历史导入用
    ``art_xxx`` 形式的自建 ID，甚至是 ``art_{batch_start+i}`` 这种**位置** ID，
    重跑导入会为新位置生成新主键，从而产生重复实体）。因此跨库比对只能按
    ``(法名, 条号)`` 这一语义身份，不能用 id。
    """
    return f"{normalize_text(law_name)}\u0001{normalize_text(article_number)}"


def truncate_utf8(text: str | bytes | None, max_bytes: int) -> str:
    """按 UTF-8 **字节**数安全截断字符串。

    Milvus 的 VARCHAR ``max_length`` 以字节计数而非字符数，中文每字 3 字节，
    直接 ``text[:max_bytes]`` 会在含中文时轻松超限并导致**整批**写入失败。
    这里按字节切分后再解码，并用 ``errors="ignore"`` 丢弃被切断的半个字符，
    因此结果字节数一定 ≤ ``max_bytes``。
    """
    if not text:
        return ""
    data = text if isinstance(text, bytes) else str(text).encode("utf-8")
    if len(data) <= max_bytes:
        return data.decode("utf-8") if isinstance(data, bytes) else str(text)
    return data[:max_bytes].decode("utf-8", errors="ignore")
