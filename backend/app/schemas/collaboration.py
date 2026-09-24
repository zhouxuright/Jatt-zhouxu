"""Collaboration schemas -- request/response models for multi-agent collaboration endpoints."""

import re
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Pattern matching null bytes and ASCII control characters
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


# =============================================================================
# Request Schemas
# =============================================================================

class CollaborationAnalyzeRequest(BaseModel):
    """Request body for complex multi-agent analysis."""
    query: str = Field(
        ...,
        min_length=1,
        max_length=10000,
        description="Complex legal question requiring multi-agent collaboration",
    )
    context: dict[str, Any] | None = Field(
        default=None,
        description="Optional context information for the analysis",
    )
    stream: bool = Field(
        default=False,
        description="Whether to stream the response",
    )

    @field_validator("query")
    @classmethod
    def sanitize_query(cls, v: str) -> str:
        """Strip null bytes and control characters from user input."""
        return _CONTROL_CHAR_RE.sub("", v)


class CollaborationResearchRequest(BaseModel):
    """Request body for deep legal research."""
    legal_question: str = Field(
        ...,
        min_length=1,
        max_length=10000,
        description="Legal question for deep research",
    )
    focus_areas: list[str] | None = Field(
        default=None,
        description="Optional specific areas to focus the research on",
    )
    max_sources: int = Field(
        default=20,
        ge=1,
        le=50,
        description="Maximum number of sources to retrieve",
    )

    @field_validator("legal_question")
    @classmethod
    def sanitize_question(cls, v: str) -> str:
        """Strip null bytes and control characters from user input."""
        return _CONTROL_CHAR_RE.sub("", v)


class CollaborationReviewRequest(BaseModel):
    """Request body for quality review of text."""
    text: str = Field(
        ...,
        min_length=1,
        max_length=20000,
        description="Text to review for quality",
    )
    review_type: str = Field(
        default="general",
        description="Type of review: general, legal_response, contract_analysis, document",
    )
    original_query: str | None = Field(
        default=None,
        description="Original query that produced the text (for context)",
    )

    @field_validator("text")
    @classmethod
    def sanitize_text(cls, v: str) -> str:
        """Strip null bytes and control characters from user input."""
        return _CONTROL_CHAR_RE.sub("", v)


# =============================================================================
# Response Schemas
# =============================================================================

class CollaborationAnalyzeResponse(BaseModel):
    """Response for complex multi-agent analysis."""
    final_response: str = Field(..., description="The synthesized response from all agents")
    intent: str = Field(default="", description="Classified task intent")
    collaboration_pattern: str = Field(default="", description="Collaboration pattern used")
    agents_involved: list[str] = Field(
        default_factory=list,
        description="List of agents that participated",
    )
    iterations: int = Field(default=0, description="Number of refinement iterations")
    disclaimer: str = Field(
        default="重要提示：以下内容由 AI 生成，仅供学习参考，不构成正式法律建议。具体法律问题请咨询持证律师。",
        description="AI response disclaimer",
    )
    created_at: datetime = Field(default_factory=datetime.now)


class CollaborationResearchResponse(BaseModel):
    """Response for deep legal research."""
    relevant_laws: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Relevant law articles found",
    )
    relevant_cases: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Relevant court cases found",
    )
    judicial_interpretations: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Relevant judicial interpretations found",
    )
    analysis: str = Field(default="", description="Comprehensive analysis text")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="Research confidence score")
    sources_cited: list[str] = Field(
        default_factory=list,
        description="All sources cited in the analysis",
    )
    disclaimer: str = Field(
        default="重要提示：以下内容由 AI 生成，仅供学习参考，不构成正式法律建议。具体法律问题请咨询持证律师。",
        description="AI response disclaimer",
    )
    created_at: datetime = Field(default_factory=datetime.now)


class CollaborationReviewResponse(BaseModel):
    """Response for quality review."""
    quality_score: int = Field(default=0, ge=0, le=100, description="Overall quality score")
    issues: list[str] = Field(
        default_factory=list,
        description="Issues found during review",
    )
    suggestions: list[str] = Field(
        default_factory=list,
        description="Suggestions for improvement",
    )
    corrected_text: str = Field(default="", description="Corrected version of the text")
    citation_check: dict[str, Any] = Field(
        default_factory=dict,
        description="Citation verification results",
    )
    completeness_check: dict[str, Any] = Field(
        default_factory=dict,
        description="Completeness verification results",
    )
    created_at: datetime = Field(default_factory=datetime.now)


class CollaborationPatternInfo(BaseModel):
    """Information about a collaboration pattern."""
    id: str = Field(..., description="Pattern identifier")
    name: str = Field(..., description="Pattern display name")
    description: str = Field(..., description="Pattern description")
    use_case: str = Field(default="", description="When to use this pattern")


class CollaborationPatternsResponse(BaseModel):
    """Response listing available collaboration patterns."""
    patterns: list[CollaborationPatternInfo] = Field(
        default_factory=list,
        description="Available collaboration patterns",
    )
