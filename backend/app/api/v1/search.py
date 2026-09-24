"""Web Search API endpoints -- real-time web search for legal information.

Endpoints:
- POST /search/web -- General web search
- POST /search/legal-updates -- Search for recent legal regulation updates
- POST /search/cases -- Search for recent court cases
- GET /search/sources -- List trusted legal sources
"""
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.services.web_search import (
    WebSearchEngine,
    get_web_search_engine,
    summarize_search_results,
    TRUSTED_LEGAL_DOMAINS,
)

logger = logging.getLogger(__name__)
router = APIRouter()


# ============================================================================
# Request / Response schemas
# ============================================================================

class WebSearchRequest(BaseModel):
    """Request for web search."""
    query: str = Field(..., description="Search query", min_length=1, max_length=500)
    num_results: int = Field(default=10, ge=1, le=50, description="Number of results")
    engine: str | None = Field(default=None, description="Search engine (bing/baidu/sogou/serper)")
    legal_only: bool = Field(default=False, description="Filter to legal sources only")
    time_range: str | None = Field(
        default=None,
        description="Time filter (day/week/month/year)",
    )
    summarize: bool = Field(default=True, description="Whether to summarize results with LLM")


class SearchResultItem(BaseModel):
    """A single search result."""
    title: str
    url: str
    snippet: str
    source: str = ""
    published_date: str = ""
    relevance_score: float = 0.0
    is_legal_source: bool = False


class WebSearchResponse(BaseModel):
    """Response from web search."""
    query: str
    results: list[SearchResultItem] = Field(default_factory=list)
    summary: str = ""
    total_found: int = 0
    search_engine: str = ""
    elapsed_seconds: float = 0.0
    error: str | None = None


class LegalUpdateRequest(BaseModel):
    """Request for legal regulation updates."""
    topic: str = Field(..., description="Legal topic to monitor", min_length=1, max_length=200)
    days_back: int = Field(default=30, ge=1, le=365, description="How many days back to search")
    summarize: bool = Field(default=True, description="Whether to summarize results")


class CaseSearchRequest(BaseModel):
    """Request for court case search."""
    keywords: str = Field(..., description="Search keywords", min_length=1, max_length=200)
    case_type: str | None = Field(default=None, description="Case type filter")
    summarize: bool = Field(default=True)


# ============================================================================
# POST /search/web -- General web search
# ============================================================================

@router.post("/search/web", response_model=WebSearchResponse, summary="Web search")
async def web_search(request: WebSearchRequest) -> WebSearchResponse:
    """Perform a web search with optional LLM summarization.

    Supports multiple search engines with automatic fallback.
    Can filter to only trusted legal sources.
    """
    engine = get_web_search_engine()
    response = await engine.search(
        query=request.query,
        num_results=request.num_results,
        engine=request.engine,
        legal_only=request.legal_only,
        time_range=request.time_range,
    )

    summary = ""
    if request.summarize and response.results:
        summary = await summarize_search_results(
            request.query, response.results
        )

    return WebSearchResponse(
        query=response.query,
        results=[
            SearchResultItem(
                title=r.title,
                url=r.url,
                snippet=r.snippet,
                source=r.source,
                published_date=r.published_date,
                relevance_score=r.relevance_score,
                is_legal_source=r.is_legal_source,
            )
            for r in response.results
        ],
        summary=summary,
        total_found=response.total_found,
        search_engine=response.search_engine,
        elapsed_seconds=response.elapsed_seconds,
        error=response.error,
    )


# ============================================================================
# POST /search/legal-updates -- Legal regulation updates
# ============================================================================

@router.post("/search/legal-updates", response_model=WebSearchResponse, summary="Legal regulation updates")
async def legal_updates(request: LegalUpdateRequest) -> WebSearchResponse:
    """Search for recent legal regulation updates on a specific topic.

    Queries government and court sources for new regulations,
    judicial interpretations, and policy changes.
    """
    engine = get_web_search_engine()
    response = await engine.search_legal_updates(
        topic=request.topic,
        days_back=request.days_back,
    )

    summary = ""
    if request.summarize and response.results:
        summary = await summarize_search_results(
            f"关于{request.topic}的最新法律法规变化", response.results
        )

    return WebSearchResponse(
        query=response.query,
        results=[
            SearchResultItem(
                title=r.title,
                url=r.url,
                snippet=r.snippet,
                source=r.source,
                published_date=r.published_date,
                relevance_score=r.relevance_score,
                is_legal_source=r.is_legal_source,
            )
            for r in response.results
        ],
        summary=summary,
        total_found=response.total_found,
        search_engine=response.search_engine,
        elapsed_seconds=response.elapsed_seconds,
        error=response.error,
    )


# ============================================================================
# POST /search/cases -- Court case search
# ============================================================================

@router.post("/search/cases", response_model=WebSearchResponse, summary="Court case web search")
async def case_web_search(request: CaseSearchRequest) -> WebSearchResponse:
    """Search for recent court cases by keywords via web search."""
    engine = get_web_search_engine()
    response = await engine.search_cases(
        keywords=request.keywords,
        case_type=request.case_type,
    )

    summary = ""
    if request.summarize and response.results:
        summary = await summarize_search_results(
            f"关于{request.keywords}的相关案例", response.results
        )

    return WebSearchResponse(
        query=response.query,
        results=[
            SearchResultItem(
                title=r.title,
                url=r.url,
                snippet=r.snippet,
                source=r.source,
                published_date=r.published_date,
                relevance_score=r.relevance_score,
                is_legal_source=r.is_legal_source,
            )
            for r in response.results
        ],
        summary=summary,
        total_found=response.total_found,
        search_engine=response.search_engine,
        elapsed_seconds=response.elapsed_seconds,
        error=response.error,
    )


# ============================================================================
# GET /search/sources -- Trusted legal sources
# ============================================================================

@router.get("/search/sources", summary="List trusted legal sources")
async def list_trusted_sources() -> dict[str, Any]:
    """List all trusted legal information sources used for filtering."""
    sources = sorted(TRUSTED_LEGAL_DOMAINS)
    return {
        "trusted_domains": sources,
        "total": len(sources),
        "description": "以下域名被系统识别为权威法律信息来源",
    }
