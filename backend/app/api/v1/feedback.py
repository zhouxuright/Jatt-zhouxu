"""Feedback endpoints -- submit and view feedback on AI responses."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.models.feedback import Feedback
from app.models.message import Message
from app.models.user import User
from app.schemas.feedback import (
    FeedbackCreate,
    FeedbackResponse,
    FeedbackStatsResponse,
)

router = APIRouter()


@router.post("/feedback", response_model=FeedbackResponse, status_code=status.HTTP_201_CREATED)
async def submit_feedback(
    payload: FeedbackCreate,
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> Feedback:
    """Submit feedback (rating and optional comment) for an AI message."""
    # Verify the message exists if message_id is provided
    if payload.message_id:
        result = await db.execute(select(Message).where(Message.id == payload.message_id))
        message = result.scalar_one_or_none()
        if message is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Message not found",
            )

    # Check for duplicate feedback from the same user on the same message
    if payload.message_id:
        existing = await db.execute(
            select(Feedback).where(
                Feedback.message_id == payload.message_id,
                Feedback.user_id == current_user.id,
            )
        )
        if existing.scalar_one_or_none() is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="You have already submitted feedback for this message",
            )

    feedback = Feedback(
        message_id=payload.message_id,
        user_id=current_user.id,
        rating=payload.rating,
        comment=payload.comment,
        feedback_type=payload.feedback_type,
    )
    db.add(feedback)
    await db.flush()
    await db.refresh(feedback)
    return feedback


@router.get("/feedback/stats", response_model=FeedbackStatsResponse)
async def get_feedback_stats(
    current_user: Annotated[User, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> FeedbackStatsResponse:
    """Get aggregated feedback statistics for the current user."""
    # Total and average
    total_result = await db.execute(
        select(
            func.count(Feedback.id).label("total"),
            func.avg(Feedback.rating).label("avg_rating"),
        ).where(Feedback.user_id == current_user.id)
    )
    row = total_result.one_or_none()
    total = int(row.total) if row and row.total is not None else 0
    avg_rating = float(row.avg_rating) if row and row.avg_rating is not None else 0.0

    # Count by type
    helpful_result = await db.execute(
        select(func.count(Feedback.id)).where(
            Feedback.user_id == current_user.id,
            Feedback.feedback_type == "helpful",
        )
    )
    helpful_count = helpful_result.scalar() or 0

    not_helpful_result = await db.execute(
        select(func.count(Feedback.id)).where(
            Feedback.user_id == current_user.id,
            Feedback.feedback_type == "not_helpful",
        )
    )
    not_helpful_count = not_helpful_result.scalar() or 0

    inaccurate_result = await db.execute(
        select(func.count(Feedback.id)).where(
            Feedback.user_id == current_user.id,
            Feedback.feedback_type == "inaccurate",
        )
    )
    inaccurate_count = inaccurate_result.scalar() or 0

    # Rating distribution
    dist_result = await db.execute(
        select(Feedback.rating, func.count(Feedback.id))
        .where(Feedback.user_id == current_user.id)
        .group_by(Feedback.rating)
        .order_by(Feedback.rating)
    )
    rating_distribution = {
        int(rating): int(count) for rating, count in dist_result.all()
    }

    return FeedbackStatsResponse(
        total_feedback=total,
        average_rating=round(avg_rating, 2),
        helpful_count=helpful_count,
        not_helpful_count=not_helpful_count,
        inaccurate_count=inaccurate_count,
        rating_distribution=rating_distribution,
    )