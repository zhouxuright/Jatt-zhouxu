"""管理员鉴权边界：MCP 工具执行端点。

背景（见 docs/COMMERCIALIZATION_GAP_ANALYSIS_2026-09-24.md §6.1）：
"工具调用"调试页被移到 `/admin/tools` 并加了前端守卫，但前端守卫不是安全边界 ——
任何已登录的普通律师都能直接 `POST /api/v1/mcp/tools/execute`，拿到
`enterprise_lookup` 在未配置 key 时返回的 `source="demo_mode"` 编造企业信息。

这些测试锁住后端这一层：
- 执行类端点（execute / execute-batch）必须要求 admin；
- 发现类端点（GET /tools）必须对普通用户保持开放，因为对话页的工具选择器用它。

断言用"未知工具名"来区分「被鉴权拦下」和「通过了鉴权」：管理员请求会被放行到
handler，然后因为工具不存在返回 404 —— 这样既不触发真实工具执行（不会产生 LLM
调用或费用），又能证明鉴权确实通过了。
"""
from datetime import datetime, timedelta, timezone

import pytest
from jose import jwt

from app.core.config import settings
from app.models.user import User


def _token_for(user: User) -> dict[str, str]:
    """为指定用户签发一枚有效 JWT 的 Authorization 头。"""
    token = jwt.encode(
        {
            "sub": str(user.id),
            "exp": datetime.now(timezone.utc) + timedelta(hours=1),
            "iat": datetime.now(timezone.utc),
        },
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def admin_headers(async_session) -> dict[str, str]:
    """一个真实入库的 admin 用户，及其 Authorization 头。"""
    admin = User(
        username="adminuser",
        email="adminuser@example.com",
        hashed_password="x",  # 仅用于签发 token，不做登录
        role="admin",
        is_active=True,
    )
    async_session.add(admin)
    await async_session.flush()
    await async_session.refresh(admin)
    return _token_for(admin)


# ---------------------------------------------------------------------------
# 执行类端点：必须 admin
# ---------------------------------------------------------------------------

async def test_execute_requires_auth(test_client):
    """无 token → 401（未认证先于未授权）。"""
    res = await test_client.post(
        "/api/v1/mcp/tools/execute",
        json={"tool_name": "government_regulation", "parameters": {}},
    )
    assert res.status_code == 401, res.text


async def test_execute_rejects_non_admin(test_client, auth_headers):
    """普通律师（role=user）直接调用执行端点 → 403。"""
    res = await test_client.post(
        "/api/v1/mcp/tools/execute",
        json={"tool_name": "government_regulation", "parameters": {}},
        headers=auth_headers,
    )
    assert res.status_code == 403, res.text


async def test_execute_batch_rejects_non_admin(test_client, auth_headers):
    """批量执行端点同样必须是 admin。"""
    res = await test_client.post(
        "/api/v1/mcp/tools/execute-batch",
        json={"calls": [{"name": "government_regulation", "parameters": {}}]},
        headers=auth_headers,
    )
    assert res.status_code == 403, res.text


async def test_execute_allows_admin(test_client, admin_headers):
    """admin 通过鉴权 → 到达 handler。

    用一个不存在的工具名，断言 404（而不是 401/403）：404 来自 handler 内部，
    证明请求确实穿过了 `get_current_admin_user`，同时又不会真的执行任何工具。
    """
    res = await test_client.post(
        "/api/v1/mcp/tools/execute",
        json={"tool_name": "__does_not_exist__", "parameters": {}},
        headers=admin_headers,
    )
    assert res.status_code == 404, res.text


# ---------------------------------------------------------------------------
# 发现类端点：必须保持开放（对话页工具选择器依赖它）
# ---------------------------------------------------------------------------

async def test_list_tools_stays_open_to_non_admin(test_client, auth_headers):
    """普通用户仍应能列出工具 —— 否则对话页的 MCP popover 会挂掉。

    前端 ChatView.vue 用 `mcpApi.listTools()` 渲染工具列表，所以
    GET /tools 不能收归 admin-only（只有执行才是）。
    """
    res = await test_client.get("/api/v1/mcp/tools", headers=auth_headers)
    assert res.status_code == 200, res.text
    assert "tools" in res.json()


async def test_list_tools_requires_auth(test_client):
    """但匿名调用依然要 401（router.py 的默认保护）。"""
    res = await test_client.get("/api/v1/mcp/tools")
    assert res.status_code == 401, res.text
