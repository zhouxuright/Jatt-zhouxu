"""Collaboration API endpoints -- multi-agent collaboration, deep research, and quality review.

Endpoints:
- POST /collaboration/analyze -- Complex multi-agent analysis
- POST /collaboration/research -- Deep legal research
- POST /collaboration/review -- Quality review of any text
- GET /collaboration/patterns -- List available collaboration patterns
"""
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, status

from app.schemas.collaboration import (
    CollaborationAnalyzeRequest,
    CollaborationAnalyzeResponse,
    CollaborationResearchRequest,
    CollaborationResearchResponse,
    CollaborationReviewRequest,
    CollaborationReviewResponse,
    CollaborationPatternInfo,
    CollaborationPatternsResponse,
)
from app.agents.collaboration import (
    MultiAgentCollaborator,
    create_multi_agent_collaborator,
    COLLABORATION_PATTERNS,
)
from app.agents.research_agent import DeepResearchAgent, create_deep_research_agent
from app.agents.review_agent import QualityReviewAgent, create_quality_review_agent

logger = logging.getLogger(__name__)
router = APIRouter()


# ============================================================================
# Singleton instances (lazy initialization)
# ============================================================================

_collaborator: MultiAgentCollaborator | None = None
_research_agent: DeepResearchAgent | None = None
_review_agent: QualityReviewAgent | None = None


def get_collaborator() -> MultiAgentCollaborator:
    global _collaborator
    if _collaborator is None:
        _collaborator = create_multi_agent_collaborator()
    return _collaborator


def get_research_agent() -> DeepResearchAgent:
    global _research_agent
    if _research_agent is None:
        _research_agent = create_deep_research_agent()
    return _research_agent


def get_review_agent() -> QualityReviewAgent:
    global _review_agent
    if _review_agent is None:
        _review_agent = create_quality_review_agent()
    return _review_agent


# ============================================================================
# POST /collaboration/analyze
# ============================================================================

@router.post(
    "/analyze",
    response_model=CollaborationAnalyzeResponse,
    summary="Complex multi-agent analysis",
    description=(
        "Accept a complex legal question, route to the appropriate collaboration "
        "pattern, and return a comprehensive multi-agent response."
    ),
)
async def analyze_complex(request: CollaborationAnalyzeRequest) -> CollaborationAnalyzeResponse:
    """Execute multi-agent collaboration for complex legal queries."""
    try:
        collaborator = get_collaborator()
        result = await collaborator.run(
            query=request.query,
            context=request.context,
        )

        return CollaborationAnalyzeResponse(
            final_response=result.get("final_response", ""),
            intent=result.get("intent", ""),
            collaboration_pattern=result.get("collaboration_pattern", ""),
            agents_involved=result.get("agents_involved", []),
            iterations=result.get("iterations", 0),
        )
    except Exception as exc:
        logger.error("Collaboration analyze failed: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Multi-agent analysis failed: {str(exc)}",
        ) from exc


# ============================================================================
# POST /collaboration/research
# ============================================================================

@router.post(
    "/research",
    response_model=CollaborationResearchResponse,
    summary="Deep legal research",
    description=(
        "Perform comprehensive legal research combining statute search, case search, "
        "judicial interpretation search, and knowledge graph traversal."
    ),
)
async def research(request: CollaborationResearchRequest) -> CollaborationResearchResponse:
    """Execute deep legal research on a question."""
    try:
        agent = get_research_agent()
        result = await agent.research(legal_question=request.legal_question)

        return CollaborationResearchResponse(
            relevant_laws=result.get("relevant_laws", []),
            relevant_cases=result.get("relevant_cases", []),
            judicial_interpretations=result.get("judicial_interpretations", []),
            analysis=result.get("analysis", ""),
            confidence=result.get("confidence", 0.0),
            sources_cited=result.get("sources_cited", []),
        )
    except Exception as exc:
        logger.error("Deep research failed: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Deep research failed: {str(exc)}",
        ) from exc


# ============================================================================
# POST /collaboration/review
# ============================================================================

@router.post(
    "/review",
    response_model=CollaborationReviewResponse,
    summary="Quality review of any text",
    description=(
        "Review legal text for accuracy, completeness, formatting, and "
        "provide quality scoring and correction suggestions."
    ),
)
async def review(request: CollaborationReviewRequest) -> CollaborationReviewResponse:
    """Perform quality review on the provided text."""
    try:
        agent = get_review_agent()

        # Dispatch to the appropriate review method based on review_type
        if request.review_type == "legal_response":
            result = await agent.review_legal_response(
                response=request.text,
                query=request.original_query or "",
            )
        elif request.review_type == "contract_analysis":
            result = await agent.review_contract_analysis(
                analysis={"final_output": request.text},
                contract_text=request.original_query or "",
            )
        elif request.review_type == "document":
            result = await agent.review_document(
                document=request.text,
                doc_type=request.original_query or "法律文书",
            )
        else:
            # General review
            result = await agent.run({
                "input_text": request.text,
                "input_type": "general",
                "query": request.original_query or "",
            })

        return CollaborationReviewResponse(
            quality_score=result.get("quality_score", 0),
            issues=result.get("issues", []),
            suggestions=result.get("suggestions", []),
            corrected_text=result.get(
                "corrected_text",
                result.get("corrected_response",
                    result.get("corrected_analysis",
                        result.get("corrected_document", ""))),
            ),
            citation_check=result.get("citation_check", {}),
            completeness_check=result.get("completeness_check", {}),
        )
    except Exception as exc:
        logger.error("Quality review failed: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Quality review failed: {str(exc)}",
        ) from exc


# ============================================================================
# GET /collaboration/patterns
# ============================================================================

@router.get(
    "/patterns",
    response_model=CollaborationPatternsResponse,
    summary="List available collaboration patterns",
    description="Return a list of all available multi-agent collaboration patterns.",
)
async def list_patterns() -> CollaborationPatternsResponse:
    """List all available collaboration patterns."""
    patterns = []
    for pattern_id, pattern_info in COLLABORATION_PATTERNS.items():
        patterns.append(CollaborationPatternInfo(
            id=pattern_id,
            name=pattern_info.get("name", ""),
            description=pattern_info.get("description", ""),
            use_case=pattern_info.get("use_case", ""),
        ))

    return CollaborationPatternsResponse(patterns=patterns)
