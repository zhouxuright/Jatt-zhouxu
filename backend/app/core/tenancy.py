"""多租户隔离（P0-2）。

商业客户（律所/企业法务）要求数据物理隔离、互不可见。本模块提供
**应用层强制**的租户作用域：

* 每个用户归属一个租户（``User.tenant_id``）；
* 所有承载客户数据的表都带 ``tenant_id``；
* 查询一律经 ``scope_query`` 注入租户过滤；单条资源读取经
  ``ensure_access`` 校验，跨租户访问一律返回 404（不泄露资源存在性）。

为什么以应用层为主、RLS 为辅
----------------------------
PostgreSQL RLS 对 **超级用户无效**（会静默放行），而本项目容器默认以
``postgres`` 超级用户连接。若只依赖 RLS，会形成"看起来隔离、实际不隔离"
的高危假象。因此：

1. 应用层过滤是**生效的一线防线**（本模块）；
2. RLS 作为二线防线，需配合非超级用户角色使用，见
   ``scripts/enable_rls_hardening.sql``（可选加固，部署时按需启用）。

两者叠加即"纵深防御"，且不会出现单点失效。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import Select

from app.core.constants import DEFAULT_TENANT_CODE, DEFAULT_TENANT_ID

logger = logging.getLogger(__name__)

#: 默认租户 —— 系统初始化时创建，用于承接存量数据与单租户部署。
#: （定义在 ``app.core.constants`` 以避免 core/models 循环导入）

#: 超级管理员角色可跨租户（平台运营方），但仍需显式声明。
PLATFORM_ADMIN_ROLE = "admin"


def tenant_of(user: Any) -> str:
    """取用户所属租户；未设置时归入默认租户（兼容存量数据）。"""
    value = getattr(user, "tenant_id", None)
    return value or DEFAULT_TENANT_ID


def get_default_tenant_id() -> str:
    """新用户注册/批量导入时绑定的租户（可由配置覆盖，便于单租户交付改名）。"""
    from app.core.config import settings

    return (getattr(settings, "DEFAULT_TENANT_ID", "") or "").strip() or DEFAULT_TENANT_ID


def is_platform_admin(user: Any) -> bool:
    """平台级管理员可跨租户查看（用于运营/审计后台）。"""
    return getattr(user, "role", None) == PLATFORM_ADMIN_ROLE


def scope_query(stmt: Select, model: Any, tenant_id: str | None) -> Select:
    """为查询注入租户过滤条件。

    ``tenant_id`` 为 ``None`` 时不做限制（仅用于系统内部任务，如迁移、
    定时报表），业务接口一律传入具体租户。
    """
    if tenant_id is None:
        return stmt
    if not hasattr(model, "tenant_id"):
        # 模型未启用租户（如全局法律知识库）——不做过滤。
        return stmt
    return stmt.where(model.tenant_id == tenant_id)


def ensure_access(obj: Any, tenant_id: str | None, *, resource: str = "资源") -> None:
    """校验单条资源归属；跨租户访问抛 404（不暴露"资源存在但无权"）。"""
    if obj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{resource}不存在")
    if tenant_id is None:
        return
    obj_tenant = getattr(obj, "tenant_id", None)
    if obj_tenant is None:
        # 存量数据未打标 —— 视为默认租户。
        obj_tenant = DEFAULT_TENANT_ID
    if obj_tenant != tenant_id:
        logger.warning(
            "跨租户访问被拒绝: resource=%s owner_tenant=%s request_tenant=%s",
            resource, obj_tenant, tenant_id,
        )
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{resource}不存在")
