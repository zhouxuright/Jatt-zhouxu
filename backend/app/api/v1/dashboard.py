"""Dashboard endpoints -- aggregated stats, recent activity, and system status.

Provides the data layer for the frontend Dashboard view, including:
- GET /dashboard/stats: aggregate counts across all legal tables + user stats
- GET /dashboard/recent-activity: recent conversations and contract reviews
- GET /dashboard/system-status: knowledge base health and Milvus status
"""

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.models.compliance import ComplianceAlert
from app.models.conversation import Conversation
from app.models.document import ContractReview, Document
from app.models.feedback import Feedback
from app.models.legal_knowledge import (
    CourtCase,
    Law,
    LegalArticle,
    LegalQAPair,
)
from app.models.message import Message
from app.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class DashboardStatResponse(BaseModel):
    """Combined dashboard statistics."""
    # Knowledge base
    total_laws: int = Field(default=0, description="Total laws in knowledge base")
    total_articles: int = Field(default=0, description="Total legal articles")
    total_court_cases: int = Field(default=0, description="Total court cases")
    total_qa_pairs: int = Field(default=0, description="Total QA pairs")
    knowledge_total: int = Field(default=0, description="Sum of all knowledge records")
    # User activity
    total_conversations: int = Field(default=0, description="Total conversations")
    total_messages: int = Field(default=0, description="Total messages")
    total_reviews: int = Field(default=0, description="Total contract reviews")
    total_documents: int = Field(default=0, description="Total uploaded documents")
    feedback_count: int = Field(default=0, description="Total feedback entries")
    average_feedback_score: float = Field(default=0.0, description="Average feedback score")
    # Today
    daily_conversations: int = Field(default=0, description="Conversations today")
    daily_messages: int = Field(default=0, description="Messages today")


class ConversationItem(BaseModel):
    """A recent conversation summary."""
    id: str
    title: str
    agent_type: str = ""
    created_at: str = ""
    updated_at: str = ""


class ReviewItem(BaseModel):
    """A recent contract review summary."""
    id: str
    original_filename: str = ""
    risk_score: float | None = None
    summary: str | None = None
    created_at: str = ""


class RecentActivityResponse(BaseModel):
    """Recent activity for the current user."""
    conversations: list[ConversationItem] = Field(default_factory=list)
    reviews: list[ReviewItem] = Field(default_factory=list)


class MilvusStatus(BaseModel):
    """Milvus vector database status."""
    available: bool = Field(default=False, description="Whether Milvus is reachable")
    collection_stats: dict[str, Any] = Field(
        default_factory=dict,
        description="Per-collection document counts",
    )
    error: str | None = Field(default=None, description="Error message if unavailable")


class SystemStatusResponse(BaseModel):
    """System health and knowledge base status."""
    knowledge_base: dict[str, int] = Field(default_factory=dict, description="Table counts")
    milvus: MilvusStatus = Field(default_factory=MilvusStatus)
    open_alerts: int = Field(default=0, description="Open compliance alerts for current user")
    database_healthy: bool = Field(default=True)


# ---------------------------------------------------------------------------
# GET /dashboard/stats
# ---------------------------------------------------------------------------


@router.get("/stats", response_model=DashboardStatResponse)
async def get_dashboard_stats(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> DashboardStatResponse:
    """Aggregate counts from all legal tables and user activity stats.

    Runs lightweight COUNT queries in parallel via asyncio.gather for
    acceptable response times even with large tables.
    """
    import asyncio
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    async def _count(stmt) -> int:
        result = await db.execute(stmt)
        return result.scalar() or 0

    # Run all COUNT queries in parallel instead of sequentially
    (
        total_laws,
        total_articles,
        total_court_cases,
        total_qa_pairs,
        total_conversations,
        total_messages,
        total_reviews,
        total_documents,
        daily_conversations,
        daily_messages,
    ) = await asyncio.gather(
        _count(select(func.count(Law.id))),
        _count(select(func.count(LegalArticle.id))),
        _count(select(func.count(CourtCase.id))),
        _count(select(func.count(LegalQAPair.id))),
        _count(select(func.count(Conversation.id))),
        _count(select(func.count(Message.id))),
        _count(select(func.count(ContractReview.id))),
        _count(select(func.count(Document.id))),
        _count(select(func.count(Conversation.id)).where(Conversation.created_at >= today_start)),
        _count(select(func.count(Message.id)).where(Message.created_at >= today_start)),
    )
    knowledge_total = total_laws + total_articles + total_court_cases + total_qa_pairs

    # Feedback (single query with both count and avg)
    feedback_result = await db.execute(
        select(
            func.count(Feedback.id).label("count"),
            func.avg(Feedback.rating).label("avg"),
        )
    )
    fb_row = feedback_result.one_or_none()
    feedback_count = fb_row[0] if fb_row else 0
    avg_feedback = float(fb_row[1]) if fb_row and fb_row[1] else 0.0

    return DashboardStatResponse(
        total_laws=total_laws,
        total_articles=total_articles,
        total_court_cases=total_court_cases,
        total_qa_pairs=total_qa_pairs,
        knowledge_total=knowledge_total,
        total_conversations=total_conversations,
        total_messages=total_messages,
        total_reviews=total_reviews,
        total_documents=total_documents,
        feedback_count=feedback_count,
        average_feedback_score=round(avg_feedback, 2),
        daily_conversations=daily_conversations,
        daily_messages=daily_messages,
    )


# ---------------------------------------------------------------------------
# GET /dashboard/recent-activity
# ---------------------------------------------------------------------------


@router.get("/recent-activity", response_model=RecentActivityResponse)
async def get_recent_activity(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> RecentActivityResponse:
    """Recent conversations and contract reviews for the current user.

    Returns the 5 most recent of each, ordered by creation time descending.
    """
    # Recent conversations
    conv_result = await db.execute(
        select(Conversation)
        .where(Conversation.user_id == current_user.id)
        .order_by(Conversation.created_at.desc())
        .limit(5)
    )
    conversations = [
        ConversationItem(
            id=c.id,
            title=c.title or "新对话",
            agent_type=c.agent_type or "",
            created_at=c.created_at.isoformat() if c.created_at else "",
            updated_at=c.updated_at.isoformat() if c.updated_at else "",
        )
        for c in conv_result.scalars().all()
    ]

    # Recent contract reviews
    review_result = await db.execute(
        select(ContractReview)
        .where(ContractReview.user_id == current_user.id)
        .order_by(ContractReview.created_at.desc())
        .limit(5)
    )
    reviews = [
        ReviewItem(
            id=r.id,
            original_filename=r.original_filename or "",
            risk_score=r.risk_score,
            summary=r.summary,
            created_at=r.created_at.isoformat() if r.created_at else "",
        )
        for r in review_result.scalars().all()
    ]

    return RecentActivityResponse(
        conversations=conversations,
        reviews=reviews,
    )


# ---------------------------------------------------------------------------
# GET /dashboard/system-status
# ---------------------------------------------------------------------------


@router.get("/system-status", response_model=SystemStatusResponse)
async def get_system_status(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SystemStatusResponse:
    """Knowledge base health and system status.

    Returns table counts for all legal knowledge tables, Milvus vector DB
    status, and the count of open compliance alerts for the current user.
    """
    # Knowledge base table counts
    knowledge_base: dict[str, int] = {}
    for model_cls, label in [
        (Law, "laws"),
        (LegalArticle, "legal_articles"),
        (CourtCase, "court_cases"),
        (LegalQAPair, "legal_qa_pairs"),
    ]:
        result = await db.execute(select(func.count(model_cls.id)))
        knowledge_base[label] = result.scalar() or 0

    # Milvus status (best-effort; never fail the endpoint if Milvus is down)
    milvus_status = MilvusStatus(available=False)
    try:
        from app.rag.milvus_service import get_milvus_rag_service
        milvus = get_milvus_rag_service()
        # Attempt a lightweight connectivity check
        collection_stats: dict[str, Any] = {}
        if hasattr(milvus, 'collection_name'):
            # Try to get collection info
            try:
                from pymilvus import utility
                collection_name = milvus.collection_name
                if utility.has_collection(collection_name):
                    stats = utility.get_collection_stats(collection_name)
                    collection_stats[collection_name] = {
                        "row_count": int(stats.get("row_count", 0)) if stats else 0,
                    }
                milvus_status = MilvusStatus(
                    available=True,
                    collection_stats=collection_stats,
                )
            except Exception as milvus_err:
                logger.debug("Milvus status check failed: %s", milvus_err)
                milvus_status = MilvusStatus(
                    available=False,
                    error=str(milvus_err),
                )
        else:
            milvus_status = MilvusStatus(available=True, collection_stats={})
    except ImportError:
        milvus_status = MilvusStatus(
            available=False,
            error="pymilvus not installed",
        )
    except Exception as e:
        logger.debug("Milvus check unexpected error: %s", e)
        milvus_status = MilvusStatus(available=False, error=str(e))

    # Open compliance alerts for current user
    alert_result = await db.execute(
        select(func.count(ComplianceAlert.id))
        .where(
            ComplianceAlert.user_id == current_user.id,
            ComplianceAlert.status == "open",
        )
    )
    open_alerts = alert_result.scalar() or 0

    # Database health
    db_healthy = True
    try:
        await db.execute(text("SELECT 1"))
    except Exception:
        db_healthy = False

    return SystemStatusResponse(
        knowledge_base=knowledge_base,
        milvus=milvus_status,
        open_alerts=open_alerts,
        database_healthy=db_healthy,
    )
