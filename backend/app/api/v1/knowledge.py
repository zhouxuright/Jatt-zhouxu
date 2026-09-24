"""Knowledge graph API endpoints -- graph building, querying, and visualization."""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import get_current_user
from app.models.user import User
from app.schemas.knowledge import (
    BuildGraphResponse,
    CitationChainResponse,
    GraphData,
    GraphStatsResponse,
    LawFamilyResponse,
    RelatedArticlesResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Singleton knowledge graph builder
# ---------------------------------------------------------------------------

_graph_builder = None


def _get_graph_builder():
    """Return the singleton LegalKnowledgeGraphBuilder instance."""
    global _graph_builder
    if _graph_builder is None:
        from app.core.database import async_session_factory
        from app.rag.legal_knowledge_graph import LegalKnowledgeGraphBuilder
        _graph_builder = LegalKnowledgeGraphBuilder(async_session_factory)
    return _graph_builder


# ---------------------------------------------------------------------------
# POST /knowledge/graph -- Build/rebuild the knowledge graph
# ---------------------------------------------------------------------------


@router.post("/graph", response_model=BuildGraphResponse)
async def build_knowledge_graph(
    current_user: Annotated[User, Depends(get_current_user)],
) -> BuildGraphResponse:
    """Build or rebuild the knowledge graph from the PostgreSQL database.

    Extracts entities (laws, articles, cases, concepts) and relationships
    (CONTAINS, REFERENCES, CITES, RELATED_TO, AMENDS) from the legal database.
    """
    try:
        builder = _get_graph_builder()
        stats = await builder.build_from_database()
        return BuildGraphResponse(
            status="success",
            nodes=stats["nodes"],
            edges=stats["edges"],
            laws=stats["laws"],
            articles=stats["articles"],
            cases=stats["cases"],
            concepts=stats["concepts"],
            cross_references=stats["cross_references"],
            amendments=stats["amendments"],
        )
    except Exception as exc:
        logger.exception("Failed to build knowledge graph")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Knowledge graph build failed: {str(exc)}",
        )


# ---------------------------------------------------------------------------
# GET /knowledge/related/{article_id} -- Get related articles
# ---------------------------------------------------------------------------


@router.get("/related/{article_id}")
async def get_related_articles(
    article_id: str,
    current_user: Annotated[User, Depends(get_current_user)],
    depth: int = Query(default=2, ge=1, le=4, description="Traversal depth"),
) -> RelatedArticlesResponse:
    """Get related articles through knowledge graph traversal.

    Finds articles connected via cross-references, shared concepts,
    and other graph relationships.
    """
    try:
        builder = _get_graph_builder()
        related = builder.find_related_articles(article_id, depth=depth)

        return RelatedArticlesResponse(
            article_id=article_id,
            primary_articles=[],
            related_articles=related,
            total_related=len(related),
        )
    except Exception as exc:
        logger.exception("Failed to find related articles")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to find related articles: {str(exc)}",
        )


# ---------------------------------------------------------------------------
# GET /knowledge/law-family/{law_name} -- Get law family
# ---------------------------------------------------------------------------


@router.get("/law-family/{law_name:path}")
async def get_law_family(
    law_name: str,
    current_user: Annotated[User, Depends(get_current_user)],
) -> LawFamilyResponse:
    """Get all related laws: amendments, parent laws, and child regulations.

    The law_name path parameter accepts the full law name including
    Chinese characters, e.g., '中华人民共和国劳动合同法'.
    """
    try:
        builder = _get_graph_builder()
        family = builder.get_law_family(law_name)

        if not family and not builder._law_index.get(law_name):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Law not found in knowledge graph: {law_name}",
            )

        amendments = [f for f in family if f["relationship"] == "AMENDS"]
        parent_laws = [f for f in family if "reverse" in f["relationship"] and "AMENDS" in f["relationship"]]
        child_regulations = [f for f in family if f["relationship"] not in ("AMENDS",) and "reverse" not in f["relationship"]]

        from app.schemas.knowledge import LawFamilyMember
        return LawFamilyResponse(
            law_name=law_name,
            amendments=[LawFamilyMember(**a) for a in amendments],
            parent_laws=[LawFamilyMember(**p) for p in parent_laws],
            child_regulations=[LawFamilyMember(**c) for c in child_regulations],
            all_related=[LawFamilyMember(**f) for f in family],
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to get law family")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get law family: {str(exc)}",
        )


# ---------------------------------------------------------------------------
# GET /knowledge/citation-chain -- Get citation chain between laws
# ---------------------------------------------------------------------------


@router.get("/citation-chain", response_model=CitationChainResponse)
async def get_citation_chain(
    current_user: Annotated[User, Depends(get_current_user)],
    from_law: str = Query(..., description="Starting law name"),
    to_law: str = Query(..., description="Target law name"),
) -> CitationChainResponse:
    """Find the citation chain between two laws.

    Uses BFS graph traversal to find a path connecting the two laws
    through article cross-references and citations.
    """
    try:
        builder = _get_graph_builder()
        chain = builder.find_citation_chain(from_law, to_law)

        return CitationChainResponse(
            from_law=from_law,
            to_law=to_law,
            chain=chain,
            chain_length=len(chain),
            path_found=len(chain) > 0,
        )
    except Exception as exc:
        logger.exception("Failed to find citation chain")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to find citation chain: {str(exc)}",
        )


# ---------------------------------------------------------------------------
# GET /knowledge/export -- Export graph data for visualization
# ---------------------------------------------------------------------------


@router.get("/export", response_model=GraphData)
async def export_graph(
    current_user: Annotated[User, Depends(get_current_user)],
) -> GraphData:
    """Export the full knowledge graph as JSON for frontend visualization.

    Returns all nodes and edges suitable for rendering with D3.js,
    vis.js, or similar graph visualization libraries.
    """
    try:
        builder = _get_graph_builder()
        data = builder.export_graph_data()

        return GraphData(
            nodes=data["nodes"],
            edges=data["edges"],
        )
    except Exception as exc:
        logger.exception("Failed to export graph")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to export graph: {str(exc)}",
        )


# ---------------------------------------------------------------------------
# GET /knowledge/stats -- Graph statistics
# ---------------------------------------------------------------------------


@router.get("/stats", response_model=GraphStatsResponse)
async def get_graph_stats(
    current_user: Annotated[User, Depends(get_current_user)],
) -> GraphStatsResponse:
    """Get statistics about the knowledge graph.

    Returns node counts by type, edge counts by relation type,
    and total counts.
    """
    try:
        builder = _get_graph_builder()
        stats = builder.get_stats()

        return GraphStatsResponse(**stats)
    except Exception as exc:
        logger.exception("Failed to get graph stats")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get graph stats: {str(exc)}",
        )
