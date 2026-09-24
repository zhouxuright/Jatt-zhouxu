# -*- coding: utf-8 -*-
"""E2E verification for P1.6 (multi-agent orchestration).

Verifies against the live nginx endpoint (http://localhost/api/v1/...):
1. GET  /collaboration/patterns        — 4 collaboration patterns exposed
2. POST /collaboration/analyze          — full pipeline: LLM classification -> agents -> synthesis
3. POST /collaboration/analyze (explicit context) — deterministic override path
4. POST /chat/chat/stream (enable_multi_agent)    — chat pipeline integration

Requires only `requests` — run from host:
    python backend/scripts/e2e_p16_multiagent.py
"""
import json
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
    username = f"e2e_p16_{ts}"
    email = f"{username}@test.local"

    r = s.post(f"{BASE}/auth/register", json={
        "username": username, "email": email, "password": "Test@12345",
    }, timeout=30)
    r = s.post(f"{BASE}/auth/login", json={
        "username": username, "password": "Test@12345",
    }, timeout=30)
    check("注册/登录测试账号", r.status_code == 200, f"HTTP {r.status_code}")
    body = r.json()
    token = (body.get("token") or {}).get("access_token") or body.get("access_token")
    if not token:
        print(f"!!! 无法获取 token, 中止: {str(body)[:200]}")
        sys.exit(1)
    auth = {"Authorization": f"Bearer {token}"}

    # ---------------- 1. 协作模式列表 ----------------
    print("\n--- 1. 协作模式列表 ---")
    r = s.get(f"{BASE}/collaboration/patterns", timeout=30)
    data = r.json() if r.status_code == 200 else {}
    pattern_ids = {p.get("id") for p in data.get("patterns", [])}
    check("GET /collaboration/patterns 返回4种模式",
          r.status_code == 200 and pattern_ids == {"parallel", "sequential", "iterative", "hierarchical"},
          f"HTTP {r.status_code}, patterns={sorted(pattern_ids)}")

    # ---------------- 2. 协作分析（LLM 自动分类） ----------------
    print("\n--- 2. 多智能体协作分析（LLM 自动分类） ---")
    query = ("互联网公司裁员20人，涉及劳动合同解除、经济补偿金计算、"
             "竞业限制协议效力，以及被裁员工个人信息处理的合规问题，请综合分析法律风险")
    t0 = time.time()
    r = s.post(f"{BASE}/collaboration/analyze", headers=auth, json={
        "query": query,
    }, timeout=290)
    elapsed = time.time() - t0
    ok = r.status_code == 200
    data = r.json() if ok else {}
    agents = data.get("agents_involved", [])
    pattern = data.get("collaboration_pattern", "")
    resp_len = len(data.get("final_response", ""))
    check("POST /collaboration/analyze 自动分类并执行", ok and len(agents) >= 2 and resp_len > 500,
          f"HTTP {r.status_code}, pattern={pattern}, agents={agents}, "
          f"len={resp_len}, {elapsed:.0f}s")
    check("协作报告包含法律声明", ok and "法律声明" in data.get("final_response", ""))
    check("协作报告在300s超时内完成", ok and elapsed < 290, f"{elapsed:.0f}s")

    # ---------------- 3. 协作分析（显式 context 覆盖） ----------------
    print("\n--- 3. 多智能体协作分析（显式 context 覆盖） ---")
    t0 = time.time()
    r = s.post(f"{BASE}/collaboration/analyze", headers=auth, json={
        "query": "用人单位违法解除劳动合同的法律后果有哪些？",
        "context": {
            "required_agents": ["law_retrieval", "legal_consult"],
            "collaboration_pattern": "sequential",
        },
    }, timeout=290)
    elapsed = time.time() - t0
    ok = r.status_code == 200
    data = r.json() if ok else {}
    check("显式 context 确定性执行（sequential 检索→咨询）",
          ok and data.get("collaboration_pattern") == "sequential"
          and set(data.get("agents_involved", [])) >= {"law_retrieval", "legal_consult"},
          f"HTTP {r.status_code}, pattern={data.get('collaboration_pattern')}, "
          f"agents={data.get('agents_involved')}, {elapsed:.0f}s")

    # ---------------- 4. Chat 流式管线多智能体开关 ----------------
    print("\n--- 4. Chat 流式管线 enable_multi_agent ---")
    t0 = time.time()
    r = s.post(f"{BASE}/chat/chat/stream", headers=auth, json={
        "message": "我是电商卖家，客户恶意差评并威胁投诉，同时供应商延迟交货要违约，我应该怎么应对？",
        "enable_multi_agent": True,
    }, stream=True, timeout=290)
    ok = r.status_code == 200
    meta = {}
    tokens = 0
    done_content = ""
    if ok:
        for line in r.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data: "):
                continue
            try:
                event = json.loads(line[6:])
            except json.JSONDecodeError:
                continue
            etype = event.get("type")
            if etype == "meta":
                meta = event.get("data", {})
            elif etype == "token":
                tokens += 1
            elif etype == "done":
                done_content = event.get("content", "")
    elapsed = time.time() - t0

    ma_meta = meta.get("multi_agent", {})
    features = meta.get("features_used", {})
    check("POST /chat/stream (enable_multi_agent) 流式返回",
          ok and tokens > 50 and len(done_content) > 500,
          f"HTTP {r.status_code}, tokens={tokens}, content_len={len(done_content)}, {elapsed:.0f}s")
    check("meta 标记 multi_agent 特性已启用", features.get("multi_agent") is True,
          f"features_used={features}")
    check("meta 包含多智能体执行详情（pattern + agents）",
          bool(ma_meta.get("pattern")) and len(ma_meta.get("agents", [])) >= 1,
          f"multi_agent={ma_meta}")
    check("流式回复包含实质法律内容", "法" in done_content or "合同" in done_content)

    # ---------------- 总结 ----------------
    print("\n" + "=" * 60)
    passed = sum(1 for _, ok_, _ in results if ok_)
    total = len(results)
    print(f"总计: {passed}/{total} 通过")
    for name, ok_, detail in results:
        if not ok_:
            print(f"  FAIL: {name} — {detail}")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
