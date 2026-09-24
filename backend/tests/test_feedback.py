"""Tests for feedback endpoints."""

import pytest
from httpx import AsyncClient


# ---------------------------------------------------------------------------
# POST /feedback/feedback
# ---------------------------------------------------------------------------


class TestFeedbackSubmission:
    """Tests for POST /api/v1/feedback/feedback."""

    async def test_feedback_without_auth_returns_401(self, test_client: AsyncClient):
        """Submitting feedback without authentication should return 401."""
        payload = {
            "message_id": "00000000-0000-0000-0000-000000000000",
            "rating": 5,
            "comment": "Great answer!",
            "feedback_type": "helpful",
        }
        resp = await test_client.post("/api/v1/feedback/feedback", json=payload)
        assert resp.status_code == 401

    async def test_feedback_submission_with_message(
        self, test_client: AsyncClient, auth_headers, async_session
    ):
        """Submitting feedback with a valid message_id should succeed."""
        from app.models.conversation import Conversation
        from app.models.message import Message
        from app.models.user import User
        from sqlalchemy import select

        # Get the current user from the auth headers
        # We need to create a conversation and message for the sample_user
        result = await async_session.execute(select(User).where(User.username == "testuser"))
        user = result.scalar_one()

        # Create a conversation
        conversation = Conversation(
            user_id=user.id,
            title="Test Conversation",
            agent_type="legal_consultation",
        )
        async_session.add(conversation)
        await async_session.flush()

        # Create a message
        message = Message(
            conversation_id=conversation.id,
            role="assistant",
            content="这是一条测试回复。",
        )
        async_session.add(message)
        await async_session.flush()

        payload = {
            "message_id": message.id,
            "rating": 5,
            "comment": "非常有帮助的回答",
            "feedback_type": "helpful",
        }
        resp = await test_client.post(
            "/api/v1/feedback/feedback", json=payload, headers=auth_headers
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["rating"] == 5
        assert data["feedback_type"] == "helpful"
        assert data["message_id"] == message.id
        assert data["user_id"] == user.id


# ---------------------------------------------------------------------------
# GET /feedback/feedback/stats
# ---------------------------------------------------------------------------


class TestFeedbackStats:
    """Tests for GET /api/v1/feedback/feedback/stats."""

    async def test_feedback_stats_empty(self, test_client: AsyncClient, auth_headers):
        """Feedback stats for a user with no feedback should show zeros."""
        resp = await test_client.get(
            "/api/v1/feedback/feedback/stats", headers=auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_feedback"] == 0
        assert data["average_rating"] == 0.0
        assert data["helpful_count"] == 0
        assert data["not_helpful_count"] == 0
        assert data["inaccurate_count"] == 0
        assert data["rating_distribution"] == {}
