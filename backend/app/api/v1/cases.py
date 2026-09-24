"""Case retrieval endpoints -- court case search with AI-powered analysis.

Uses hybrid search: full-text PostgreSQL search + AI summarization.
"""

import json
import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.models.legal_knowledge import CourtCase, Law, LegalArticle
from app.models.user import User
from app.schemas.case_retrieval import (
    CaseCategoriesResponse,
    CaseDetailResponse,
    CaseItem,
    CaseSearchRequest,
    CaseSearchResponse,
)
from app.services.llm_service import get_raw_llm_service

logger = logging.getLogger(__name__)
router = APIRouter()


# ---------------------------------------------------------------------------
# Helper: keyword extraction for case search
# ---------------------------------------------------------------------------

_CASE_KEYWORDS = {
    "劳动": ["劳动合同", "劳动争议", "经济补偿", "工伤", "加班"],
    "婚姻": ["离婚", "财产分割", "抚养权", "赡养"],
    "合同": ["合同违约", "合同纠纷", "买卖合同", "租赁合同"],
    "刑事": ["盗窃", "诈骗", "故意伤害", "抢劫", "贪污", "受贿"],
    "侵权": ["侵权", "人身损害", "精神损害", "高空抛物"],
    "消费": ["消费者权益", "欺诈", "退货", "赔偿"],
    "知识产权": ["著作权", "专利", "商标", "商业秘密"],
    "房产": ["房屋买卖", "租赁", "物业", "拆迁"],
    "公司": ["股权", "股东", "公司治理", "破产"],
    "继承": ["继承", "遗嘱", "遗产"],
}


def _extract_case_keywords(query: str) -> list[str]:
    """Extract relevant legal keywords from query."""
    keywords = []
    for category, terms in _CASE_KEYWORDS.items():
        if category in query:
            keywords.extend(terms)
    # Also add direct terms
    for term in _CASE_KEYWORDS.values():
        for t in term:
            if t in query and t not in keywords:
                keywords.append(t)
    return keywords[:10]  # Limit to 10 keywords


# ---------------------------------------------------------------------------
# POST /cases/search  -- hybrid: Milvus vector semantics + PostgreSQL keywords
# ---------------------------------------------------------------------------

def _build_case_filters(payload: CaseSearchRequest) -> list:
    """Build the shared PG filter conditions (metadata filters only).

    contains(autoescape=True) emits a bound-parameter LIKE with user wildcards
    escaped -- user input never becomes SQL text.
    """
    filters = []
    if payload.case_type:
        filters.append(CourtCase.case_type == payload.case_type)
    if payload.cause_of_action:
        filters.append(CourtCase.cause_of_action.contains(payload.cause_of_action, autoescape=True))
    if payload.court_name:
        filters.append(CourtCase.court_name.contains(payload.court_name, autoescape=True))
    if payload.year_from:
        year_from = str(payload.year_from) + "-01-01"
        filters.append(CourtCase.decision_date >= year_from)
    if payload.year_to:
        year_to = str(payload.year_to) + "-12-31"
        filters.append(CourtCase.decision_date <= year_to)
    return filters


def _case_to_item(case: CourtCase, score: float) -> CaseItem:
    return CaseItem(
        id=case.id,
        case_number=case.case_number,
        title=case.title,
        court_name=case.court_name,
        case_type=case.case_type,
        cause_of_action=case.cause_of_action,
        decision_date=case.decision_date,
        summary=case.summary,
        key_points=case.key_points,
        referenced_laws=case.referenced_laws,
        tags=case.tags,
        relevance_score=round(score, 4),
    )


# Static fetch ceiling for keyword recall; results are trimmed to top_k in
# Python after merging with semantic hits (schema caps top_k at 50).
KEYWORD_FETCH_LIMIT = 100


@router.post("/search", response_model=CaseSearchResponse)
async def search_cases(
    payload: CaseSearchRequest,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> CaseSearchResponse:
    """Search court cases: semantic vector recall (Milvus/BGE-M3) hybridized
    with keyword search (PostgreSQL), plus AI summarization.

    User input reaches SQL exclusively via SQLAlchemy bound parameters
    (contains(autoescape=True) / in_()); no concatenation into SQL text.
    """

    search_mode = "keyword"
    case_by_id: dict[str, CourtCase] = {}
    scored: list[tuple[CourtCase, float]] = []

    meta_filters = _build_case_filters(payload)

    # ------------------------------------------------------------------
    # 1) Semantic vector recall (Milvus BGE-M3, cosine)
    # ------------------------------------------------------------------
    vector_hits: list[dict[str, Any]] = []
    if payload.semantic:
        try:
            from app.rag.milvus_service import get_milvus_rag_service
            vector_hits = get_milvus_rag_service().search_cases(
                payload.query,
                top_k=payload.top_k,
                case_type=payload.case_type or "",
            )
        except Exception as exc:
            logger.warning("Semantic case search unavailable, falling back to keyword: %s", exc)
            vector_hits = []

    if vector_hits:
        search_mode = "semantic_hybrid"
        hit_ids = [h["id"] for h in vector_hits if h.get("id")]
        hit_scores = {h["id"]: float(h.get("score", 0.0)) for h in vector_hits if h.get("id")}
        if hit_ids:
            result = await db.execute(
                select(CourtCase).where(
                    CourtCase.id.in_(hit_ids),
                    *meta_filters,
                )
            )
            for case in result.scalars().all():
                case_by_id[case.id] = case
                scored.append((case, hit_scores.get(case.id, 0.0)))

    # ------------------------------------------------------------------
    # 2) Keyword recall (always runs; fills gaps and supports no-vector mode)
    # ------------------------------------------------------------------
    keyword_query = select(CourtCase)
    if payload.query:
        # 关键词召回只匹配 title/tags/cause_of_action 三个窄字段，并配套
        # pg_trgm GIN 索引（alembic 003）走索引扫描。summary/key_points/full_text
        # 是巨型 Text 字段，LIKE 会触发百万级全表扫描，且其语义已由 Milvus
        # 向量召回覆盖，故不再参与关键词匹配，避免案例检索耗时膨胀。
        text_filters = [field.contains(payload.query, autoescape=True) for field in [
            CourtCase.title, CourtCase.tags, CourtCase.cause_of_action,
        ]]
        for kw in _extract_case_keywords(payload.query):
            text_filters.append(CourtCase.title.contains(kw, autoescape=True))
            text_filters.append(CourtCase.tags.contains(kw, autoescape=True))
        keyword_query = keyword_query.where(or_(*text_filters))

    if meta_filters:
        keyword_query = keyword_query.where(*meta_filters)

    keyword_query = keyword_query.order_by(CourtCase.decision_date.desc()).limit(KEYWORD_FETCH_LIMIT)
    result = await db.execute(keyword_query)
    keyword_cases = result.scalars().all()

    seen_ids = {c.id for c, _ in scored}
    for case in keyword_cases:
        case_by_id[case.id] = case
        if case.id in seen_ids:
            continue
        seen_ids.add(case.id)
        # Keyword-only hits get a neutral score below any semantic hit
        scored.append((case, 0.5))

    # Vector hits first (higher relevance), then keyword-only hits
    scored.sort(key=lambda pair: pair[1], reverse=True)
    ranked = scored[:payload.top_k]

    items = [_case_to_item(case, score) for case, score in ranked]

    # AI summarization of results
    ai_summary = None
    if items and len(items) >= 2:
        try:
            llm = get_raw_llm_service()
            top_cases = [case_by_id[i.id] for i in items[:5] if i.id in case_by_id]
            case_lines = [
                "- " + c.case_number + ": " + (c.title or "") + " (" + (c.cause_of_action or "未知案由") + ")"
                for c in top_cases
            ]
            nl = chr(10)
            prompt = (
                "用户搜索: " + payload.query
                + nl + "找到 " + str(len(items)) + " 个相关案例:" + nl
                + nl.join(case_lines)
                + nl + nl + "请用 2-3 句话总结这些案例的共同特点和裁判趋势。"
            )

            result = await llm.chat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=200,
            )
            ai_summary = result.get("content", "")
        except Exception as exc:
            logger.warning("AI case summarization failed: %s", exc)

    return CaseSearchResponse(
        query=payload.query,
        total=len(items),
        results=items,
        ai_summary=ai_summary,
        search_mode=search_mode,
    )


# ---------------------------------------------------------------------------
# GET /cases/categories  (MUST be before /{case_id})
# ---------------------------------------------------------------------------

@router.get("/categories", response_model=CaseCategoriesResponse)
async def get_case_categories(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> CaseCategoriesResponse:
    """Get available case categories (types, causes of action, courts)."""

    # Get distinct case types
    type_result = await db.execute(
        select(func.distinct(CourtCase.case_type)).where(
            CourtCase.case_type.isnot(None)
        )
    )
    case_types = [row[0] for row in type_result.all() if row[0]]

    # Get distinct causes of action
    cause_result = await db.execute(
        select(func.distinct(CourtCase.cause_of_action)).where(
            CourtCase.cause_of_action.isnot(None)
        )
    )
    causes = [row[0] for row in cause_result.all() if row[0]]

    # Get distinct courts
    court_result = await db.execute(
        select(func.distinct(CourtCase.court_name)).where(
            CourtCase.court_name.isnot(None)
        )
    )
    courts = [row[0] for row in court_result.all() if row[0]]

    return CaseCategoriesResponse(
        case_types=sorted(case_types),
        causes_of_action=sorted(causes),
        courts=sorted(courts),
    )


# ---------------------------------------------------------------------------
# GET /cases/{case_id}  (MUST be after /categories)
# ---------------------------------------------------------------------------

@router.get("/{case_id}", response_model=CaseDetailResponse)
async def get_case_detail(
    case_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> CaseDetailResponse:
    """Get detailed information about a specific court case."""
    result = await db.execute(
        select(CourtCase).where(CourtCase.id == case_id)
    )
    case = result.scalar_one_or_none()

    if case is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Case not found",
        )

    return CaseDetailResponse.model_validate(case)
