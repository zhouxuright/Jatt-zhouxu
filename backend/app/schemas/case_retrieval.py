"""Case retrieval schemas -- request/response models for court case search."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class CaseSearchRequest(BaseModel):
    """Request body for searching court cases."""
    query: str = Field(..., min_length=1, max_length=500, description="Search query")
    case_type: str | None = Field(default=None, description="Filter by case type: 民事/刑事/行政")
    cause_of_action: str | None = Field(default=None, description="Filter by cause of action")
    court_name: str | None = Field(default=None, description="Filter by court name")
    year_from: int | None = Field(default=None, ge=1900, le=2100, description="Year from")
    year_to: int | None = Field(default=None, ge=1900, le=2100, description="Year to")
    top_k: int = Field(default=10, ge=1, le=50, description="Number of results")
    semantic: bool = Field(
        default=True,
        description="Enable vector semantic search (Milvus BGE-M3) hybridized with keyword search.",
    )


class CaseItem(BaseModel):
    """A single court case search result."""
    id: str
    case_number: str
    title: str
    court_name: str | None = None
    case_type: str | None = None
    cause_of_action: str | None = None
    decision_date: str | None = None
    summary: str | None = None
    key_points: str | None = None
    referenced_laws: str | None = None
    tags: str | None = None
    relevance_score: float = Field(default=0.0, description="Relevance score (0.0-1.0)")


class CaseSearchResponse(BaseModel):
    """Response for court case search."""
    query: str
    total: int
    results: list[CaseItem]
    ai_summary: str | None = Field(default=None, description="AI-generated summary of results")
    search_mode: str = Field(
        default="keyword",
        description="Search mode used: semantic_hybrid (vector+keyword) or keyword.",
    )


class CaseDetailResponse(BaseModel):
    """Detailed court case response."""
    model_config = ConfigDict(from_attributes=True)

    id: str
    case_number: str
    title: str
    court_name: str | None = None
    case_type: str | None = None
    cause_of_action: str | None = None
    decision_date: str | None = None
    parties: str | None = None
    summary: str | None = None
    full_text: str | None = None
    key_points: str | None = None
    referenced_laws: str | None = None
    judgment_result: str | None = None
    tags: str | None = None
    created_at: datetime


class CaseCategoriesResponse(BaseModel):
    """Response listing available case categories."""
    case_types: list[str] = Field(default_factory=list)
    causes_of_action: list[str] = Field(default_factory=list)
    courts: list[str] = Field(default_factory=list)
