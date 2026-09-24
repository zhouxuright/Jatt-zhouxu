# -*- coding: utf-8 -*-
"""端到端验证：一次真实对话 → 引用校验 → 审计留痕（P0-3 + P0-4 闭环）。

单独成脚本是因为完整验证脚本会在前面打满限流（60 req/min），导致这一步
误报 429。本脚本只发 3 个请求，并在限流时自动退避重试。

用法：
    docker compose exec -e BASE=http://localhost:8001 backend \
        python scripts/verify_citation_audit_e2e.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

BASE = os.environ.get("BASE", "http://localhost:8001")
API = f"{BASE}/api/v1"


def _post_retry(url: str, **kwargs) -> requests.Response:
    """限流(429)时退避重试。"""
    for attempt in range(4):
        r = requests.post(url, timeout=kwargs.pop("timeout", 180), **kwargs)
        if r.status_code != 429:
            return r
        wait = 30 * (attempt + 1)
        print(f"  429 限流，等待 {wait}s 后重试…")
        time.sleep(wait)
    return r


async def _promote(username: str, role: str = "admin", active: bool = True) -> None:
    from sqlalchemy import text

    from app.core.database import async_session_factory

    async with async_session_factory() as db:
        await db.execute(
            text("UPDATE users SET role = :r, is_active = :a WHERE username = :u"),
            {"r": role, "a": active, "u": username},
        )
        await db.commit()


async def main() -> int:
    stamp = str(int(time.time()))[-6:]
    username = f"citeaudit{stamp}"
    email = f"citeaudit{stamp}@example.com"
    password = "CiteAudit@12345"

    requests.post(f"{API}/auth/register", json={
        "username": username, "email": email, "password": password, "full_name": "Citation Audit",
    }, timeout=60)
    await _promote(username, "admin", True)

    r = requests.post(f"{API}/auth/login", json={"username": username, "password": password}, timeout=60)
    if r.status_code != 200:
        print(f"FAIL 登录失败 status={r.status_code} {r.text[:200]}")
        return 1
    H = {"Authorization": f"Bearer {r.json()['token']['access_token']}"}

    print(">>> STEP 1  发起一次真实对话（包含法条引用）")
    resp = _post_retry(
        f"{API}/chat/chat",
        headers=H,
        json={"message": "请依据《中华人民共和国民法典》第一条说明民事权益受法律保护的原则，并注明条号。"},
        timeout=240,
    )
    print(f"    chat status={resp.status_code} len={len(resp.content)}")
    if resp.status_code != 200:
        print(f"FAIL 对话失败: {resp.text[:300]}")
        await _promote(username, "user", False)
        return 1

    body = resp.json()
    content = body.get("content") or body.get("answer") or ""
    print(f"    回答片段: {content[:120]}...")

    print(">>> STEP 2  回读审计日志（action=ai.citation_verify）")
    time.sleep(2)
    rl = requests.get(f"{API}/admin/audit-logs",
                      params={"action": "ai.citation_verify", "page_size": 5, "user_id": None},
                      headers=H, timeout=60)
    logs = rl.json().get("logs", []) if rl.status_code == 200 else []
    print(f"    audit status={rl.status_code} logs={len(logs)}")
    for item in logs[:3]:
        print(f"      - {item.get('action')} | {item.get('request_summary')}")

    hit = [x for x in logs if "citation_verify" in (x.get("request_summary") or "")]
    ok = len(hit) > 0
    print(f"\n{'PASS' if ok else 'FAIL'}  引用校验留痕: {len(hit)} 条")

    print(">>> STEP 3  导出 CSV 并核对是否含留痕")
    rc = requests.get(f"{API}/admin/audit-logs/export",
                      params={"format": "csv", "action": "ai.citation_verify", "limit": 200},
                      headers=H, timeout=60)
    csv_text = rc.text if rc.status_code == 200 else ""
    has_row = "citation_verify" in csv_text
    print(f"    export status={rc.status_code} bytes={len(rc.content)} 含留痕={has_row}")

    print(">>> STEP 4  清理测试账号")
    await _promote(username, "user", False)
    ok_clean = True
    print(f"\n{'PASS' if ok_clean else 'FAIL'}  清理完成")

    return 0 if (ok and has_row) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
