"""Conversation model -- groups messages into a single chat session."""

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.crypto_types import EncryptedJSON, EncryptedText
from app.core.database import Base
from app.models.base import UUIDMixin, TimestampMixin

if TYPE_CHECKING:
    from app.models.user import User
    from app.models.message import Message


class Conversation(Base, UUIDMixin, TimestampMixin):
    """A conversation (chat session) between a user and the AI assistant."""

    __tablename__ = "conversations"

    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: 所属租户（P0-2）；列表/详情查询强制按此过滤
    tenant_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True, index=True,
    )
    title: Mapped[str] = mapped_column(String(256), nullable=False, default="New Conversation")
    agent_type: Mapped[str] = mapped_column(
        String(64), nullable=False, default="legal_consultation",
        doc="Type of agent: legal_consultation, contract_review, document_generation",
    )
    summary: Mapped[str | None] = mapped_column(
        EncryptedText, nullable=True, doc="增量摘要（含案情描述），落盘加密",
    )
    fact_sheet: Mapped[dict | None] = mapped_column(
        EncryptedJSON, nullable=True,
        doc="结构化事实档案（当事人/争议焦点/引用法条），加密落盘",
    )
    topic_segments: Mapped[list | None] = mapped_column(
        EncryptedJSON, nullable=True,
        doc="对话中识别出的主题分段，加密落盘",
    )

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="conversations")
    messages: Mapped[list["Message"]] = relationship(
        "Message", back_populates="conversation", lazy="selectin", order_by="Message.created_at",
    )

    def __repr__(self) -> str:
        return f"<Conversation(id={self.id}, title={self.title!r}, agent_type={self.agent_type!r})>"
