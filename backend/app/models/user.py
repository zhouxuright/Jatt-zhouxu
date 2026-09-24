"""User model for authentication and profile management."""

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.core.crypto import blind_index
from app.core.crypto_types import EncryptedString
from app.core.database import Base
from app.models.base import UUIDMixin, TimestampMixin

if TYPE_CHECKING:
    from app.models.conversation import Conversation
    from app.models.feedback import Feedback
    from app.models.document import Document


class UserRole:
    USER = "user"
    ADMIN = "admin"
    LAWYER = "lawyer"


class User(Base, UUIDMixin, TimestampMixin):
    """Application user."""

    __tablename__ = "users"

    username: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True
    )
    #: 邮箱为 PII，落盘加密（Fernet）。密文随机，故唯一性改由 email_bidx 保证。
    email: Mapped[str] = mapped_column(EncryptedString, nullable=False)
    #: 邮箱盲索引（HMAC-SHA256，64 位十六进制）：密文不可检索，靠它做等值查询与去重。
    email_bidx: Mapped[str | None] = mapped_column(
        String(64), nullable=True, unique=True, index=True,
        doc="Blind index of the (encrypted) email, for exact-match lookup & dedup",
    )
    hashed_password: Mapped[str] = mapped_column(String(256), nullable=False)
    role: Mapped[str] = mapped_column(
        SAEnum(UserRole.USER, UserRole.ADMIN, UserRole.LAWYER, name="user_role"),
        default=UserRole.USER,
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    #: 所属租户（P0-2 多租户隔离）。存量数据由迁移 008 回填为默认租户。
    tenant_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("tenants.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
        doc="Owning tenant; NULL is treated as the default tenant",
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None,
        doc="Timestamp when the account was soft-deleted",
    )
    deletion_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None,
        doc="Timestamp when account deletion was requested",
    )

    # Relationships — lazy="noload" to avoid loading all data on every auth check
    conversations: Mapped[list["Conversation"]] = relationship(
        "Conversation", back_populates="user", lazy="noload"
    )
    feedbacks: Mapped[list["Feedback"]] = relationship(
        "Feedback", back_populates="user", lazy="noload"
    )
    documents: Mapped[list["Document"]] = relationship(
        "Document", back_populates="user", lazy="noload"
    )

    @validates("email")
    def _sync_email_blind_index(self, _key: str, value: str) -> str:
        """写入邮箱时自动同步盲索引，业务代码无需手工维护。"""
        self.email_bidx = blind_index(value, "user.email")
        return value

    @staticmethod
    def email_lookup(value: str):
        """构造"按邮箱精确查询"的过滤条件。

        密文（Fernet）是随机化的，无法直接等值比较，因此一律走盲索引。
        存量数据的盲索引由迁移 ``007`` 回填。
        """
        return User.email_bidx == blind_index(value, "user.email")

    @staticmethod
    def email_search_pattern(value: str) -> str:
        """管理端搜索用：盲索引不支持子串匹配，此处退化为"精确命中"提示。"""
        return blind_index(value, "user.email") or ""

    def __repr__(self) -> str:
        return f"<User(id={self.id}, username={self.username!r}, role={self.role!r})>"