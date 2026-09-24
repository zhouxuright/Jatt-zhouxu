"""
RBAC (Role-Based Access Control) — 基于角色的访问控制系统

角色定义:
- super_admin: 超级管理员，拥有全部权限
- org_admin: 组织管理员，管理组织内用户和资源
- lawyer: 律师，完整的法律工具访问权限
- paralegal: 律师助理，受限的法律工具访问
- client: 客户/普通用户，仅聊天和只读访问

使用方法:
    from app.core.rbac import require_permission, require_role

    @router.get("/admin/users")
    async def list_users(
        current_user: User = Depends(require_permission("users:manage"))
    ):
        ...
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Any

from fastapi import Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


# ============================================================================
# Role Definitions
# ============================================================================

class Role(str, Enum):
    SUPER_ADMIN = "super_admin"
    ORG_ADMIN = "org_admin"
    LAWYER = "lawyer"
    PARALEGAL = "paralegal"
    CLIENT = "client"


# ============================================================================
# Permission Matrix
# ============================================================================

PERMISSIONS: dict[str, list[str]] = {
    Role.SUPER_ADMIN: [
        "*",  # Wildcard: all permissions
    ],
    Role.ORG_ADMIN: [
        "users:manage",
        "users:read",
        "org:manage",
        "org:read",
        "chat:*",
        "contract:*",
        "document:*",
        "law:*",
        "cases:*",
        "analytics:*",
        "analytics:read",
        "knowledge:*",
        "knowledge:read",
        "feedback:*",
        "feedback:read",
        "mcp:*",
        "skills:*",
    ],
    Role.LAWYER: [
        "chat:read",
        "chat:write",
        "chat:history",
        "contract:read",
        "contract:write",
        "contract:review",
        "document:read",
        "document:write",
        "document:generate",
        "law:read",
        "law:search",
        "cases:read",
        "cases:search",
        "mcp:read",
        "mcp:execute",
        "skills:read",
        "skills:execute",
        "feedback:write",
        "knowledge:read",
    ],
    Role.PARALEGAL: [
        "chat:read",
        "chat:write",
        "chat:history",
        "contract:read",
        "document:read",
        "law:read",
        "law:search",
        "cases:read",
        "cases:search",
        "skills:read",
        "skills:execute",
        "feedback:write",
        "knowledge:read",
    ],
    Role.CLIENT: [
        "chat:read",
        "chat:write",
        "document:read",
        "law:read",
        "feedback:write",
    ],
}


# ============================================================================
# Permission Checking
# ============================================================================

def check_permission(user_role: str, required_permission: str) -> bool:
    """
    检查角色是否具有指定权限。

    Args:
        user_role: 用户角色
        required_permission: 所需权限 (支持通配符匹配，如 "chat:*" 匹配 "chat:read")

    Returns:
        True 如果有权限，False 否则
    """
    permissions = PERMISSIONS.get(user_role, [])

    # Super admin has all permissions
    if "*" in permissions:
        return True

    # Direct match
    if required_permission in permissions:
        return True

    # Wildcard match: "chat:*" matches "chat:read", "chat:write", etc.
    required_parts = required_permission.split(":")
    for perm in permissions:
        perm_parts = perm.split(":")
        if len(perm_parts) == len(required_parts):
            match = True
            for pp, rp in zip(perm_parts, required_parts):
                if pp != "*" and pp != rp:
                    match = False
                    break
            if match:
                return True

    return False


# ============================================================================
# FastAPI Dependencies
# ============================================================================

def require_role(*roles: str):
    """
    FastAPI 依赖: 要求用户具有指定角色之一。

    Usage:
        @router.get("/admin")
        async def admin_only(user = Depends(require_role("super_admin", "org_admin"))):
            ...
    """
    from app.api.deps import get_current_user

    async def _check(current_user=Depends(get_current_user)):
        user_role = getattr(current_user, "role", None)
        if user_role is None:
            user_role = Role.CLIENT.value  # Default role

        if user_role not in roles:
            logger.warning(
                "Access denied: user %s (role=%s) tried to access role-restricted resource (required=%s)",
                getattr(current_user, "id", "unknown"),
                user_role,
                roles,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"权限不足。需要角色: {', '.join(roles)}，当前角色: {user_role}",
            )
        return current_user

    return _check


def require_permission(permission: str):
    """
    FastAPI 依赖: 要求用户具有指定权限。

    Usage:
        @router.get("/contracts")
        async def list_contracts(user = Depends(require_permission("contract:read"))):
            ...
    """
    from app.api.deps import get_current_user

    async def _check(current_user=Depends(get_current_user)):
        user_role = getattr(current_user, "role", None)
        if user_role is None:
            user_role = Role.CLIENT.value

        if not check_permission(user_role, permission):
            logger.warning(
                "Access denied: user %s (role=%s) lacks permission %s",
                getattr(current_user, "id", "unknown"),
                user_role,
                permission,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"权限不足。需要权限: {permission}，当前角色: {user_role} 无此权限",
            )
        return current_user

    return _check


def require_any_permission(*permissions: str):
    """
    FastAPI 依赖: 要求用户具有至少一个指定权限。

    Usage:
        @router.get("/data")
        async def access_data(user = Depends(require_any_permission("analytics:read", "contract:read"))):
            ...
    """
    from app.api.deps import get_current_user

    async def _check(current_user=Depends(get_current_user)):
        user_role = getattr(current_user, "role", None)
        if user_role is None:
            user_role = Role.CLIENT.value

        for perm in permissions:
            if check_permission(user_role, perm):
                return current_user

        logger.warning(
            "Access denied: user %s (role=%s) lacks any of permissions %s",
            getattr(current_user, "id", "unknown"),
            user_role,
            permissions,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"权限不足。需要以下权限之一: {', '.join(permissions)}",
        )

    return _check


# ============================================================================
# Organization / Tenant Isolation
# ============================================================================

class TenantContext:
    """租户上下文 — 用于多租户数据隔离。

    在中间件中设置，在数据库查询中使用。
    """

    def __init__(self, org_id: str | None = None):
        self.org_id = org_id

    def get_org_filter(self) -> dict[str, Any]:
        """获取组织过滤条件 (用于 SQLAlchemy 查询)"""
        if self.org_id:
            return {"org_id": self.org_id}
        return {}


# ============================================================================
# Role Hierarchy
# ============================================================================

ROLE_HIERARCHY = {
    Role.SUPER_ADMIN: 5,
    Role.ORG_ADMIN: 4,
    Role.LAWYER: 3,
    Role.PARALEGAL: 2,
    Role.CLIENT: 1,
}


def is_role_at_least(user_role: str, minimum_role: str) -> bool:
    """检查用户角色是否至少达到指定级别"""
    user_level = ROLE_HIERARCHY.get(user_role, 0)
    min_level = ROLE_HIERARCHY.get(minimum_role, 0)
    return user_level >= min_level
