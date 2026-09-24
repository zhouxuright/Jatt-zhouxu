"""Feedback loop service for collecting, analyzing, and reporting user feedback.

Tracks satisfaction, accuracy, and response metrics, generates improvement
suggestions, and provides KPI statistics for the legal AI system.
"""

import logging
from collections import Counter
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.models.feedback import Feedback
from app.models.message import Message

logger = logging.getLogger(__name__)


class FeedbackLoopService:
    """Manages the feedback loop for continuous system improvement.

    Collects user feedback, analyzes patterns, generates improvement
    suggestions, and tracks key performance indicators.

    Attributes:
        _db_session: Async database session for queries.
    """

    # Feedback categories for automatic classification
    FEEDBACK_CATEGORIES = [
        "accuracy",       # Legal accuracy of the response
        "completeness",   # Whether the response covers all aspects
        "clarity",        # How clear and understandable the response is
        "relevance",      # How relevant to the asked question
        "citation",       # Quality of legal citations
        "speed",          # Response time satisfaction
        "other",          # Uncategorized feedback
    ]

    def __init__(self) -> None:
        """Initialize the feedback loop service."""
        self._feedback_cache: list[dict[str, Any]] = []
        self._last_analysis: datetime | None = None

    async def collect_feedback(
        self,
        db: AsyncSession,
        message_id: str,
        user_id: str,
        rating: int,
        comment: str | None = None,
        feedback_type: str = "general",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Collect and store user feedback for an AI response.

        Args:
            db: Database session.
            message_id: ID of the message being rated.
            user_id: ID of the user providing feedback.
            rating: Rating from 1 to 5.
            comment: Optional text comment.
            feedback_type: Category of feedback (e.g., "helpful", "inaccurate").
            metadata: Additional structured data about the feedback.

        Returns:
            Dict with the created feedback record.
        """
        import uuid

        if rating < 1 or rating > 5:
            raise ValueError(f"Rating must be between 1 and 5, got {rating}")

        # Verify the message exists
        result = await db.execute(
            select(Message).where(Message.id == message_id)
        )
        message = result.scalar_one_or_none()
        if message is None:
            raise ValueError(f"Message not found: {message_id}")

        feedback = Feedback(
            id=uuid.uuid4(),
            message_id=uuid.UUID(message_id),
            user_id=uuid.UUID(user_id),
            rating=rating,
            comment=comment,
            feedback_type=feedback_type,
        )

        db.add(feedback)
        await db.flush()
        await db.refresh(feedback)

        logger.info(
            "Feedback collected: message=%s rating=%d type=%s",
            message_id, rating, feedback_type,
        )

        return {
            "id": str(feedback.id),
            "message_id": str(feedback.message_id),
            "user_id": str(feedback.user_id),
            "rating": feedback.rating,
            "comment": feedback.comment,
            "feedback_type": feedback.feedback_type,
            "created_at": feedback.created_at.isoformat() if feedback.created_at else None,
        }

    async def analyze_feedback(
        self,
        db: AsyncSession,
        days: int = 30,
        limit: int = 1000,
    ) -> dict[str, Any]:
        """Analyze feedback patterns over a time period.

        Args:
            db: Database session.
            days: Number of days to analyze.
            limit: Maximum number of feedback records to analyze.

        Returns:
            Dict with analysis results including distributions, trends, and clusters.
        """
        since = datetime.utcnow() - timedelta(days=days)

        # Get feedback records
        result = await db.execute(
            select(Feedback)
            .where(Feedback.created_at >= since)
            .order_by(Feedback.created_at.desc())
            .limit(limit)
        )
        feedbacks = result.scalars().all()

        if not feedbacks:
            return {
                "period_days": days,
                "total_feedback": 0,
                "message": "No feedback data available for this period.",
            }

        # Calculate rating distribution
        rating_dist = Counter(f.rating for f in feedbacks)

        # Calculate feedback type distribution
        type_dist = Counter(f.feedback_type for f in feedbacks)

        # Calculate average rating
        avg_rating = sum(f.rating for f in feedbacks) / len(feedbacks)

        # Calculate satisfaction rate (ratings >= 4)
        satisfied = sum(1 for f in feedbacks if f.rating >= 4)
        satisfaction_rate = satisfied / len(feedbacks) * 100

        # Calculate accuracy rate (feedback_type != "inaccurate")
        accurate = sum(1 for f in feedbacks if f.feedback_type != "inaccurate")
        accuracy_rate = accurate / len(feedbacks) * 100

        # Analyze comments for common themes
        comment_analysis = await self._analyze_comments(
            [f.comment for f in feedbacks if f.comment]
        )

        # Analyze trend over time (daily buckets)
        daily_trend: dict[str, dict[str, Any]] = {}
        for f in feedbacks:
            if f.created_at:
                day_key = f.created_at.strftime("%Y-%m-%d")
                if day_key not in daily_trend:
                    daily_trend[day_key] = {"count": 0, "rating_sum": 0}
                daily_trend[day_key]["count"] += 1
                daily_trend[day_key]["rating_sum"] += f.rating

        trend_data = [
            {
                "date": day,
                "count": data["count"],
                "avg_rating": round(data["rating_sum"] / data["count"], 2),
            }
            for day, data in sorted(daily_trend.items())
        ]

        self._last_analysis = datetime.utcnow()

        return {
            "period_days": days,
            "total_feedback": len(feedbacks),
            "average_rating": round(avg_rating, 2),
            "satisfaction_rate": round(satisfaction_rate, 1),
            "accuracy_rate": round(accuracy_rate, 1),
            "rating_distribution": {
                str(k): v for k, v in sorted(rating_dist.items())
            },
            "feedback_type_distribution": dict(type_dist),
            "daily_trend": trend_data,
            "comment_themes": comment_analysis,
            "analyzed_at": self._last_analysis.isoformat(),
        }

    async def _analyze_comments(
        self,
        comments: list[str],
    ) -> dict[str, Any]:
        """Analyze comment text to identify common themes.

        Uses simple keyword-based clustering for Chinese legal feedback.

        Args:
            comments: List of comment strings.

        Returns:
            Dict with theme clusters and keyword statistics.
        """
        if not comments:
            return {"themes": [], "total_comments": 0}

        # Define keyword sets for different themes
        theme_keywords = {
            "accuracy": [
                "错误", "不对", "不准确", "有误", "错误", "不正确",
                "wrong", "incorrect", "inaccurate", "error",
            ],
            "completeness": [
                "不够详细", "不完整", "缺少", "补充", "更详细",
                "incomplete", "missing", "more detail",
            ],
            "clarity": [
                "不清楚", "难懂", "模糊", "不明白", "困惑",
                "unclear", "confusing", "vague",
            ],
            "citation": [
                "法条", "引用", "出处", "依据", "条文",
                "citation", "reference", "source",
            ],
            "usefulness": [
                "有用", "帮助", "很好", "不错", "感谢",
                "useful", "helpful", "great", "thank",
            ],
        }

        theme_counts: dict[str, int] = {theme: 0 for theme in theme_keywords}
        uncategorized = 0

        for comment in comments:
            comment_lower = comment.lower()
            matched = False
            for theme, keywords in theme_keywords.items():
                if any(kw in comment_lower for kw in keywords):
                    theme_counts[theme] += 1
                    matched = True
                    break
            if not matched:
                uncategorized += 1

        themes = [
            {"theme": theme, "count": count, "percentage": round(count / len(comments) * 100, 1)}
            for theme, count in sorted(theme_counts.items(), key=lambda x: x[1], reverse=True)
            if count > 0
        ]

        return {
            "themes": themes,
            "total_comments": len(comments),
            "uncategorized": uncategorized,
        }

    async def generate_report(
        self,
        db: AsyncSession,
        days: int = 30,
    ) -> dict[str, Any]:
        """Generate a comprehensive feedback analysis report.

        Args:
            db: Database session.
            days: Number of days to cover in the report.

        Returns:
            Dict with full report including analysis, suggestions, and KPIs.
        """
        analysis = await self.analyze_feedback(db, days=days)
        suggestions = await self.generate_suggestions(analysis)
        kpis = await self.get_kpi_stats(db, days=days)

        return {
            "report_title": f"法律AI系统用户反馈分析报告 ({days}天)",
            "generated_at": datetime.utcnow().isoformat(),
            "period": f"{days} days",
            "analysis": analysis,
            "improvement_suggestions": suggestions,
            "kpi_stats": kpis,
        }

    async def generate_suggestions(
        self,
        analysis: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Generate improvement suggestions based on feedback analysis.

        Args:
            analysis: Feedback analysis result from analyze_feedback().

        Returns:
            List of suggestion dicts with priority, category, and action items.
        """
        suggestions: list[dict[str, Any]] = []

        avg_rating = analysis.get("average_rating", 0)
        satisfaction = analysis.get("satisfaction_rate", 0)
        accuracy = analysis.get("accuracy_rate", 0)

        # Low satisfaction
        if satisfaction < 80:
            suggestions.append({
                "priority": "high",
                "category": "quality",
                "title": "提升回复满意度",
                "description": f"当前满意度为 {satisfaction}%，需提升至80%以上",
                "actions": [
                    "加强法律知识库建设，确保回复准确性",
                    "优化回复模板，提高回复的完整性和清晰度",
                    "针对低分反馈进行专项分析",
                ],
            })

        # Low accuracy
        if accuracy < 85:
            suggestions.append({
                "priority": "high",
                "category": "accuracy",
                "title": "提升法律准确性",
                "description": f"当前准确率为 {accuracy}%，需提升至85%以上",
                "actions": [
                    "增加法律条文引用验证机制",
                    "引入法律专家审核流程",
                    "扩充法律案例数据库",
                ],
            })

        # Check comment themes
        comment_themes = analysis.get("comment_themes", {}).get("themes", [])
        for theme_info in comment_themes:
            theme = theme_info.get("theme", "")
            percentage = theme_info.get("percentage", 0)

            if theme == "accuracy" and percentage > 20:
                suggestions.append({
                    "priority": "high",
                    "category": "accuracy",
                    "title": "改进法律准确性",
                    "description": f"{percentage}%的评论涉及准确性问题",
                    "actions": [
                        "审查高频错误的法律领域",
                        "更新相关法律知识库",
                        "增加回答前的法条验证步骤",
                    ],
                })
            elif theme == "completeness" and percentage > 15:
                suggestions.append({
                    "priority": "medium",
                    "category": "completeness",
                    "title": "增强回复完整性",
                    "description": f"{percentage}%的评论提到回复不够完整",
                    "actions": [
                        "优化RAG检索策略，确保覆盖更多相关文档",
                        "增加多角度分析能力",
                        "提供补充信息引导",
                    ],
                })
            elif theme == "clarity" and percentage > 15:
                suggestions.append({
                    "priority": "medium",
                    "category": "clarity",
                    "title": "提升回复清晰度",
                    "description": f"{percentage}%的评论认为回复不够清晰",
                    "actions": [
                        "优化法律术语解释",
                        "增加结构化回复模板",
                        "添加案例说明辅助理解",
                    ],
                })

        # General improvement suggestions
        if avg_rating < 3.5:
            suggestions.append({
                "priority": "critical",
                "category": "overall",
                "title": "整体质量改进",
                "description": f"平均评分为 {avg_rating}，需要全面改进",
                "actions": [
                    "全面审查系统回复质量",
                    "加强RAG检索和LLM生成质量",
                    "建立持续改进机制",
                ],
            })

        return suggestions

    async def get_kpi_stats(
        self,
        db: AsyncSession,
        days: int = 30,
    ) -> dict[str, Any]:
        """Get key performance indicator statistics.

        Args:
            db: Database session.
            days: Number of days to cover.

        Returns:
            Dict with KPI metrics including satisfaction, accuracy, and volume.
        """
        since = datetime.utcnow() - timedelta(days=days)

        # Total feedback count
        result = await db.execute(
            select(func.count(Feedback.id)).where(Feedback.created_at >= since)
        )
        total_feedback = result.scalar() or 0

        if total_feedback == 0:
            return {
                "period_days": days,
                "total_feedback": 0,
                "satisfaction_rate": 0,
                "accuracy_rate": 0,
                "avg_response_time_seconds": 0,
                "total_responses": 0,
            }

        # Average rating
        result = await db.execute(
            select(func.avg(Feedback.rating)).where(Feedback.created_at >= since)
        )
        avg_rating = result.scalar() or 0

        # Satisfaction rate (rating >= 4)
        result = await db.execute(
            select(func.count(Feedback.id))
            .where(Feedback.created_at >= since, Feedback.rating >= 4)
        )
        satisfied = result.scalar() or 0
        satisfaction_rate = (satisfied / total_feedback * 100) if total_feedback > 0 else 0

        # Accuracy rate (not "inaccurate")
        result = await db.execute(
            select(func.count(Feedback.id))
            .where(
                Feedback.created_at >= since,
                Feedback.feedback_type != "inaccurate",
            )
        )
        accurate = result.scalar() or 0
        accuracy_rate = (accurate / total_feedback * 100) if total_feedback > 0 else 0

        # Total messages/responses in period
        result = await db.execute(
            select(func.count(Message.id))
            .where(
                Message.created_at >= since,
                Message.role == "assistant",
            )
        )
        total_responses = result.scalar() or 0

        # Rating distribution
        result = await db.execute(
            select(Feedback.rating, func.count(Feedback.id))
            .where(Feedback.created_at >= since)
            .group_by(Feedback.rating)
            .order_by(Feedback.rating)
        )
        rating_dist = {str(row[0]): row[1] for row in result.all()}

        # Feedback type distribution
        result = await db.execute(
            select(Feedback.feedback_type, func.count(Feedback.id))
            .where(Feedback.created_at >= since)
            .group_by(Feedback.feedback_type)
        )
        type_dist = {row[0]: row[1] for row in result.all()}

        return {
            "period_days": days,
            "total_feedback": total_feedback,
            "total_responses": total_responses,
            "average_rating": round(float(avg_rating), 2),
            "satisfaction_rate": round(satisfaction_rate, 1),
            "accuracy_rate": round(accuracy_rate, 1),
            "rating_distribution": rating_dist,
            "feedback_type_distribution": type_dist,
            "feedback_rate": round(total_feedback / total_responses * 100, 1) if total_responses > 0 else 0,
        }

    async def get_feedback_trend(
        self,
        db: AsyncSession,
        days: int = 30,
    ) -> list[dict[str, Any]]:
        """Get daily feedback trend data for charting.

        Args:
            db: Database session.
            days: Number of days to cover.

        Returns:
            List of daily trend data points.
        """
        since = datetime.utcnow() - timedelta(days=days)

        result = await db.execute(
            select(
                func.date(Feedback.created_at).label("day"),
                func.count(Feedback.id).label("count"),
                func.avg(Feedback.rating).label("avg_rating"),
            )
            .where(Feedback.created_at >= since)
            .group_by(func.date(Feedback.created_at))
            .order_by(func.date(Feedback.created_at))
        )

        return [
            {
                "date": str(row.day),
                "count": row.count,
                "avg_rating": round(float(row.avg_rating), 2),
            }
            for row in result.all()
        ]