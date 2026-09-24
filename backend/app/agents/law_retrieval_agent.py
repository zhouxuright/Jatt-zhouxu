"""
Law Article Retrieval Agent - Multi-strategy legal search using LangGraph.

Flow: parse_query -> (semantic_search, keyword_search, exact_search) -> rerank_results
"""
from typing import Any, Optional
import asyncio
import json
import logging
import re

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langgraph.graph import StateGraph, END
from pydantic import BaseModel, Field

from app.agents.base_agent import BaseAgent, AgentState
from app.prompts.legal_prompts import (
    LAW_RETRIEVAL_SYSTEM_PROMPT,
    LAW_RETRIEVAL_TEMPLATE,
    LEGAL_DISCLAIMER,
)
from app.services.llm_service import ChatLLMService, get_llm_service

logger = logging.getLogger(__name__)


# =============================================================================
# Law Category Mapping
# =============================================================================

LAW_CATEGORIES: dict[str, str] = {
    "宪法": "宪法及宪法相关法",
    "刑法": "刑法",
    "民法": "民商法",
    "行政法": "行政法",
    "经济法": "经济法",
    "社会法": "社会法",
    "诉讼法": "诉讼与非诉讼程序法",
    "商法": "民商法",
    "知识产权": "民商法",
    "劳动法": "社会法",
    "环境法": "经济法",
    "税法": "经济法",
    "公司法": "民商法",
    "合同": "民商法",
    "侵权": "民商法",
    "婚姻": "民商法",
    "继承": "民商法",
    "仲裁": "诉讼与非诉讼程序法",
}

# Default category filter for each top-level category
CATEGORY_LAW_MAP: dict[str, list[str]] = {
    "宪法及宪法相关法": ["中华人民共和国宪法", "立法法", "选举法", "组织法", "监督法"],
    "刑法": ["中华人民共和国刑法", "刑法修正案"],
    "民商法": [
        "中华人民共和国民法典",
        "中华人民共和国公司法",
        "中华人民共和国合伙企业法",
        "中华人民共和国商标法",
        "中华人民共和国专利法",
        "中华人民共和国著作权法",
        "中华人民共和国证券法",
        "中华人民共和国保险法",
        "中华人民共和国票据法",
        "中华人民共和国企业破产法",
    ],
    "行政法": [
        "中华人民共和国行政处罚法",
        "中华人民共和国行政许可法",
        "中华人民共和国行政强制法",
        "中华人民共和国行政复议法",
        "中华人民共和国行政诉讼法",
        "中华人民共和国治安管理处罚法",
        "中华人民共和国国家赔偿法",
    ],
    "经济法": [
        "中华人民共和国反垄断法",
        "中华人民共和国反不正当竞争法",
        "中华人民共和国消费者权益保护法",
        "中华人民共和国产品质量法",
        "中华人民共和国税收征收管理法",
        "中华人民共和国个人所得税法",
        "中华人民共和国企业所得税法",
        "中华人民共和国环境保护法",
        "中华人民共和国土地管理法",
        "中华人民共和国城乡规划法",
    ],
    "社会法": [
        "中华人民共和国劳动法",
        "中华人民共和国劳动合同法",
        "中华人民共和国社会保险法",
        "中华人民共和国工会法",
        "中华人民共和国未成年人保护法",
        "中华人民共和国妇女权益保障法",
        "中华人民共和国残疾人保障法",
        "中华人民共和国安全生产法",
    ],
    "诉讼与非诉讼程序法": [
        "中华人民共和国民事诉讼法",
        "中华人民共和国刑事诉讼法",
        "中华人民共和国行政诉讼法",
        "中华人民共和国仲裁法",
        "中华人民共和国人民调解法",
        "中华人民共和国海事诉讼特别程序法",
    ],
}


# =============================================================================
# Rule-based Domain Detection (fast, deterministic, no LLM latency)
# =============================================================================
# Each rule maps colloquial query keywords to a legal domain. When a domain is
# detected, results whose law_name matches `reject` hints are demoted and those
# matching `boost` hints are slightly promoted. This is what prevents, e.g., a
# "租房押金不退" query from surfacing labour/criminal articles via noisy BM25
# keyword matches.
# =============================================================================

DOMAIN_RULES: list[dict[str, Any]] = [
    {
        "name": "房屋租赁",
        "keywords": [
            "租房", "押金", "租金", "房租", "租赁", "出租", "承租", "房东",
            "房客", "转租", "退租", "房屋租赁", "押一付三", "中介费", "租客",
            "物业费", "房产", "买房", "卖房", "过户", "产权",
        ],
        "boost": ["租赁", "房屋", "住房", "租房", "房地产", "物业", "商品房", "不动产", "产权"],
        "reject": ["劳动", "劳动合同", "工资", "用工", "刑事", "刑法", "婚姻", "继承", "治安", "交通"],
    },
    {
        "name": "劳动",
        "keywords": [
            "工资", "劳动", "辞退", "解雇", "加班", "社保", "工伤", "劳动合同",
            "拖欠工资", "经济补偿", "赔偿金", "劳动报酬", "工时", "产假",
            "年终奖", "试用期", "双倍工资", "老板", "员工", "裁员",
        ],
        "boost": ["劳动", "工资", "劳动合同", "用工", "工会", "社会保险", "工伤", "就业", "薪酬"],
        "reject": ["租赁", "房屋", "住房", "刑事", "刑法", "婚姻", "继承", "治安"],
    },
    {
        "name": "婚姻家庭",
        "keywords": [
            "离婚", "结婚", "抚养权", "抚养费", "赡养", "继承", "夫妻", "彩礼",
            "探视权", "监护", "收养", "家暴", "婚前", "婚后", "遗嘱", "遗产",
        ],
        "boost": ["婚姻", "继承", "家庭", "收养", "监护", "抚养", "赡养", "结婚", "离婚", "夫妻"],
        "reject": ["劳动", "劳动合同", "工资", "刑事", "刑法", "租赁", "房屋", "治安"],
    },
    {
        "name": "借贷",
        "keywords": [
            "借款", "借条", "欠钱", "还钱", "借贷", "债务", "利息", "欠款",
            "高利贷", "催收", "贷款", "担保", "抵押", "民间借贷",
        ],
        "boost": ["借款", "借贷", "债务", "利息", "合同", "担保", "抵押", "民法典"],
        "reject": ["劳动", "劳动合同", "工资", "租赁", "房屋", "刑事", "刑法", "婚姻", "继承"],
    },
]


def detect_domain(query: str) -> dict[str, Any] | None:
    """Return the first matching legal domain rule, or None."""
    for rule in DOMAIN_RULES:
        if any(kw in query for kw in rule["keywords"]):
            return rule
    return None


_ARTICLE_NUM_RE = re.compile(r"[零〇一二三四五六七八九十百千万0-9]+")


def normalize_article_number(raw: str) -> str:
    """Normalize an article number into a clean ``第X条`` form.

    The upstream datastore already stores most values as ``第X条`` (e.g.
    ``第六百七十五条``); a minority carry a stray suffix (``第七十四条_1``)
    or an opaque id. Display layers must not re-wrap the result with another
    ``第...条``, so this collapses everything into one canonical token.
    """
    if not raw:
        return ""
    s = raw.strip()
    s = re.sub(r"_\d+$", "", s).strip()
    if not s:
        return raw
    if re.fullmatch(rf"第{_ARTICLE_NUM_RE.pattern}条", s):
        return s
    core = re.sub(r"^第", "", s)
    core = re.sub(r"条$", "", core)
    if re.fullmatch(_ARTICLE_NUM_RE.pattern, core):
        return f"第{core}条"
    return raw


# =============================================================================
# Article-reference parsing (for exact match search)
# =============================================================================

_CHINESE_DIGITS = "零一二三四五六七八九"
_ARABIC_NUM_RE = re.compile(r"^[0-9]+$")


def _arabic_to_chinese_num(n: int) -> str:
    """Convert 1..9999 into Chinese numerals (342 -> 三百四十二)."""
    if n < 1 or n >= 10000:
        return ""
    if n < 10:
        return _CHINESE_DIGITS[n]
    if n < 20:
        return "十" + (_CHINESE_DIGITS[n - 10] if n > 10 else "")
    parts: list[str] = []
    for value, label in ((1000, "千"), (100, "百"), (10, "十")):
        d, n = divmod(n, value)
        if d:
            parts.append(_CHINESE_DIGITS[d] + label)
        elif parts and n and parts[-1] != "零":
            parts.append("零")
    if n:
        parts.append(_CHINESE_DIGITS[n])
    return "".join(parts)


# Matches 第342条 / 第342条之一 / 三百四十条 / 第1079条之二 ...
_ARTICLE_REF_RE = re.compile(
    r"第?\s*([0-9]+|[零〇一二三四五六七八九十百千万]+)\s*条(之[一二三四五六七八九十]+)?"
)
# Chinese run (candidate law name) ending right before an article reference
_LAW_CHUNK_RE = re.compile(r"[\u4e00-\u9fa5]{2,12}$")


def _canonical_article_number(num_raw: str, suffix: str | None) -> str:
    """Build the canonical 第X条[之Y] form stored in the datastore."""
    num_raw = num_raw.replace("〇", "零")
    if _ARABIC_NUM_RE.fullmatch(num_raw):
        chinese = _arabic_to_chinese_num(int(num_raw))
        if not chinese:
            return ""
        core = chinese
    else:
        core = num_raw
    if core == "零":
        return ""
    return f"第{core}条{suffix or ''}"


def _extract_article_refs(query: str) -> list[dict[str, Any]]:
    """Extract (law_chunk, article_number) references from a query.

    Each reference carries the Chinese text immediately preceding the
    ``第X条`` token (with earlier article refs, list connectors and closing
    quotes stripped) so the exact-search node can resolve the intended law
    in PostgreSQL.
    """
    refs: list[dict[str, Any]] = []
    for m in _ARTICLE_REF_RE.finditer(query):
        article_number = _canonical_article_number(m.group(1), m.group(2))
        if not article_number:
            continue
        prefix = query[: m.start()]
        # "民法典第340条和第342条" -> both refs resolve to 民法典: strip the
        # connector first, then the earlier reference, then connectors and
        # closing quotes (《劳动合同法》第十条 -> 劳动合同法) once more.
        prefix = re.sub(r"[和与及、,，\s]+$", "", prefix)
        prefix = re.sub(r"第?[0-9零〇一二三四五六七八九十百千万\s]*条(之[一二三四五六七八九十]+)?$", "", prefix)
        prefix = re.sub(r"[和与及、,，\s》」』]+$", "", prefix)
        chunk_m = _LAW_CHUNK_RE.search(prefix)
        refs.append({
            "law_chunk": chunk_m.group(0) if chunk_m else "",
            "article_number": article_number,
        })
    return refs


# Weights applied in the results-merge step. Semantic (BGE-M3 cosine) is the
# high-quality signal; BM25 keyword matching is noisy for Chinese short queries
# and is therefore down-weighted so it can surface relevant articles without
# drowning the semantic ranking.
SEMANTIC_WEIGHT = 1.0
KEYWORD_WEIGHT = 0.5
DOMAIN_BOOST = 1.15
DOMAIN_REJECT = 0.4


# =============================================================================
# Result Model
# =============================================================================

class LawArticleResult(BaseModel):
    """A single law article retrieval result.

    Attributes:
        law_name: Full name of the law.
        article_number: Article number.
        article_content: Full text of the article.
        relevance_score: Relevance score (0.0-1.0).
        effective_status: Whether the law is currently effective.
        category: Law category.
        publish_year: Year of publication/revision.
        source: Which search strategy found this result.
    """

    law_name: str = Field(default="")
    article_number: str = Field(default="")
    article_content: str = Field(default="")
    relevance_score: float = Field(default=0.0)
    effective_status: str = Field(default="现行有效")  # 现行有效/已被修改/已废止
    category: str = Field(default="")
    publish_year: str = Field(default="")
    source: str = Field(default="")  # semantic/keyword/exact


# =============================================================================
# State Definition
# =============================================================================

class LawRetrievalState(AgentState):
    """State for the Law Retrieval Agent.

    Attributes:
        query: The user's law search query.
        parsed_keywords: Keywords extracted from the query.
        category_filter: Optional law category filter.
        semantic_results: Results from semantic search.
        keyword_results: Results from keyword search.
        exact_results: Results from exact match search.
        reranked_results: Final reranked results.
        search_strategy: The search strategy used.
    """

    query: str = Field(default="")
    parsed_keywords: list[str] = Field(default_factory=list)
    category_filter: str = Field(default="")
    semantic_results: list[dict[str, Any]] = Field(default_factory=list)
    keyword_results: list[dict[str, Any]] = Field(default_factory=list)
    exact_results: list[dict[str, Any]] = Field(default_factory=list)
    reranked_results: list[dict[str, Any]] = Field(default_factory=list)
    search_strategy: str = Field(default="multi")


# =============================================================================
# Law Retrieval Agent
# =============================================================================

class LawRetrievalAgent(BaseAgent[LawRetrievalState]):
    """Agent for multi-strategy law article retrieval.

    Workflow:
        1. parse_query - Extract keywords and identify law category
        2. semantic_search - Perform semantic similarity search
        3. keyword_search - Perform keyword-based search
        4. exact_search - Resolve explicit 《X法》第Y条 references from PostgreSQL
        5. rerank_results - Merge and rerank all results
    """

    def __init__(self, name: str = "law_retrieval_agent") -> None:
        super().__init__(name=name)
        self._llm_service: Optional[ChatLLMService] = None

    @property
    def llm_service(self) -> ChatLLMService:
        if self._llm_service is None:
            self._llm_service = get_llm_service()
        return self._llm_service

    def _build_graph(self) -> StateGraph:
        """Build the LangGraph StateGraph for law retrieval.

        Nodes:
            - parse_query: Parse query into keywords and identify category
            - semantic_search: Semantic similarity search
            - keyword_search: Keyword-based search
            - rerank_results: Merge and rerank results
        """
        builder = StateGraph(LawRetrievalState)

        builder.add_node("parse_query", self._parse_query_node)
        builder.add_node("semantic_search", self._semantic_search_node)
        builder.add_node("keyword_search", self._keyword_search_node)
        builder.add_node("exact_search", self._exact_search_node)
        builder.add_node("rerank_results", self._rerank_results_node)

        # Parallel execution: all three search strategies run after parse_query
        builder.set_entry_point("parse_query")
        builder.add_edge("parse_query", "semantic_search")
        builder.add_edge("parse_query", "keyword_search")
        builder.add_edge("parse_query", "exact_search")
        builder.add_edge("semantic_search", "rerank_results")
        builder.add_edge("keyword_search", "rerank_results")
        builder.add_edge("exact_search", "rerank_results")
        builder.add_edge("rerank_results", END)

        return builder

    # -------------------------------------------------------------------------
    # Node: parse_query
    # -------------------------------------------------------------------------

    async def _parse_query_node(self, state: LawRetrievalState) -> dict[str, Any]:
        """Extract keywords and carry through any explicit category filter.

        This node does NOT call the LLM. The downstream semantic and keyword
        search nodes consume the raw query directly, and ``rerank_results``
        uses the rule-based ``detect_domain()``, so the LLM's parsed keywords
        and category never influenced the retrieved results — it only added an
        LLM round-trip (previously bounded by a 10s timeout) to every search.
        Use deterministic rule-based extraction to keep law-search latency low.
        """
        query = state.query
        category_filter = state.category_filter

        if not query:
            return {"parsed_keywords": [], "category_filter": ""}

        # Rule-based keyword extraction (segment on punctuation/whitespace).
        keywords = [w.strip() for w in re.split(r"[,，、;；\s]+", query) if w.strip()]
        if not keywords:
            keywords = [query]

        return {"parsed_keywords": keywords, "category_filter": category_filter}

    # -------------------------------------------------------------------------
    # Node: semantic_search
    # -------------------------------------------------------------------------

    async def _semantic_search_node(self, state: LawRetrievalState) -> dict[str, Any]:
        """Perform semantic similarity search using the real RAG pipeline.

        Uses Milvus vector search + cross-encoder reranking to retrieve
        real legal articles from the knowledge base.
        """
        query = state.query

        if not query:
            return {"semantic_results": []}

        try:
            from app.rag.advanced_retriever import get_rag_pipeline
            pipeline = get_rag_pipeline()
            # Cross-encoder reranking (BGE-Reranker-V2-M3) runs on CPU in this
            # deployment and takes 70-150s for ~24 candidates, blowing past the
            # nginx 120s timeout (504). Use the Milvus BGE-M3 cosine scores
            # directly — they are already high-quality semantic signals — and
            # leave final merge/boost to the rerank_results node.
            results = pipeline.retrieve(
                query=query,
                top_k=8,
                use_bm25=False,
                use_vector=True,
                use_reranker=False,
            )
            # Normalize keys and tag source
            normalized: list[dict[str, Any]] = []
            for r in results:
                normalized.append({
                    "law_name": r.get("law_name", ""),
                    "article_number": r.get("article_number", ""),
                    "article_content": r.get("content", ""),
                    "relevance_score": r.get("rerank_score", r.get("score", 0.0)),
                    "effective_status": "现行有效",
                    "publish_year": "",
                    "category": r.get("category", ""),
                    "source": "semantic",
                })
            return {"semantic_results": normalized}
        except Exception as exc:
            logger.warning("Semantic search via RAG pipeline failed: %s", exc)
            return []

    # -------------------------------------------------------------------------
    # Node: keyword_search
    # -------------------------------------------------------------------------

    async def _keyword_search_node(self, state: LawRetrievalState) -> dict[str, Any]:
        """Perform keyword-based search using the real BM25 pipeline.

        Uses BM25 keyword retrieval to find legal articles matching
        the user's query terms.
        """
        query = state.query

        if not query:
            return {"keyword_results": []}

        try:
            from app.rag.advanced_retriever import get_rag_pipeline
            pipeline = get_rag_pipeline()
            results = pipeline.retrieve(
                query=query,
                top_k=8,
                use_bm25=True,
                use_vector=False,
                use_reranker=False,
            )
            # Normalize keys and tag source
            normalized: list[dict[str, Any]] = []
            for r in results:
                normalized.append({
                    "law_name": r.get("law_name", ""),
                    "article_number": r.get("article_number", ""),
                    "article_content": r.get("content", ""),
                    "relevance_score": r.get("bm25_score", r.get("score", 0.0)),
                    "effective_status": "现行有效",
                    "publish_year": "",
                    "category": r.get("category", ""),
                    "source": "keyword",
                })
            return {"keyword_results": normalized}
        except Exception as exc:
            logger.warning("Keyword search via BM25 pipeline failed: %s", exc)
            return []

    # -------------------------------------------------------------------------
    # Node: exact_search
    # -------------------------------------------------------------------------

    async def _exact_search_node(self, state: LawRetrievalState) -> dict[str, Any]:
        """Resolve explicit article references (《X法》第Y条) against PostgreSQL.

        Vector search ranks by embedding similarity, so short articles
        referenced by number (e.g. 民法典第三百四十条) are routinely pushed
        out of top-k by longer texts on the same topic. When the query
        names a law and an article number, this node fetches that exact
        article from the source-of-truth table instead.
        """
        refs = _extract_article_refs(state.query)
        if not refs:
            return {"exact_results": []}

        normalized: list[dict[str, Any]] = []
        try:
            from sqlalchemy import text
            from app.core.database import async_session_factory

            # Law-name resolution + article lookup in one query. Chinese law
            # names are suffix-heavy ("合同法" is a suffix of "劳动合同法") and
            # the datastore keeps duplicate and "-编/分则" split entries, so
            # the ranking is: exact short-name match first, then split
            # entries, then the canonical full form, then everything else by
            # ascending length. Chunk is guaranteed to be pure Chinese chars
            # (see _LAW_CHUNK_RE), so no LIKE wildcards can be injected.
            stmt = text(
                "SELECT l.name AS law_name, a.content, a.effective_status, a.tags "
                "FROM legal_articles a JOIN laws l ON a.law_id = l.id "
                "WHERE a.article_number = :article "
                "  AND (:chunk LIKE '%' || regexp_replace(l.name, '^中华人民共和国', '') || '%' "
                "       OR l.name LIKE '%' || :chunk || '%') "
                "ORDER BY "
                "  (regexp_replace(l.name, '^中华人民共和国', '') = :chunk) DESC, "
                "  (l.name LIKE '中华人民共和国' || :chunk || '-%') DESC, "
                "  (l.name LIKE '中华人民共和国' || :chunk || '%') DESC, "
                "  length(l.name) ASC "
                "LIMIT 2"
            )

            async with async_session_factory() as session:
                seen_content: set[str] = set()
                for ref in refs[:3]:
                    chunk = ref["law_chunk"]
                    article_number = ref["article_number"]
                    if not chunk:
                        continue
                    rows = (await session.execute(
                        stmt, {"article": article_number, "chunk": chunk},
                    )).all()
                    if not rows:
                        logger.debug(
                            "Exact match miss: chunk=%r article=%s", chunk, article_number,
                        )
                    for law_name, content, effective_status, tags in rows:
                        if content in seen_content:
                            continue
                        seen_content.add(content)
                        normalized.append({
                            "law_name": law_name,
                            "article_number": article_number,
                            "article_content": content,
                            "relevance_score": 0.98,
                            "effective_status": (
                                "现行有效"
                                if effective_status in ("active", "有效")
                                else (effective_status or "未知")
                            ),
                            "publish_year": "",
                            "category": (tags or "").split(",")[0],
                            "source": "exact",
                        })
        except Exception as exc:
            logger.warning("Exact article search failed: %s", exc)
            return {"exact_results": []}

        return {"exact_results": normalized[:5]}

    # -------------------------------------------------------------------------
    # Node: rerank_results
    # -------------------------------------------------------------------------

    @staticmethod
    def _normalize_keyword_scores(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Min-max normalize BM25 scores to [0, 1] so they are comparable
        with semantic rerank scores (also [0, 1]).

        BM25 raw scores are unbounded (e.g. 14, 63) and would otherwise
        dominate the merged ranking, crowding semantic results out.
        """
        if not results:
            return results

        scores = [r.get("relevance_score", 0.0) for r in results]
        lo = min(scores)
        hi = max(scores)
        span = (hi - lo) or 1.0

        for r in results:
            raw = r.get("relevance_score", 0.0)
            r["relevance_score"] = (raw - lo) / span

        return results

    @staticmethod
    def _weighted_score(
        result: dict[str, Any],
        score: float,
        base_weight: float,
        domain: dict[str, Any] | None,
    ) -> float:
        """Apply source weight + domain boost/reject to a raw relevance score."""
        weight = base_weight
        if domain:
            law_name = result.get("law_name", "") or ""
            if any(hint in law_name for hint in domain["reject"]):
                weight *= DOMAIN_REJECT
            elif any(hint in law_name for hint in domain["boost"]):
                weight *= DOMAIN_BOOST
        return min(score * weight, 1.0)

    async def _rerank_results_node(self, state: LawRetrievalState) -> dict[str, Any]:
        """Merge results from all strategies, deduplicate, and rerank."""
        semantic_results = state.semantic_results
        # BM25 raw scores are unbounded and noisy; min-max normalize to [0,1]
        # then down-weight relative to the high-quality semantic cosine scores.
        keyword_results = self._normalize_keyword_scores(state.keyword_results)
        exact_results = state.exact_results
        domain = detect_domain(state.query)

        # Weight each strategy and apply domain boost/reject.
        all_results: list[dict[str, Any]] = []
        for r in semantic_results:
            r = dict(r)
            r["relevance_score"] = self._weighted_score(
                r, r.get("relevance_score", 0.0), SEMANTIC_WEIGHT, domain,
            )
            all_results.append(r)
        for r in keyword_results:
            r = dict(r)
            r["relevance_score"] = self._weighted_score(
                r, r.get("relevance_score", 0.0), KEYWORD_WEIGHT, domain,
            )
            all_results.append(r)
        for r in exact_results:
            r = dict(r)
            # The user explicitly named this article — domain heuristics
            # must not demote or promote an exact reference.
            r["relevance_score"] = min(r.get("relevance_score", 0.0), 1.0)
            all_results.append(r)

        if not all_results:
            return {"reranked_results": [], "final_output": "未检索到相关法律条文。请尝试调整查询关键词或扩大检索范围。"}

        # Deduplicate by law_name + article_number, keeping the strongest hit.
        best: dict[tuple[str, str], dict[str, Any]] = {}
        sources_by_key: dict[tuple[str, str], set[str]] = {}
        for r in all_results:
            key = (r.get("law_name", ""), r.get("article_number", ""))
            sources_by_key.setdefault(key, set()).add(r.get("source", ""))
            if key not in best or r["relevance_score"] > best[key]["relevance_score"]:
                best[key] = r

        deduped: list[dict[str, Any]] = []
        for key, r in best.items():
            r["multi_source"] = len(sources_by_key[key]) > 1
            deduped.append(r)

        # Boost items corroborated by multiple strategies.
        for r in deduped:
            if r.get("multi_source"):
                r["relevance_score"] = min(r.get("relevance_score", 0) * 1.2, 1.0)

        deduped.sort(key=lambda x: x.get("relevance_score", 0), reverse=True)

        # Limit to top 10
        reranked = deduped[:10]

        # Format final output
        final_output = self._format_retrieval_output(state.query, reranked)

        return {
            "reranked_results": reranked,
            "final_output": final_output,
        }

    def _format_retrieval_output(
        self,
        query: str,
        results: list[dict[str, Any]],
    ) -> str:
        """Format retrieval results into a readable output."""
        if not results:
            return "未检索到相关法律条文。"

        parts: list[str] = [
            f"## 法律检索结果\n",
            f"**检索查询**：{query}\n",
            f"**命中条数**：{len(results)}条\n",
            "---\n",
        ]

        for i, r in enumerate(results, 1):
            law_name = r.get("law_name", "")
            article_number = r.get("article_number", "")
            article_content = r.get("article_content", "")
            relevance = r.get("relevance_score", 0)
            status = r.get("effective_status", "未知")
            publish_year = r.get("publish_year", "")
            source = r.get("source", "")

            source_label = {
                "semantic": "语义检索",
                "keyword": "关键词检索",
                "exact": "精确匹配",
            }.get(source, source)

            parts.append(f"### {i}. 《{law_name}》")
            if publish_year:
                parts.append(f"**发布/修订年份**：{publish_year}")
            parts.append(f"**{normalize_article_number(article_number)}**")
            parts.append(f"\n> {article_content}\n")
            parts.append(f"- 相关度：{relevance:.2f} | 效力状态：{status} | 检索方式：{source_label}")
            parts.append("")

        parts.append("---")
        parts.append(LEGAL_DISCLAIMER)

        return "\n".join(parts)

    # -------------------------------------------------------------------------
    # Public run method
    # -------------------------------------------------------------------------

    async def run(self, input_data: dict[str, Any]) -> dict[str, Any]:
        """Execute the law retrieval agent.

        Args:
            input_data: Must contain 'query' key. Optional 'category_filter'.

        Returns:
            Dictionary with reranked_results and final_output.
        """
        query = input_data.get("query", "")
        if not query:
            return {
                "final_output": "请提供需要检索的法律问题或关键词。",
                "reranked_results": [],
                "parsed_keywords": [],
            }

        initial_state: dict[str, Any] = {
            "query": query,
            "parsed_keywords": [],
            "category_filter": input_data.get("category_filter", ""),
            "semantic_results": [],
            "keyword_results": [],
            "exact_results": [],
            "reranked_results": [],
            "search_strategy": "multi",
            "messages": [HumanMessage(content=f"检索：{query}")],
            "context": {},
            "final_output": "",
        }

        graph = self.compile()
        result = await graph.ainvoke(initial_state)

        return {
            "final_output": result.get("final_output", ""),
            "reranked_results": result.get("reranked_results", []),
            "parsed_keywords": result.get("parsed_keywords", []),
            "category_filter": result.get("category_filter", ""),
        }

    def run_sync(self, input_data: dict[str, Any]) -> dict[str, Any]:
        """Synchronous wrapper for the run method."""
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import nest_asyncio
                nest_asyncio.apply()
            return loop.run_until_complete(self.run(input_data))
        except RuntimeError:
            return asyncio.run(self.run(input_data))


# =============================================================================
# Factory function
# =============================================================================

def create_law_retrieval_agent() -> LawRetrievalAgent:
    """Create and return a LawRetrievalAgent instance."""
    return LawRetrievalAgent()