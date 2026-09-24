"""Contract lifecycle models -- persist contracts from draft to archive.

Closes the P1.3 loop: drafting results, versions, key dates and archive
state are all persisted so the draft → review → compare → archive flow
survives across sessions.
"""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.crypto_types import EncryptedText
from app.core.database import Base
from app.models.base import TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.document import ContractReview
    from app.models.user import User


class ContractStatus:
    DRAFT = "draft"
    REVIEWED = "reviewed"
    ARCHIVED = "archived"


class Contract(Base, UUIDMixin, TimestampMixin):
    """A contract tracked through its full lifecycle."""

    __tablename__ = "contracts"
    __table_args__ = (
        Index("ix_contracts_user_id", "user_id"),
        Index("ix_contracts_status", "status"),
    )

    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
    )
    #: 所属租户（P0-2）
    tenant_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True, index=True,
    )
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    contract_type: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=ContractStatus.DRAFT,
    )
    current_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    content: Mapped[str] = mapped_column(
        EncryptedText, nullable=False, doc="Latest version text (合同正文，落盘加密)",
    )
    latest_review_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("contract_reviews.id", ondelete="SET NULL"), nullable=True,
    )
    archived_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    versions: Mapped[list["ContractVersion"]] = relationship(
        back_populates="contract",
        cascade="all, delete-orphan",
        order_by="ContractVersion.version_number",
    )
    key_dates: Mapped[list["ContractKeyDate"]] = relationship(
        back_populates="contract", cascade="all, delete-orphan",
    )
    latest_review: Mapped["ContractReview | None"] = relationship(
        foreign_keys=[latest_review_id],
    )

    def __repr__(self) -> str:
        return f"<Contract(id={self.id}, title={self.title!r}, status={self.status!r})>"


class ContractVersion(Base, UUIDMixin, TimestampMixin):
    """Immutable snapshot of one contract version."""

    __tablename__ = "contract_versions"
    __table_args__ = (
        Index("ix_contract_versions_contract_id", "contract_id"),
        Index("ix_contract_versions_number", "contract_id", "version_number", unique=True),
    )

    contract_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("contracts.id", ondelete="CASCADE"), nullable=False,
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(
        String(32), nullable=False, default="manual",
        doc="draft | redline | compare | manual | import",
    )
    content: Mapped[str] = mapped_column(
        EncryptedText, nullable=False, doc="该版本的合同正文（落盘加密）",
    )
    change_summary: Mapped[str | None] = mapped_column(EncryptedText, nullable=True)

    contract: Mapped["Contract"] = relationship(back_populates="versions")


class ContractKeyDate(Base, UUIDMixin, TimestampMixin):
    """A key date extracted from a contract, for reminder tracking."""

    __tablename__ = "contract_key_dates"
    __table_args__ = (Index("ix_contract_key_dates_contract_id", "contract_id"),)

    contract_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("contracts.id", ondelete="CASCADE"), nullable=False,
    )
    date_type: Mapped[str] = mapped_column(
        String(64), nullable=False, doc="签署日期/生效日期/到期日期/付款期限等",
    )
    date_value: Mapped[str] = mapped_column(
        String(64), nullable=False, doc="YYYY-MM-DD 或文字描述",
    )
    description: Mapped[str | None] = mapped_column(
        EncryptedText, nullable=True, doc="关键日期说明（可能含合同条款要点），落盘加密",
    )
    reminder_days_before: Mapped[int | None] = mapped_column(Integer, nullable=True)
    notified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    contract: Mapped["Contract"] = relationship(back_populates="key_dates")
