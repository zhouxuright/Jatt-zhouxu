"""Compliance risk tracking models (P1.5).

Persistent regulation-change monitoring:
- ComplianceWatchlist: per-user monitoring config (industry / topics / domains)
- RegulationChange: a regulation update detected by the monitor
- ComplianceAlert: watchlist x regulation-change match with risk analysis
"""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.base import TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.user import User


class ComplianceWatchlist(Base, UUIDMixin, TimestampMixin):
    """A user's compliance monitoring configuration."""

    __tablename__ = "compliance_watchlists"
    __table_args__ = (Index("ix_compliance_watchlists_user_id", "user_id"),)

    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    industry: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    topics: Mapped[str | None] = mapped_column(
        Text, nullable=True, doc="JSON list of free-text topics to watch",
    )
    compliance_domains: Mapped[str | None] = mapped_column(
        Text, nullable=True, doc="JSON list of COMPLIANCE_DOMAINS ids",
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_scan_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    alerts: Mapped[list["ComplianceAlert"]] = relationship(
        back_populates="watchlist", cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<ComplianceWatchlist(id={self.id}, name={self.name!r})>"


class RegulationChange(Base, UUIDMixin, TimestampMixin):
    """A regulation update detected from external sources (FLK etc.)."""

    __tablename__ = "regulation_changes"
    __table_args__ = (
        UniqueConstraint("dedupe_key", name="uq_regulation_changes_dedupe"),
        Index("ix_regulation_changes_publish_date", "publish_date"),
    )

    dedupe_key: Mapped[str] = mapped_column(
        String(256), nullable=False, doc="source:source_id, unique",
    )
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    law_type: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    publish_date: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    effective_date: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="flk")
    source_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    alerts: Mapped[list["ComplianceAlert"]] = relationship(
        back_populates="regulation_change",
    )

    def __repr__(self) -> str:
        return f"<RegulationChange(title={self.title!r}, publish={self.publish_date!r})>"


class ComplianceAlert(Base, UUIDMixin, TimestampMixin):
    """A risk alert raised when a regulation change hits a watchlist."""

    __tablename__ = "compliance_alerts"
    __table_args__ = (
        UniqueConstraint("watchlist_id", "regulation_change_id",
                         name="uq_compliance_alerts_pair"),
        Index("ix_compliance_alerts_user_id", "user_id"),
        Index("ix_compliance_alerts_status", "status"),
    )

    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
    )
    watchlist_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("compliance_watchlists.id", ondelete="CASCADE"), nullable=False,
    )
    regulation_change_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("regulation_changes.id", ondelete="CASCADE"), nullable=False,
    )
    risk_level: Mapped[str] = mapped_column(String(16), nullable=False, default="中")
    matched_keyword: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    analysis: Mapped[str | None] = mapped_column(
        Text, nullable=True, doc="JSON: impact / risk_level / suggested_actions",
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="open",
        doc="open | acknowledged | resolved",
    )
    acknowledged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    watchlist: Mapped["ComplianceWatchlist"] = relationship(back_populates="alerts")
    regulation_change: Mapped["RegulationChange"] = relationship(back_populates="alerts")
