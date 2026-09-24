# -*- coding: utf-8 -*-
"""E2E verification for P1.3 (contract lifecycle) and P1.5 (compliance tracking).

Runs against the live nginx endpoint (http://localhost/api/v1/...).
Requires only `requests` (available in backend venv) — run from host:
    python backend/scripts/e2e_p13_p15.py
"""
import sys
import time

import requests

BASE = "http://localhost/api/v1"
results = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def main() -> None:
    s = requests.Session()
    ts = int(time.time())
    username = f"e2e_p15_{ts}"
    email = f"{username}@test.local"

    r = s.post(f"{BASE}/auth/register", json={
        "username": username, "email": email, "password": "Test@12345",
    }, timeout=30)
    reg_ok = r.status_code in (200, 201)

    r = s.post(f"{BASE}/auth/login", json={
        "username": username, "password": "Test@12345",
    }, timeout=30)
    check("注册/登录测试账号", reg_ok and r.status_code == 200, f"HTTP {r.status_code}")
    body = r.json()
    token = (body.get("token") or {}).get("access_token") or body.get("access_token")
    if not token:
        print(f"!!! 无法获取 token, 中止: {str(body)[:200]}")
        sys.exit(1)
    auth = {"Authorization": f"Bearer {token}"}

    # ---------------- P1.3 合同全生命周期 ----------------
    print("\n--- P1.3 合同全生命周期闭环 ---")

    # 1. 起草（AI 生成, 持久化）
    r = s.post(f"{BASE}/contract/lifecycle/draft", headers=auth, json={
        "description": "起草一份简单的软件服务合同，甲方委托乙方开发一个小程序，服务期6个月，费用5万元",
        "contract_type": "服务合同",
    }, timeout=180)
    body = r.json() if r.status_code == 200 else {}
    check("P1.3 起草合同并保存草稿", r.status_code == 200 and body.get("contract_id"),
          f"contract_id={str(body.get('contract_id'))[:8]}, method={body.get('method')}")
    contract_id = body.get("contract_id")

    if contract_id:
        # 2. 合同列表
        r = s.get(f"{BASE}/contract/lifecycle/contracts", headers=auth, timeout=30)
        items = r.json().get("items", [])
        check("P1.3 合同列表", r.status_code == 200 and any(
            c["id"] == contract_id for c in items), f"total={r.json().get('total')}")

        # 3. 导入第二个合同（版本对比用）
        original_text = "第一条 服务内容\n甲方委托乙方开发小程序。\n第二条 服务费用\n合同总价款为人民币5万元。\n第三条 交付时间\n乙方应于2026年12月31日前交付。"
        r = s.post(f"{BASE}/contract/lifecycle/contracts", headers=auth, json={
            "title": "E2E测试-软件服务合同", "content": original_text, "contract_type": "服务合同",
        }, timeout=30)
        imp = r.json()
        check("P1.3 导入现有合同文本", r.status_code == 201 and imp.get("id"),
              f"status={imp.get('status')}")
        imp_id = imp.get("id")

        # 4. 版本对比（保存新版本）
        modified_text = "第一条 服务内容\n甲方委托乙方开发小程序及配套管理系统。\n第二条 服务费用\n合同总价款为人民币8万元。\n第三条 交付时间\n乙方应于2026年10月31日前交付。"
        if imp_id:
            r = s.post(f"{BASE}/contract/lifecycle/compare", headers=auth, json={
                "original": original_text, "modified": modified_text,
                "original_label": "v1", "modified_label": "v2",
                "contract_id": imp_id,
            }, timeout=120)
            cmp_body = r.json()
            check("P1.3 版本对比+保存新版本", r.status_code == 200 and cmp_body.get("saved_version") == 2,
                  f"additions={cmp_body.get('additions')}, deletions={cmp_body.get('deletions')}")

        # 5. 提取关键日期（持久化）
        if imp_id:
            r = s.post(f"{BASE}/contract/lifecycle/extract-dates", headers=auth, json={
                "contract_text": modified_text, "contract_id": imp_id,
            }, timeout=120)
            dates = r.json().get("dates", [])
            check("P1.3 提取合同关键日期", r.status_code == 200 and len(dates) > 0,
                  f"提取 {len(dates)} 个日期, saved={bool(r.json().get('saved_to_contract'))}")

        # 6. 审查合同（链接审查结果）
        r = s.post(f"{BASE}/contract/lifecycle/contracts/{imp_id}/review", headers=auth, timeout=300)
        rev = r.json()
        check("P1.3 审查合同并链接结果", r.status_code == 200 and rev.get("review_id")
              and rev.get("contract_status") == "reviewed",
              f"risk_score={rev.get('risk_score')}")

        # 7. 合同详情（版本历史 + 审查 + 关键日期）
        r = s.get(f"{BASE}/contract/lifecycle/contracts/{imp_id}", headers=auth, timeout=30)
        detail = r.json()
        check("P1.3 合同详情（版本/日期/审查）", r.status_code == 200
              and len(detail.get("versions", [])) == 2
              and detail.get("latest_review") is not None
              and len(detail.get("key_dates", [])) > 0,
              f"versions={len(detail.get('versions', []))}, key_dates={len(detail.get('key_dates', []))}")

        # 8. 归档
        r = s.post(f"{BASE}/contract/lifecycle/contracts/{imp_id}/archive",
                   headers=auth, json={}, timeout=30)
        arc = r.json()
        check("P1.3 归档合同", r.status_code == 200 and arc.get("status") == "archived",
              f"archived_at={str(arc.get('archived_at'))[:19]}")

        # 9. 删除测试合同
        r = s.delete(f"{BASE}/contract/lifecycle/contracts/{imp_id}", headers=auth, timeout=30)
        check("P1.3 删除合同（级联清理）", r.status_code == 200)

    # ---------------- P1.5 合规风险动态跟踪 ----------------
    print("\n--- P1.5 合规风险动态跟踪 ---")

    # 1. 创建监控配置
    r = s.post(f"{BASE}/compliance/watchlists", headers=auth, json={
        "name": "E2E数据合规监控", "industry": "互联网科技",
        "topics": ["个人信息", "数据", "商标"],
        "compliance_domains": ["data_privacy", "ip"],
        "enabled": True,
    }, timeout=30)
    wl = r.json()
    check("P1.5 创建监控配置", r.status_code == 201 and wl.get("id"), f"id={str(wl.get('id'))[:8]}")
    wl_id = wl.get("id")

    # 2. 监控配置列表
    r = s.get(f"{BASE}/compliance/watchlists", headers=auth, timeout=30)
    check("P1.5 监控配置列表", r.status_code == 200 and len(r.json().get("items", [])) >= 1,
          f"items={len(r.json().get('items', []))}")

    # 3. 更新监控配置（停用再启用）
    if wl_id:
        r = s.put(f"{BASE}/compliance/watchlists/{wl_id}", headers=auth, json={"enabled": False}, timeout=30)
        check("P1.5 更新监控配置", r.status_code == 200 and r.json().get("enabled") is False)
        s.put(f"{BASE}/compliance/watchlists/{wl_id}", headers=auth, json={"enabled": True}, timeout=30)

    # 4. 立即扫描（FLK 同步 + 匹配 + 告警）
    r = s.post(f"{BASE}/compliance/scan", headers=auth, params={"days_back": 90}, timeout=300)
    scan = r.json()
    check("P1.5 立即扫描法规动态", r.status_code == 200
          and "changes_synced" in scan and "alerts_created" in scan,
          f"synced={scan.get('changes_synced')}, alerts={scan.get('alerts_created')}")

    # 5. 法规变更列表
    r = s.get(f"{BASE}/compliance/regulation-changes", headers=auth,
              params={"days_back": 365, "page_size": 5}, timeout=30)
    ch = r.json()
    check("P1.5 近期法规变更列表", r.status_code == 200 and ch.get("total", 0) > 0,
          f"total={ch.get('total')}, sample={(ch.get('items') or [{}])[0].get('title', 'N/A')[:30]}")

    # 6. 告警列表
    r = s.get(f"{BASE}/compliance/alerts", headers=auth, timeout=30)
    alerts = r.json()
    n_alerts = len(alerts.get("items", []))
    check("P1.5 告警列表", r.status_code == 200,
          f"total={alerts.get('total')}")
    if n_alerts > 0:
        a = alerts["items"][0]
        check("P1.5 告警含 LLM 影响分析", bool(a.get("analysis")),
              f"risk={a.get('risk_level')}, kw={a.get('matched_keyword')}")
        # 7. 确认告警
        r = s.post(f"{BASE}/compliance/alerts/{a['id']}/ack", headers=auth, timeout=30)
        check("P1.5 确认告警", r.status_code == 200 and r.json().get("status") == "acknowledged")
    else:
        check("P1.5 告警含 LLM 影响分析", False, "无告警生成（检查扫描匹配）")

    # 8. 删除监控配置
    if wl_id:
        r = s.delete(f"{BASE}/compliance/watchlists/{wl_id}", headers=auth, timeout=30)
        check("P1.5 删除监控配置", r.status_code == 200)

    # ---------------- 结果汇总 ----------------
    print("\n" + "=" * 50)
    passed = sum(1 for _, ok, _ in results if ok)
    print(f"E2E 结果: {passed}/{len(results)} 通过")
    for name, ok, _ in results:
        if not ok:
            print(f"  FAILED: {name}")
    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    main()
