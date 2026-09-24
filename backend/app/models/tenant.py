"""租户模型（P0-2 多租户隔离）。

一个租户 = 一家客户（律所 / 企业法务部 / 政府部门）。单租户部署也使用
本表，默认租户 ``default`` 承接全部存量数据，保证同一套代码既能私有化
单租户交付、也能 SaaS 多租户运营。
"""

from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import UUIDMixin, TimestampMixin


class TenantStatus:
    ACTIVE = "active"
    SUSPENDED = "suspended"      # 欠费/违规暂停
    TRIAL = "trial"              # 试用


class Tenant(Base, UUIDMixin, TimestampMixin):
    """一个客户租户。"""

    __tablename__ = "tenants"

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    code: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True,
        doc="租户短代码（用于域名/登录域）",
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=TenantStatus.ACTIVE,
    )
    plan: Mapped[str] = mapped_column(
        String(32), nullable=False, default="standard",
        doc="standard | professional | enterprise | private",
    )
    max_users: Mapped[int] = mapped_column(Integer, nullable=False, default=50)
    contact_email: Mapped[str | None] = mapped_column(String(256), nullable=True)
    remark: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return f"<Tenant(id={self.id}, code={self.code!r}, status={self.status!r})>"
