"""Law search endpoints -- legal article retrieval.

Uses the LawRetrievalAgent for multi-strategy semantic and keyword search.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.law_retrieval_agent import (
    LawRetrievalAgent,
    LAW_CATEGORIES,
    CATEGORY_LAW_MAP,
    create_law_retrieval_agent,
)
from app.api.deps import get_current_user
from app.core.database import get_db
from app.models.user import User
from pydantic import BaseModel, Field

router = APIRouter()

# ---------------------------------------------------------------------------
# Lazy-loaded agent instance
# ---------------------------------------------------------------------------
_law_retrieval_agent: LawRetrievalAgent | None = None


def get_law_retrieval_agent() -> LawRetrievalAgent:
    """Return a singleton LawRetrievalAgent instance."""
    global _law_retrieval_agent
    if _law_retrieval_agent is None:
        _law_retrieval_agent = create_law_retrieval_agent()
    return _law_retrieval_agent


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class LawSearchRequest(BaseModel):
    """Request body for law article search."""
    query: str = Field(..., min_length=1, max_length=500, description="Search query for law articles")
    category: str | None = Field(default=None, description="Optional law category filter")
    top_k: int = Field(default=10, ge=1, le=50, description="Number of results to return")


class LawArticleItem(BaseModel):
    """A single law article search result."""
    law_name: str = Field(default="", description="Full name of the law")
    article_number: str = Field(default="", description="Article number")
    article_content: str = Field(default="", description="Full text of the article")
    relevance_score: float = Field(default=0.0, description="Relevance score (0.0-1.0)")
    effective_status: str = Field(default="现行有效", description="Effectiveness status")
    category: str = Field(default="", description="Law category")
    publish_year: str = Field(default="", description="Year of publication/revision")
    source: str = Field(default="", description="Search strategy: semantic/keyword")


class LawSearchResponse(BaseModel):
    """Response for law article search."""
    query: str = Field(default="", description="Original search query")
    total: int = Field(default=0, description="Total number of results")
    results: list[LawArticleItem] = Field(default_factory=list, description="Search results")
    formatted_output: str = Field(default="", description="Formatted markdown output")


class LawArticleDetail(BaseModel):
    """Detailed article information."""
    article_id: str = Field(default="", description="Unique article identifier")
    law_name: str = Field(default="", description="Full name of the law")
    title: str = Field(default="", description="Article title")
    article_number: str = Field(default="", description="Article number")
    content: str = Field(default="", description="Full article content")
    effective_date: str = Field(default="", description="Effective date")
    status: str = Field(default="active", description="Status: active/amended/repealed")
    tags: list[str] = Field(default_factory=list, description="Article tags")


class LawCategoryItem(BaseModel):
    """A law category with its contained laws."""
    category: str = Field(default="", description="Category name")
    laws: list[str] = Field(default_factory=list, description="Laws in this category")


class LawCategoriesResponse(BaseModel):
    """Response listing all law categories."""
    categories: list[LawCategoryItem] = Field(default_factory=list)
    total: int = Field(default=0)


# ---------------------------------------------------------------------------
# POST /law/search
# ---------------------------------------------------------------------------


@router.post("/search", response_model=LawSearchResponse)
async def search_law_articles(
    payload: LawSearchRequest,
    current_user: Annotated[User, Depends(get_current_user)],
) -> LawSearchResponse:
    """Search for law articles by query using the LawRetrievalAgent.

    The agent performs multi-strategy search (semantic + keyword) and
    returns reranked results with relevance scores.
    """
    if not payload.query.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Search query cannot be empty.",
        )

    # Validate category if provided
    category_filter = ""
    if payload.category:
        if payload.category in LAW_CATEGORIES:
            category_filter = LAW_CATEGORIES[payload.category]
        elif payload.category in CATEGORY_LAW_MAP:
            category_filter = payload.category
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unknown category: {payload.category}. "
                       f"Available: {', '.join(LAW_CATEGORIES.keys())}",
            )

    try:
        agent = get_law_retrieval_agent()
        agent_result = await agent.run({
            "query": payload.query,
            "category_filter": category_filter,
        })
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Law search failed: {str(exc)}",
        )

    reranked_results = agent_result.get("reranked_results", [])
    final_output = agent_result.get("final_output", "")

    # Limit to top_k
    results = reranked_results[:payload.top_k]

    # Map to response items
    items: list[LawArticleItem] = []
    for r in results:
        items.append(
            LawArticleItem(
                law_name=r.get("law_name", ""),
                article_number=r.get("article_number", ""),
                article_content=r.get("article_content", ""),
                relevance_score=r.get("relevance_score", 0.0),
                effective_status=r.get("effective_status", "现行有效"),
                category=r.get("category", ""),
                publish_year=r.get("publish_year", ""),
                source=r.get("source", ""),
            )
        )

    return LawSearchResponse(
        query=payload.query,
        total=len(results),
        results=items,
        formatted_output=final_output,
    )


# ---------------------------------------------------------------------------
# GET /law/articles
# ---------------------------------------------------------------------------


@router.get("/articles", response_model=LawCategoriesResponse)
async def list_law_articles(
    current_user: Annotated[User, Depends(get_current_user)],
    category: str | None = Query(default=None, description="Filter by category"),
) -> LawCategoriesResponse:
    """List all available law articles, organized by category.

    Returns the category hierarchy with available law names.
    """
    categories: list[LawCategoryItem] = []

    if category and category in CATEGORY_LAW_MAP:
        # Return only the requested category
        categories.append(
            LawCategoryItem(
                category=category,
                laws=CATEGORY_LAW_MAP[category],
            )
        )
    else:
        # Return all categories
        for cat_name, laws in CATEGORY_LAW_MAP.items():
            categories.append(
                LawCategoryItem(
                    category=cat_name,
                    laws=laws,
                )
            )

    return LawCategoriesResponse(
        categories=categories,
        total=len(categories),
    )


# ---------------------------------------------------------------------------
# GET /law/articles/{category}
# ---------------------------------------------------------------------------


@router.get("/articles/{category_name}", response_model=LawCategoryItem)
async def get_articles_by_category(
    category_name: str,
    current_user: Annotated[User, Depends(get_current_user)],
) -> LawCategoryItem:
    """Get articles for a specific law category."""
    if category_name not in CATEGORY_LAW_MAP:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Category not found: {category_name}. "
                   f"Available: {', '.join(CATEGORY_LAW_MAP.keys())}",
        )

    return LawCategoryItem(
        category=category_name,
        laws=CATEGORY_LAW_MAP[category_name],
    )


# ---------------------------------------------------------------------------
# GET /law/categories
# ---------------------------------------------------------------------------


@router.get("/categories")
async def list_law_categories(
    current_user: Annotated[User, Depends(get_current_user)],
) -> dict[str, str]:
    """List all available law categories with their descriptions."""
    return LAW_CATEGORIES

# ===========================================================================
# P0-3 引用可溯源：核验与原文溯源
# ===========================================================================

class CitationTraceItem(BaseModel):
    """单条引用的溯源结果。"""
    found: bool
    match_type: str | None = None
    law: dict[str, Any] | None = None
    article: dict[str, Any] | None = None
    provenance: str = "unknown"
    message: str = ""


class CitationTraceResponse(BaseModel):
    """批量溯源响应。"""
    query_count: int
    found_count: int
    items: list[CitationTraceItem]


class CitationVerifyRequest(BaseModel):
    """引用核验请求。"""
    text: str = Field(..., min_length=1, max_length=50000,
                      description="待核验的文本（通常是一段 AI 回答）")


@router.get("/citations/trace", response_model=CitationTraceResponse)
async def trace_citations(
    db: Annotated[AsyncSession, Depends(get_db)],
    _user: Annotated[User, Depends(get_current_user)],
    law_name: str = Query(..., min_length=1, max_length=120, description="法律名称（全称或简称）"),
    article_number: str = Query(..., min_length=1, max_length=32, description="条号，如 120 / 一百二十"),
) -> CitationTraceResponse:
    """溯源查询：把"《法名》第X条"还原为可核对的权威原文（P0-3）。

    面向客户与监管的**可验证性**：任何引用都能一键回查原文、条号与效力状态。
    """
    from app.services.citation_verifier import resolve_citation_detail

    detail = await resolve_citation_detail(db, law_name, article_number)
    item = CitationTraceItem(**detail)
    return CitationTraceResponse(
        query_count=1,
        found_count=1 if item.found else 0,
        items=[item],
    )


@router.post("/citations/verify")
async def verify_answer_citations(
    payload: CitationVerifyRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    _user: Annotated[User, Depends(get_current_user)],
) -> dict[str, Any]:
    """核验一段文本中的全部法律引用，返回逐条状态与整体置信度（P0-3）。"""
    from app.services.citation_verifier import build_citation_gate, verify_citations

    result = await verify_citations(db, payload.text)
    result["gate"] = build_citation_gate(result)
    return result
