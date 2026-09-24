"""Feedback schemas."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class FeedbackCreate(BaseModel):
    """Request to submit feedback on an AI message."""
    message_id: str = Field(..., description="ID of the message being rated")
    rating: int = Field(..., ge=1, le=5, description="Rating from 1 (worst) to 5 (best)")
    comment: str | None = Field(default=None, max_length=2000, description="Optional comment")
    feedback_type: str = Field(
        default="helpful",
        description="Feedback type: helpful, not_helpful, inaccurate",
    )


class FeedbackResponse(BaseModel):
    """Feedback record returned by the API."""
    model_config = ConfigDict(from_attributes=True)

    id: str
    message_id: str
    user_id: str
    rating: int
    comment: str | None = None
    feedback_type: str
    created_at: datetime


class FeedbackStatsResponse(BaseModel):
    """Aggregated feedback statistics."""
    total_feedback: int
    average_rating: float
    helpful_count: int
    not_helpful_count: int
    inaccurate_count: int
    rating_distribution: dict[int, int] = Field(
        default_factory=dict, description="Map of rating value to count"
    )
