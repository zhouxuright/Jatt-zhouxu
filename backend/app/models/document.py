"""Document model -- uploaded legal documents for review."""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Float, ForeignKey, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.crypto_types import EncryptedText
from app.core.database import Base
from app.models.base import TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.user import User


class DocumentStatus:
    UPLOADED = "uploaded"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"


class Document(Base, UUIDMixin):
    """An uploaded legal document (contract, brief, etc.)."""

    __tablename__ = "documents"

    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    #: 所属租户（P0-2）
    tenant_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True, index=True,
    )
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(512), nullable=False)
    file_type: Mapped[str] = mapped_column(
        String(64), nullable=False,
        doc="e.g., pdf, docx, txt, xlsx",
    )
    file_size: Mapped[int] = mapped_column(nullable=False, doc="File size in bytes")
    file_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=DocumentStatus.UPLOADED,
    )
    error_message: Mapped[str | None] = mapped_column(EncryptedText, nullable=True)
    summary: Mapped[str | None] = mapped_column(
        EncryptedText, nullable=True, doc="文书解析摘要（含正文片段），落盘加密",
    )
    upload_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="documents")

    def __repr__(self) -> str:
        return f"<Document(id={self.id}, filename={self.original_filename!r}, status={self.status!r})>"


class ContractReview(Base, UUIDMixin, TimestampMixin):
    """Persisted contract review results."""

    __tablename__ = "contract_reviews"
    __table_args__ = (
        Index("ix_contract_reviews_user_id", "user_id"),
        Index("ix_contract_reviews_document_id", "document_id"),
    )

    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    document_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("documents.id", ondelete="SET NULL"),
        nullable=True,
    )
    original_filename: Mapped[str] = mapped_column(String(512), nullable=False)
    risk_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    risk_items: Mapped[str | None] = mapped_column(
        EncryptedText, nullable=True, doc="JSON-serialized list of risk items (加密)"
    )
    summary: Mapped[str | None] = mapped_column(EncryptedText, nullable=True)
    full_analysis: Mapped[str | None] = mapped_column(
        EncryptedText, nullable=True, doc="合同审查全文分析（含合同正文引用），落盘加密",
    )

    # Relationships
    user: Mapped["User"] = relationship("User", backref="contract_reviews")
    document: Mapped["Document | None"] = relationship("Document", backref="contract_reviews")

    def __repr__(self) -> str:
        return f"<ContractReview(id={self.id}, user_id={self.user_id}, risk_score={self.risk_score})>"
