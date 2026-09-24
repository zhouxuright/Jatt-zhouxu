"""跨会话长期记忆模型（P1-5）。

与 ``Conversation.fact_sheet``（会话内事实档案）不同，``UserMemory``
沉淀的是**跨会话**的稳定画像：当事人身份、常涉业务领域、偏好输出风格、
既有争议焦点、历史结论等。新会话首轮请求时自动注入，使 AI"记得"用户。

隐私设计：``profile_json`` / ``profile_text`` 均为加密列（``EncryptedText``），
符合 PIPL 对个人信息最小化与加密存储的要求。
"""

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.crypto_types import EncryptedText
from app.core.database import Base
from app.models.base import UUIDMixin, TimestampMixin

if TYPE_CHECKING:
    from app.models.user import User


class UserMemory(Base, UUIDMixin, TimestampMixin):
    """一名用户唯一一条跨会话长期记忆。"""

    __tablename__ = "user_memory"

    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    #: LegalFactSheet 的 JSON 快照（加密）
    profile_json: Mapped[str | None] = mapped_column(EncryptedText, nullable=True)
    #: 自然语言画像，直接拼进 system prompt（加密）
    profile_text: Mapped[str | None] = mapped_column(EncryptedText, nullable=True)
    #: 累计合并过的会话轮次数量，用于判断画像置信度
    entry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_conversation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    user: Mapped["User"] = relationship("User", backref="memory", uselist=False)

    def __repr__(self) -> str:
        return f"<UserMemory(user_id={self.user_id}, entries={self.entry_count})>"
