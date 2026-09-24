"""Analytics endpoints -- data flywheel for system usage statistics.

Provides dashboard statistics, topic trends, and usage analytics
for monitoring and improving the legal AI system.
"""

from datetime import datetime, timedelta, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db, get_current_admin_user
from app.core.config import settings
from app.models.conversation import Conversation
from app.models.feedback import Feedback
from app.models.message import Message, MessageRole
from app.models.user import User
from app.models.document import Document
from pydantic import BaseModel, Field

router = APIRouter()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class DashboardStats(BaseModel):
    """System dashboard statistics."""
    total_users: int = Field(default=0, description="Total registered users")
    active_users: int = Field(default=0, description="Active users in the last 30 days")
    total_conversations: int = Field(default=0, description="Total conversations")
    total_messages: int = Field(default=0, description="Total messages exchanged")
    total_documents: int = Field(default=0, description="Total uploaded documents")
    total_reviews: int = Field(default=0, description="Total contract reviews")
    average_feedback_score: float = Field(default=0.0, description="Average feedback rating (1-5)")
    feedback_count: int = Field(default=0, description="Total feedback entries")
    daily_active_users: int = Field(default=0, description="Users active today")
    daily_conversations: int = Field(default=0, description="Conversations created today")
    daily_messages: int = Field(default=0, description="Messages sent today")


class TopicStat(BaseModel):
    """Statistics for a consultation topic."""
    topic: str = Field(default="", description="Topic name")
    count: int = Field(default=0, description="Number of consultations")
    percentage: float = Field(default=0.0, description="Percentage of total")


class TopicStatsResponse(BaseModel):
    """Response for top consultation topics."""
    topics: list[TopicStat] = Field(default_factory=list)
    total: int = Field(default=0, description="Total consultations")
    period: str = Field(default="30d", description="Analysis period")


class TrendPoint(BaseModel):
    """A single data point in a trend."""
    date: str = Field(default="", description="Date in YYYY-MM-DD format")
    conversations: int = Field(default=0, description="Conversations created")
    messages: int = Field(default=0, description="Messages sent")
    users: int = Field(default=0, description="Active users")
    feedback_avg: float = Field(default=0.0, description="Average feedback score")


class TrendResponse(BaseModel):
    """Response for usage trends over time."""
    trends: list[TrendPoint] = Field(default_factory=list)
    period: str = Field(default="30d", description="Analysis period")
    granularity: str = Field(default="day", description="Data granularity")


# ---------------------------------------------------------------------------
# GET /analytics/dashboard
# ---------------------------------------------------------------------------


@router.get("/dashboard", response_model=DashboardStats)
async def get_dashboard_stats(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> DashboardStats:
    """Get system dashboard statistics.

    Returns aggregate stats including total users, conversations,
    messages, feedback scores, and today's activity.
    """
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    thirty_days_ago = now - timedelta(days=30)

    # Total users
    total_users_result = await db.execute(
        select(func.count(User.id))
    )
    total_users = total_users_result.scalar() or 0

    # Active users (last 30 days)
    active_users_result = await db.execute(
        select(func.count(func.distinct(Conversation.user_id)))
        .where(Conversation.created_at >= thirty_days_ago)
    )
    active_users = active_users_result.scalar() or 0

    # Total conversations
    total_conversations_result = await db.execute(
        select(func.count(Conversation.id))
    )
    total_conversations = total_conversations_result.scalar() or 0

    # Total messages
    total_messages_result = await db.execute(
        select(func.count(Message.id))
    )
    total_messages = total_messages_result.scalar() or 0

    # Total documents
    total_documents_result = await db.execute(
        select(func.count(Document.id))
    )
    total_documents = total_documents_result.scalar() or 0

    # Total reviews (documents that are ready = reviewed)
    total_reviews_result = await db.execute(
        select(func.count(Document.id)).where(Document.status == "ready")
    )
    total_reviews = total_reviews_result.scalar() or 0

    # Average feedback score
    feedback_stats_result = await db.execute(
        select(
            func.avg(Feedback.rating).label("avg_rating"),
            func.count(Feedback.id).label("count"),
        )
    )
    feedback_stats = feedback_stats_result.one_or_none()
    avg_feedback = float(feedback_stats[0]) if feedback_stats and feedback_stats[0] else 0.0
    feedback_count = feedback_stats[1] if feedback_stats else 0

    # Daily active users
    daily_users_result = await db.execute(
        select(func.count(func.distinct(Conversation.user_id)))
        .where(Conversation.created_at >= today_start)
    )
    daily_active_users = daily_users_result.scalar() or 0

    # Daily conversations
    daily_conv_result = await db.execute(
        select(func.count(Conversation.id))
        .where(Conversation.created_at >= today_start)
    )
    daily_conversations = daily_conv_result.scalar() or 0

    # Daily messages
    daily_msg_result = await db.execute(
        select(func.count(Message.id))
        .where(Message.created_at >= today_start)
    )
    daily_messages = daily_msg_result.scalar() or 0

    return DashboardStats(
        total_users=total_users,
        active_users=active_users,
        total_conversations=total_conversations,
        total_messages=total_messages,
        total_documents=total_documents,
        total_reviews=total_reviews,
        average_feedback_score=round(avg_feedback, 2),
        feedback_count=feedback_count,
        daily_active_users=daily_active_users,
        daily_conversations=daily_conversations,
        daily_messages=daily_messages,
    )


# ---------------------------------------------------------------------------
# GET /analytics/topics
# ---------------------------------------------------------------------------


@router.get("/topics", response_model=TopicStatsResponse)
async def get_top_topics(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    period: str = Query(default="30d", description="Analysis period: 7d, 30d, 90d, all"),
    limit: int = Query(default=10, ge=1, le=50, description="Number of top topics"),
) -> TopicStatsResponse:
    """Get top consultation topics based on conversation agent_type distribution.

    Analyzes the types of legal consultations over the specified period.
    """
    now = datetime.now(timezone.utc)

    # Parse period
    period_days = {
        "7d": 7,
        "30d": 30,
        "90d": 90,
        "all": None,
    }.get(period, 30)

    # Build query
    if period_days is not None:
        start_date = now - timedelta(days=period_days)
        topics_result = await db.execute(
            select(
                Conversation.agent_type,
                func.count(Conversation.id).label("count"),
            )
            .where(Conversation.created_at >= start_date)
            .group_by(Conversation.agent_type)
            .order_by(func.count(Conversation.id).desc())
            .limit(limit)
        )
    else:
        topics_result = await db.execute(
            select(
                Conversation.agent_type,
                func.count(Conversation.id).label("count"),
            )
            .group_by(Conversation.agent_type)
            .order_by(func.count(Conversation.id).desc())
            .limit(limit)
        )

    topics_data = topics_result.all()

    # Topic name mapping
    topic_name_map: dict[str, str] = {
        "legal_consultation": "法律咨询",
        "contract_review": "合同审查",
        "document_generation": "文书生成",
        "law_retrieval": "法律检索",
        "general": "一般问题",
    }

    total = sum(row[1] for row in topics_data)

    topics: list[TopicStat] = []
    for row in topics_data:
        agent_type = row[0]
        count = row[1]
        topics.append(
            TopicStat(
                topic=topic_name_map.get(agent_type, agent_type),
                count=count,
                percentage=round((count / total * 100) if total > 0 else 0, 1),
            )
        )

    return TopicStatsResponse(
        topics=topics,
        total=total,
        period=period,
    )


# ---------------------------------------------------------------------------
# GET /analytics/trends
# ---------------------------------------------------------------------------


@router.get("/trends", response_model=TrendResponse)
async def get_usage_trends(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
    period: str = Query(default="30d", description="Analysis period: 7d, 30d, 90d"),
    granularity: str = Query(default="day", description="Granularity: day, week"),
) -> TrendResponse:
    """Get usage trends over time using efficient GROUP BY queries.

    Replaces the old O(N*4) loop with 3 efficient GROUP BY queries.
    """
    now = datetime.now(timezone.utc)
    period_days = {"7d": 7, "30d": 30, "90d": 90}.get(period, 30)
    start_date = now - timedelta(days=period_days)

    # Use date_trunc for grouping (works on both PostgreSQL and SQLite)
    if settings.DATABASE_URL.startswith("postgresql"):
        date_trunc_conv = func.date_trunc("day", Conversation.created_at)
        date_trunc_msg = func.date_trunc("day", Message.created_at)
        date_trunc_fb = func.date_trunc("day", Feedback.created_at)
    else:
        # SQLite fallback: use date() function
        date_trunc_conv = func.date(Conversation.created_at)
        date_trunc_msg = func.date(Message.created_at)
        date_trunc_fb = func.date(Feedback.created_at)

    # Query 1: Daily conversations + unique users
    conv_result = await db.execute(
        select(date_trunc_conv.label("day"),
               func.count(Conversation.id).label("conv_count"),
               func.count(func.distinct(Conversation.user_id)).label("user_count"))
        .where(Conversation.created_at >= start_date)
        .group_by(date_trunc_conv)
    )
    conv_by_day: dict[str, tuple[int, int]] = {}
    for row in conv_result.all():
        day_str = str(row[0])[:10]
        conv_by_day[day_str] = (row[1], row[2])

    # Query 2: Daily messages
    msg_result = await db.execute(
        select(date_trunc_msg.label("day"), func.count(Message.id).label("msg_count"))
        .where(Message.created_at >= start_date)
        .group_by(date_trunc_msg)
    )
    msg_by_day: dict[str, int] = {str(row[0])[:10]: row[1] for row in msg_result.all()}

    # Query 3: Daily average feedback
    fb_result = await db.execute(
        select(date_trunc_fb.label("day"), func.avg(Feedback.rating).label("avg_rating"))
        .where(Feedback.created_at >= start_date)
        .group_by(date_trunc_fb)
    )
    fb_by_day: dict[str, float] = {str(row[0])[:10]: float(row[1]) for row in fb_result.all()}

    # Build trend points for each day
    trends: list[TrendPoint] = []
    for i in range(period_days):
        day = start_date + timedelta(days=i)
        day_str = day.strftime("%Y-%m-%d")
        conv_count, user_count = conv_by_day.get(day_str, (0, 0))
        msg_count = msg_by_day.get(day_str, 0)
        fb_avg = fb_by_day.get(day_str, 0.0)

        trends.append(TrendPoint(
            date=day_str,
            conversations=conv_count,
            messages=msg_count,
            users=user_count,
            feedback_avg=round(fb_avg, 2),
        ))

    return TrendResponse(trends=trends, period=period, granularity=granularity)


# ---------------------------------------------------------------------------
# GET /analytics/feedback-summary
# ---------------------------------------------------------------------------


@router.get("/feedback-summary")
async def get_feedback_summary(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """Get feedback rating distribution summary."""
    rating_dist = await db.execute(
        select(
            Feedback.rating,
            func.count(Feedback.id).label("count"),
        )
        .group_by(Feedback.rating)
        .order_by(Feedback.rating)
    )
    ratings = rating_dist.all()

    distribution: dict[int, int] = {i: 0 for i in range(1, 6)}
    for row in ratings:
        rating_val = int(row[0])
        distribution[rating_val] = row[1]

    # Average rating
    avg_result = await db.execute(
        select(func.avg(Feedback.rating))
    )
    avg_rating = avg_result.scalar()

    return {
        "distribution": distribution,
        "average_rating": round(float(avg_rating) if avg_rating else 0.0, 2),
        "total_feedback": sum(distribution.values()),
    }