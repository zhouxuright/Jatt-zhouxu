"""Audit log model for tracking user operations."""
from sqlalchemy import Integer, String, Text, Index
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base
from app.models.base import UUIDMixin, TimestampMixin


class AuditLog(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "audit_logs"

    user_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    #: 所属租户（P0-2）—— 合规审计导出按租户隔离
    tenant_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    resource_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resource_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)
    request_method: Mapped[str | None] = mapped_column(String(10), nullable=True)
    request_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    status_code: Mapped[int | None] = mapped_column(nullable=True)
    request_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    request_body_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True,
        doc="SHA256 hash of the first 1KB of the request body",
    )
    duration_ms: Mapped[int | None] = mapped_column(
        Integer, nullable=True,
        doc="Request processing duration in milliseconds",
    )
    response_status: Mapped[int | None] = mapped_column(
        Integer, nullable=True,
        doc="HTTP response status code (may differ from status_code for error handling)",
    )

    __table_args__ = (
        Index("ix_audit_logs_created", "created_at"),
        Index("ix_audit_logs_action_resource", "action", "resource_type"),
    )
