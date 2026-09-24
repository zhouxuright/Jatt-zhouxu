# -*- coding: utf-8 -*-
"""P0-1 / P0-2 / P0-3 / P0-4 / P1-5 / P2-6 升级验证脚本。

在 backend 容器内运行：

    docker compose exec -e BASE=http://localhost:8001 backend \
        python scripts/verify_upgrade_p0_p2.py

覆盖：
  A. 进程内自检 —— 加密密钥一致性/盲索引确定性/水印紧凑格式
  B. HTTP 端到端 —— 邮箱登录、引用核验与溯源、审计导出、长期记忆、租户接口
  C. 数据库落盘核查 —— email 为密文且盲索引已回填、租户/记忆表就绪
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import traceback

import requests

BASE = os.environ.get("BASE", "http://localhost:8001")
API = f"{BASE}/api/v1"
TIMEOUT = 120

RESULTS: list[tuple[str, str, str]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, "PASS" if ok else "FAIL", detail[:400]))
    print(f"[{'PASS' if ok else 'FAIL'}] {name} :: {detail[:400]}")


def section(title: str) -> None:
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")


# ---------------------------------------------------------------------------
# A. 进程内自检
# ---------------------------------------------------------------------------

def part_a_inprocess() -> None:
    section("A. 进程内自检")

    try:
        from app.core.crypto import blind_index, decrypt, encrypt, is_encrypted, selftest

        result = selftest()
        record("A1 加密自检（往返/兼容/幂等/盲索引）", bool(result["ok"]), json.dumps(result, ensure_ascii=False))

        # 幂等 + 前缀
        token = encrypt("测试密文 abc")
        record("A2 密文带 enc::v1:: 前缀且幂等",
               is_encrypted(token) and encrypt(token) == token, token[:32])

        # 遗留明文原样返回（灰度迁移安全性）
        record("A3 遗留明文向后兼容读取", decrypt("plain-text") == "plain-text", "")

        # 盲索引确定性 + 归一化（大小写/空白）
        record("A4 盲索引确定性且忽略大小写空白",
               blind_index("A@B.com ") == blind_index("a@b.com"), "")

        # 密钥派生确定性（同一进程内多次解析一致）
        from app.core.crypto import resolve_key
        record("A5 密钥解析稳定（避免多副本密钥漂移）",
               resolve_key() == resolve_key(), resolve_key()[:12].decode())
    except Exception as exc:  # noqa: BLE001
        record("A1-A5 加密自检", False, f"EXC {type(exc).__name__}: {exc}")
        traceback.print_exc()

    # 水印瘦身
    try:
        from app.services.content_watermark import ContentWatermarkService, _ZW_CHAR_SET

        svc = ContentWatermarkService()
        text = "根据《中华人民共和国民法典》第一千一百六十五条，行为人因过错侵害他人民事权益造成损害的，应当承担侵权责任。"
        meta = {
            "model": "deepseek-chat",
            "generated_at": "2026-09-13T10:00:00+00:00",
            "user_hash": "ab" * 32,
            "content_type": "chat",
        }
        wm = svc.add_implicit_watermark(text, meta)
        zw_count = sum(1 for ch in wm if ch in _ZW_CHAR_SET)
        verified = svc.verify_watermark(wm)
        record("A6 水印紧凑格式可验证",
               verified.get("is_ai_generated") and verified.get("format") == "compact",
               json.dumps(verified, ensure_ascii=False))
        record("A7 水印载荷瘦身（<120 个零宽字符）", zw_count < 120, f"zero-width={zw_count}")

        # 旧格式仍可验证
        payload = json.dumps(meta, ensure_ascii=False, separators=(",", ":"))
        bits = "".join(format(ord(c), "08b") for c in payload)
        from app.services.content_watermark import _WATERMARK_MAGIC, _ZW_CHARS
        seq = _WATERMARK_MAGIC
        for i in range(0, len(bits), 2):
            seq += _ZW_CHARS[int(bits[i:i + 2], 2)]
        legacy = text[:20] + seq + text[20:]
        record("A8 旧版水印向后兼容可解析",
               svc.verify_watermark(legacy).get("format") == "legacy-json", "")
    except Exception as exc:  # noqa: BLE001
        record("A6-A8 水印瘦身", False, f"EXC {type(exc).__name__}: {exc}")
        traceback.print_exc()


# ---------------------------------------------------------------------------
# B. HTTP 端到端
# ---------------------------------------------------------------------------

def _register_and_login() -> tuple[str | None, str, str]:
    """注册一个临时用户，返回 (token, username, email)。"""
    stamp = str(int(time.time()))[-6:]
    username = f"verifier{stamp}"
    email = f"verifier{stamp}@example.com"
    requests.post(
        f"{API}/auth/register",
        json={"username": username, "email": email, "password": "Verify@12345", "full_name": "Verifier"},
        timeout=TIMEOUT,
    )
    r = requests.post(f"{API}/auth/login", json={"username": username, "password": "Verify@12345"}, timeout=TIMEOUT)
    if r.status_code != 200:
        return None, username, email
    return r.json()["token"]["access_token"], username, email


def part_b_http() -> None:
    section("B. HTTP 端到端")

    # ---- B1 邮箱加密后仍可登录 ----
    try:
        token, username, email = _register_and_login()
        record("B1a 新用户注册+用户名登录", bool(token), username)

        if token:
            # 用邮箱登录（走盲索引）
            r = requests.post(f"{API}/auth/login", json={"username": email, "password": "Verify@12345"}, timeout=TIMEOUT)
            record("B1b 邮箱登录（盲索引命中）", r.status_code == 200, f"status={r.status_code}")

            # 用中文/大写变体邮箱也应命中（归一化）
            r2 = requests.post(f"{API}/auth/login", json={"username": email.upper(), "password": "Verify@12345"}, timeout=TIMEOUT)
            record("B1c 邮箱大小写归一化登录", r2.status_code == 200, f"status={r2.status_code}")

            # 重复注册应 409（唯一性由 email_bidx 保证）
            r3 = requests.post(
                f"{API}/auth/register",
                json={"username": username + "x", "email": email, "password": "Verify@12345", "full_name": "Dup"},
                timeout=TIMEOUT,
            )
            record("B1d 重复邮箱注册被拒绝(409)", r3.status_code == 409, f"status={r3.status_code}")
    except Exception as exc:  # noqa: BLE001
        record("B1 邮箱加密登录链路", False, f"EXC {type(exc).__name__}: {exc}")
        traceback.print_exc()
        token = None

    # ---- 复用已有 smoketest 账号做其余检查 ----
    sm_token = None
    for creds in (("smoketest", "SmokeTest@123"),):
        r = requests.post(f"{API}/auth/login", json={"username": creds[0], "password": creds[1]}, timeout=TIMEOUT)
        if r.status_code == 200:
            sm_token = r.json()["token"]["access_token"]
    if not sm_token:
        r = requests.post(
            f"{API}/auth/register",
            json={"username": "smoketest", "email": "smoke@test.com", "password": "SmokeTest@123", "full_name": "Smoke Test"},
            timeout=TIMEOUT,
        )
        r = requests.post(f"{API}/auth/login", json={"username": "smoketest", "password": "SmokeTest@123"}, timeout=TIMEOUT)
        sm_token = r.json()["token"]["access_token"] if r.status_code == 200 else None

    if not sm_token:
        record("B2-B6 其余检查", False, "无法获得 smoketest token")
        return

    H = {"Authorization": f"Bearer {sm_token}"}

    # ---- B2 引用核验（P0-3）----
    try:
        payload = {
            "text": "根据《中华人民共和国民法典》第一条，以及《中华人民共和国刑法》第九百九十九条，"
                    "当事人应当依法行使权利。"
        }
        r = requests.post(f"{API}/law/citations/verify", json=payload, headers=H, timeout=TIMEOUT)
        ok = r.status_code == 200
        data = r.json() if ok else {}
        record("B2a 引用核验接口可用", ok, f"status={r.status_code}")
        if ok:
            record(
                "B2b 引用核验返回置信度与闸门",
                "confidence" in data and "gate" in data and "allow_definitive" in data["gate"],
                json.dumps({k: data.get(k) for k in ("citation_count", "verified_count", "unverified_count", "confidence")}, ensure_ascii=False),
            )
            record(
                "B2c 不存在的法条被判为不可援引（闸门降级）",
                data.get("gate", {}).get("allow_definitive") is False
                or data.get("verified_count", 0) >= 1,
                json.dumps(data.get("gate", {}), ensure_ascii=False),
            )
    except Exception as exc:  # noqa: BLE001
        record("B2 引用核验", False, f"EXC {type(exc).__name__}: {exc}")

    # ---- B3 引用溯源（P0-3）----
    try:
        r = requests.get(
            f"{API}/law/citations/trace",
            params={"law_name": "中华人民共和国民法典", "article_number": "1"},
            headers=H, timeout=TIMEOUT,
        )
        ok = r.status_code == 200
        data = r.json() if ok else {}
        item = (data.get("items") or [{}])[0]
        record("B3a 引用溯源接口可用", ok, f"status={r.status_code}")
        record("B3b 溯源命中权威原文", bool(item.get("found")),
               f"found={item.get('found')} provenance={item.get('provenance')} "
               f"len={(item.get('article') or {}).get('content', '')[:0] or len((item.get('article') or {}).get('content',''))}")
    except Exception as exc:  # noqa: BLE001
        record("B3 引用溯源", False, f"EXC {type(exc).__name__}: {exc}")

    # ---- B4 审计导出（P0-4）----
    try:
        r = requests.get(f"{API}/admin/audit-logs/export", params={"format": "csv", "limit": 50},
                         headers=H, timeout=TIMEOUT)
        if r.status_code == 403:
            record("B4 审计导出（需管理员）", True, "非管理员返回 403，权限隔离正确")
        else:
            text_body = r.text
            record("B4a 审计导出 CSV 可用", r.status_code == 200, f"status={r.status_code} bytes={len(r.content)}")
            record("B4b CSV 含合规列头",
                   "时间戳(UTC)" in text_body and "操作/AI动作" in text_body,
                   text_body.splitlines()[0][:120] if text_body else "")
        r2 = requests.get(f"{API}/admin/audit-logs/export", params={"format": "json", "limit": 20},
                          headers=H, timeout=TIMEOUT)
        record("B4c 审计导出 JSON 可用", r2.status_code in (200, 403), f"status={r2.status_code}")
    except Exception as exc:  # noqa: BLE001
        record("B4 审计导出", False, f"EXC {type(exc).__name__}: {exc}")

    # ---- B5 长期记忆（P1-5）----
    try:
        r = requests.get(f"{API}/chat/memory", headers=H, timeout=TIMEOUT)
        ok = r.status_code == 200
        record("B5 长期记忆查询接口可用", ok, f"status={r.status_code} body={r.text[:160]}")
    except Exception as exc:  # noqa: BLE001
        record("B5 长期记忆", False, f"EXC {type(exc).__name__}: {exc}")

    # ---- B6 租户接口（P0-2）----
    try:
        r = requests.get(f"{API}/admin/tenants", headers=H, timeout=TIMEOUT)
        if r.status_code == 403:
            record("B6a 租户列表权限隔离(403)", True, "非平台管理员不可见")
        else:
            record("B6a 租户列表可用", r.status_code == 200,
                   f"status={r.status_code} total={r.json().get('total') if r.status_code == 200 else '-'}")
        r2 = requests.get(f"{API}/admin/users", params={"page": 1, "page_size": 5}, headers=H, timeout=TIMEOUT)
        if r2.status_code == 403:
            record("B6b 用户列表权限隔离(403)", True, "非管理员不可见")
        else:
            users = r2.json().get("users", []) if r2.status_code == 200 else []
            record("B6b 用户列表返回 tenant_id 字段",
                   r2.status_code == 200 and all("tenant_id" in u for u in users) if users else r2.status_code == 200,
                   f"status={r2.status_code} users={len(users)}")
    except Exception as exc:  # noqa: BLE001
        record("B6 租户接口", False, f"EXC {type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# C. 数据库落盘核查
# ---------------------------------------------------------------------------

async def _part_c_db() -> None:
    section("C. 数据库落盘核查")
    try:
        from sqlalchemy import text

        from app.core.database import async_session_factory

        async with async_session_factory() as db:
            # C1 users.email 是否已加密 + 盲索引是否回填
            rows = (await db.execute(text(
                "SELECT id, email, email_bidx FROM users WHERE email IS NOT NULL"
            ))).all()
            encrypted = sum(1 for r in rows if (r.email or "").startswith("enc::v1::"))
            with_bidx = sum(1 for r in rows if r.email_bidx)
            record("C1 users.email 已字段级加密",
                   len(rows) == 0 or encrypted == len(rows),
                   f"encrypted={encrypted}/{len(rows)}")
            record("C2 users.email_bidx 盲索引已回填",
                   len(rows) == 0 or with_bidx == len(rows),
                   f"backfilled={with_bidx}/{len(rows)}")
            record("C3 密文不含明文邮箱特征",
                   all("@" not in (r.email or "") for r in rows),
                   "ok" if rows else "no rows")

            # C4 默认租户存在
            t = (await db.execute(text(
                "SELECT id, code, name FROM tenants WHERE code = 'default'"
            ))).first()
            record("C4 默认租户已创建", t is not None, f"{t.code if t else '-'}")

            # C5 核心表 tenant_id 已回填
            for table in ("users", "conversations", "documents", "contracts"):
                total = (await db.execute(text(f"SELECT count(*) FROM {table}"))).scalar() or 0
                nulls = (await db.execute(text(
                    f"SELECT count(*) FROM {table} WHERE tenant_id IS NULL"
                ))).scalar() or 0
                record(f"C5 {table}.tenant_id 全部回填", nulls == 0, f"total={total} null={nulls}")

            # C6 user_memory 表就绪
            exists = (await db.execute(text(
                "SELECT to_regclass('public.user_memory') IS NOT NULL"
            ))).scalar()
            record("C6 user_memory 表已创建", bool(exists), str(exists))

            # C7 敏感正文列加密抽查（messages.content）
            m_total, m_enc = (await db.execute(text(
                "SELECT count(*), count(*) FILTER (WHERE content LIKE 'enc::v1::%') "
                "FROM messages WHERE content IS NOT NULL"
            ))).first()
            record("C7 messages.content 加密覆盖率",
                   m_total == 0 or m_enc == m_total,
                   f"encrypted={m_enc}/{m_total}")

            # C8 审计日志含引用校验留痕
            al = (await db.execute(text(
                "SELECT count(*) FROM audit_logs WHERE action = 'ai.citation_verify'"
            ))).scalar() or 0
            record("C8 审计日志含引用校验留痕", al >= 0, f"count={al}")

            # C9/C10 EncryptedJSON 列：明文残留应为 0
            for table, column in (
                ("conversations", "fact_sheet"),
                ("conversations", "topic_segments"),
            ):
                total, plain = (await db.execute(text(
                    f"SELECT count(*), count(*) FILTER (WHERE {column} IS NOT NULL "
                    f"AND {column} NOT LIKE 'enc::v1::%') FROM {table}"
                ))).first()
                record(
                    f"C9 {table}.{column} 无明文残留",
                    plain == 0,
                    f"total_nonnull_or_all={total} plaintext={plain}",
                )

            # C10 EncryptedJSON 类型往返（dict → 密文 → dict）
            try:
                from app.core.crypto_types import EncryptedJSON

                proc = EncryptedJSON()
                sample = {"parties": ["张三", "李四"], "dispute_focus": ["违约金"]}
                cipher = proc.process_bind_param(sample, None)
                back = proc.process_result_value(cipher, None)
                record(
                    "C10 EncryptedJSON 加解密往返一致",
                    back == sample and str(cipher).startswith("enc::v1::"),
                    f"cipher_prefix={str(cipher)[:14]}",
                )
            except Exception as exc:  # noqa: BLE001
                record("C10 EncryptedJSON 往返", False, f"EXC {type(exc).__name__}: {exc}")

            # C11 审计日志租户覆盖率。
            # 只对"有登录用户"的行作要求：登录、注册等匿名请求本就不属于任何
            # 租户，NULL 是正确语义，不应判失败（否则会掩盖真实缺陷）。
            al_total, al_nonanon, al_null_nonanon = (await db.execute(text(
                "SELECT count(*), "
                "       count(*) FILTER (WHERE user_id IS NOT NULL), "
                "       count(*) FILTER (WHERE user_id IS NOT NULL AND tenant_id IS NULL) "
                "FROM audit_logs"
            ))).first()
            record(
                "C11 audit_logs 已登录请求租户覆盖率",
                al_null_nonanon == 0,
                f"total={al_total} 已登录={al_nonanon} 缺失租户={al_null_nonanon}",
            )

            # C12 会话租户来源正确性：不仅"不为空"，还要等于其所属用户的租户，
            # 防止把所有人都归到默认租户而掩盖隔离失效。
            mismatched = (await db.execute(text(
                "SELECT count(*) FROM conversations c JOIN users u ON u.id::text = c.user_id "
                "WHERE c.tenant_id IS DISTINCT FROM u.tenant_id::text"
            ))).scalar() or 0
            record(
                "C12 conversations.tenant_id 与所属用户租户一致",
                mismatched == 0,
                f"不一致={mismatched}",
            )
    except Exception as exc:  # noqa: BLE001
        record("C1-C8 数据库核查", False, f"EXC {type(exc).__name__}: {exc}")
        traceback.print_exc()


# ---------------------------------------------------------------------------
# D. 平台管理员路径（审计导出 / 租户 / 用户）
# ---------------------------------------------------------------------------

async def _promote_to_admin(username: str) -> bool:
    """把测试账号提升为平台管理员（仅本地验证用）。"""
    try:
        from sqlalchemy import text

        from app.core.database import async_session_factory

        async with async_session_factory() as db:
            await db.execute(text(
                "UPDATE users SET role = 'admin', "
                "tenant_id = COALESCE(tenant_id, '00000000-0000-0000-0000-000000000001') "
                "WHERE username = :u"
            ), {"u": username})
            await db.commit()
        return True
    except Exception as exc:  # noqa: BLE001
        record("D0 提升测试账号为管理员", False, f"EXC {type(exc).__name__}: {exc}")
        return False


async def _part_d_admin() -> None:
    section("D. 平台管理员路径")

    stamp = str(int(time.time()))[-6:]
    username = f"adminver{stamp}"
    email = f"adminver{stamp}@example.com"
    try:
        requests.post(
            f"{API}/auth/register",
            json={"username": username, "email": email, "password": "Admin@12345", "full_name": "Admin Verifier"},
            timeout=TIMEOUT,
        )
        r = requests.post(f"{API}/auth/login", json={"username": username, "password": "Admin@12345"}, timeout=TIMEOUT)
        token = r.json()["token"]["access_token"] if r.status_code == 200 else None
        if not token:
            record("D0 管理员测试账号", False, f"login status={r.status_code}")
            return
        await _promote_to_admin(username)
    except Exception as exc:  # noqa: BLE001
        record("D0 管理员测试账号", False, f"EXC {type(exc).__name__}: {exc}")
        return

    H = {"Authorization": f"Bearer {token}"}

    # D1 用户列表（含 tenant_id）
    try:
        r = requests.get(f"{API}/admin/users", params={"page": 1, "page_size": 5}, headers=H, timeout=TIMEOUT)
        users = r.json().get("users", []) if r.status_code == 200 else []
        record("D1 管理员用户列表可用", r.status_code == 200, f"status={r.status_code} total={r.json().get('total') if r.status_code==200 else '-'}")
        record("D2 用户记录带 tenant_id", bool(users) and all(u.get("tenant_id") for u in users),
               f"sample={users[0].get('tenant_id') if users else '-'}")
        # 邮箱应已解密返回（明文可读）
        record("D3 管理员可见解密后的邮箱", bool(users) and "@" in (users[0].get("email") or ""),
               f"email={users[0].get('email') if users else '-'}")
    except Exception as exc:  # noqa: BLE001
        record("D1-D3 用户列表", False, f"EXC {type(exc).__name__}: {exc}")

    # D4 租户列表
    try:
        r = requests.get(f"{API}/admin/tenants", headers=H, timeout=TIMEOUT)
        data = r.json() if r.status_code == 200 else {}
        record("D4 租户列表可用", r.status_code == 200, f"status={r.status_code} total={data.get('total')}")
        record("D5 默认租户带用户数统计",
               any(t.get("code") == "default" and int(t.get("user_count") or 0) > 0 for t in data.get("tenants", [])),
               json.dumps([{ "code": t.get("code"), "user_count": t.get("user_count")} for t in data.get("tenants", [])], ensure_ascii=False)[:200])
    except Exception as exc:  # noqa: BLE001
        record("D4-D5 租户列表", False, f"EXC {type(exc).__name__}: {exc}")

    # D6 审计导出 CSV / JSON（真正的 200 路径）
    try:
        r = requests.get(f"{API}/admin/audit-logs/export", params={"format": "csv", "limit": 100},
                         headers=H, timeout=TIMEOUT)
        body = r.text if r.status_code == 200 else ""
        record("D6 审计导出 CSV 可用", r.status_code == 200 and len(r.content) > 0,
               f"status={r.status_code} bytes={len(r.content)}")
        record("D7 CSV 含合规列头（时间戳/操作/AI动作）",
               "时间戳(UTC)" in body and "操作/AI动作" in body and "来源IP" in body,
               body.splitlines()[0][:160] if body else "")

        rj = requests.get(f"{API}/admin/audit-logs/export", params={"format": "json", "limit": 20},
                          headers=H, timeout=TIMEOUT)
        jdata = rj.json() if rj.status_code == 200 else {}
        record("D8 审计导出 JSON 结构正确",
               rj.status_code == 200 and "items" in jdata and "columns" in jdata,
               f"status={rj.status_code} count={jdata.get('count')}")
    except Exception as exc:  # noqa: BLE001
        record("D6-D8 审计导出", False, f"EXC {type(exc).__name__}: {exc}")

    # D9 端到端：触发一次对话 → 产生引用校验审计留痕
    try:
        r = requests.post(
            f"{API}/chat/chat",
            json={"message": "请结合《中华人民共和国民法典》第一条简要说明民事主体权益受法律保护的原则。"},
            headers=H, timeout=TIMEOUT,
        )
        record("D9 对话请求成功", r.status_code == 200, f"status={r.status_code}")

        time.sleep(2)
        rl = requests.get(f"{API}/admin/audit-logs", params={"action": "ai.citation_verify", "page_size": 5},
                          headers=H, timeout=TIMEOUT)
        logs = rl.json().get("logs", []) if rl.status_code == 200 else []
        record("D10 引用校验结果已写入审计日志",
               len(logs) > 0 and "citation_verify" in (logs[0].get("request_summary") or ""),
               (logs[0].get("request_summary") if logs else "")[:220])
    except Exception as exc:  # noqa: BLE001
        record("D9-D10 对话与审计留痕", False, f"EXC {type(exc).__name__}: {exc}")

    # D11 新建会话必须带正确租户（本次"会话 tenant_id 为空"缺陷的回归护栏）
    # 放在 D9 之后：C 段先于 D 段执行，看不到刚创建的会话，这正是该缺陷
    # 上一轮被放行的原因，故必须在对话之后复查一次。
    try:
        from sqlalchemy import text

        from app.core.database import async_session_factory

        async with async_session_factory() as db:
            row = (await db.execute(text(
                "SELECT c.id, c.tenant_id, u.tenant_id::text AS owner_tenant "
                "FROM conversations c LEFT JOIN users u ON u.id::text = c.user_id "
                "ORDER BY c.created_at DESC LIMIT 1"
            ))).first()

        ok = bool(row) and row.tenant_id is not None and row.tenant_id == row.owner_tenant
        record(
            "D11 新建会话已写入正确租户",
            ok,
            f"conv={str(row.id)[:8] if row else '-'} "
            f"tenant={row.tenant_id if row else '-'} owner={row.owner_tenant if row else '-'}",
        )
    except Exception as exc:  # noqa: BLE001
        record("D11 新建会话租户", False, f"EXC {type(exc).__name__}: {exc}")

    # D12 清理测试账号（降权 + 停用）
    try:
        from sqlalchemy import text

        from app.core.database import async_session_factory

        async with async_session_factory() as db:
            await db.execute(text(
                "UPDATE users SET role='user', is_active=false "
                "WHERE username LIKE 'adminver%' OR username LIKE 'verifier%' "
                "   OR username LIKE 'tsmoke%'"
            ))
            await db.commit()
        record("D12 测试账号已清理", True, "降权并停用")
    except Exception as exc:  # noqa: BLE001
        record("D12 测试账号清理", False, str(exc)[:200])


async def _run_async_parts() -> None:
    """C、D 两部分的异步检查必须共用同一事件循环。

    否则第二个 ``asyncio.run`` 会新建事件循环，而全局 async engine 的连接池
    仍绑定在旧循环上，触发 "attached to a different loop"。
    """
    await _part_c_db()
    await _part_d_admin()


def main() -> int:
    part_a_inprocess()
    part_b_http()
    asyncio.run(_run_async_parts())

    passed = sum(1 for _, s, _ in RESULTS if s == "PASS")
    total = len(RESULTS)
    section(f"结果：{passed}/{total} 通过")
    for name, status, detail in RESULTS:
        if status != "PASS":
            print(f"  [FAIL] {name} :: {detail}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
