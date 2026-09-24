"""Message model -- individual messages within a conversation."""

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String, Text, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.crypto_types import EncryptedText
from app.core.database import Base
from app.models.base import UUIDMixin, TimestampMixin

if TYPE_CHECKING:
    from app.models.conversation import Conversation
    from app.models.feedback import Feedback


class MessageRole:
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class Message(Base, UUIDMixin, TimestampMixin):
    """A single message in a conversation."""

    __tablename__ = "messages"

    conversation_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(
        String(16), nullable=False,
        doc="Role: user, assistant, or system",
    )
    content: Mapped[str] = mapped_column(
        EncryptedText, nullable=False,
        doc="消息正文（含当事人信息等敏感内容），落盘加密",
    )
    tokens_used: Mapped[int | None] = mapped_column(Integer, nullable=True)
    metadata_: Mapped[str | None] = mapped_column(
        "metadata", Text, nullable=True,
        doc="JSON-encoded metadata (citations, confidence, etc.)",
    )

    # Relationships
    conversation: Mapped["Conversation"] = relationship(
        "Conversation", back_populates="messages",
    )
    feedbacks: Mapped[list["Feedback"]] = relationship(
        "Feedback", back_populates="message", lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<Message(id={self.id}, role={self.role!r})>"
