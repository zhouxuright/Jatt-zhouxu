# -*- coding: utf-8 -*-
"""E2E verification for P1.4 (litigation support deepening).

Verifies against the live nginx endpoint (http://localhost/api/v1/...):
1. POST /litigation/similar-cases — semantic retrieval + code-side statistics + LLM comparison
2. POST /litigation/predict       — auto-retrieves similar cases when absent
3. POST /litigation/report        — one-click full report (输出成文), all 6 sections + disclaimer
4. POST /litigation/evidence      — legacy regression
5. POST /litigation/costs         — legacy regression

Requires only `requests` — run from host:
    python backend/scripts/e2e_p14_litigation.py
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
    username = f"e2e_p14_{ts}"
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

    case_desc = (
        "本人经营建材生意，2024年3月与某装修公司签订《建材购销合同》，约定分三批供货"
        "总货款58万元，交货后30日内付款。本人已按约交付全部货物并由对方签收，"
        "但对方仅支付20万元，剩余38万元经多次催告至今未付。现拟起诉追讨欠款及违约金。"
    )

    # ---------------- 1. 类案检索与比对 ----------------
    print("\n--- 1. 类案检索与比对 (/litigation/similar-cases) ---")
    t0 = time.time()
    r = s.post(f"{BASE}/litigation/similar-cases", headers=auth, json={
        "case_description": case_desc,
        "top_k": 5,
    }, timeout=290)
    elapsed = time.time() - t0
    data = r.json() if r.status_code == 200 else {}
    cases = data.get("similar_cases", [])
    stats = data.get("statistics", {})
    comparison = data.get("comparison", {})
    check("类案检索返回相似案例",
          r.status_code == 200 and len(cases) >= 1,
          f"HTTP {r.status_code}, {len(cases)} 条, {elapsed:.0f}s")
    check("类案含结构化字段（案号/法院/判决结果/相似度）",
          len(cases) >= 1 and all(
              k in cases[0] for k in ("case_number", "court_name", "judgment_result", "relevance_score", "outcome_bucket")),
          f"首条: {cases[0].get('case_number', '') if cases else 'N/A'} / 相似度 {cases[0].get('relevance_score', 0) if cases else 0}")
    check("代码统计的结果分布（outcome_distribution）",
          bool(stats.get("outcome_distribution")) and stats.get("total_cases", 0) >= 1,
          f"分布: {json.dumps(stats.get('outcome_distribution', {}), ensure_ascii=False)}")
    check("LLM 比对分析（共同点/差异点/策略启示）",
          isinstance(comparison, dict) and (
              comparison.get("fact_commonalities") or comparison.get("fact_differences")
              or comparison.get("strategy_implications")),
          f"共同点 {len(comparison.get('fact_commonalities', []))} 条 / 差异点 {len(comparison.get('fact_differences', []))} 条")

    # ---------------- 2. 裁判预测（自动类案检索） ----------------
    print("\n--- 2. 裁判预测 — 自动类案检索 (/litigation/predict) ---")
    t0 = time.time()
    r = s.post(f"{BASE}/litigation/predict", headers=auth, json={
        "case_description": case_desc,
    }, timeout=290)
    elapsed = time.time() - t0
    data = r.json() if r.status_code == 200 else {}
    check("未传类案时自动检索并预测",
          r.status_code == 200 and data.get("win_probability") and data.get("similar_cases_retrieved", 0) >= 1,
          f"HTTP {r.status_code}, 检索 {data.get('similar_cases_retrieved', 0)} 条, {elapsed:.0f}s")
    check("预测附带免责声明",
          "disclaimer" in data or "仅供参考" in str(data.get("win_probability", "")),
          "disclaimer 存在")

    # ---------------- 3. 一键诉讼分析报告 ----------------
    print("\n--- 3. 一键诉讼分析报告 (/litigation/report) ---")
    t0 = time.time()
    r = s.post(f"{BASE}/litigation/report", headers=auth, json={
        "case_description": case_desc,
        "evidence_list": "《建材购销合同》原件、三批送货单及签收记录、微信催款聊天记录、银行转账流水",
        "claims": "请求支付剩余货款38万元及逾期付款违约金",
        "party": "plaintiff",
        "top_k": 5,
    }, timeout=290)
    elapsed = time.time() - t0
    data = r.json() if r.status_code == 200 else {}
    report = data.get("report_markdown", "")
    check("报告生成成功（300s 内）",
          r.status_code == 200 and len(report) > 1000,
          f"HTTP {r.status_code}, 报告 {len(report)} 字, 耗时 {elapsed:.0f}s")
    sections = ["一、案件基本情况与法律分析", "二、证据清单", "三、类案比对分析",
                "四、裁判预测", "五、诉讼策略与风险提示", "六、数据来源与声明"]
    check("报告包含全部六个章节",
          all(sec in report for sec in sections),
          f"缺失: {[sec for sec in sections if sec not in report]}")
    check("报告包含免责声明与数据来源",
          "免责声明" in report and "本地裁判文书库" in report,
          "声明齐全")
    check("报告含法条引用与胜诉评估",
          "《" in report and ("胜诉概率" in report or "胜诉评估" in report),
          "法条 + 胜诉评估存在")
    check("报告含类案统计（结果分布）",
          "结果分布" in report,
          "统计嵌入报告")

    # ---------------- 4. 旧端点回归 ----------------
    print("\n--- 4. 旧端点回归 ---")
    r = s.post(f"{BASE}/litigation/evidence", headers=auth, json={
        "case_description": case_desc,
        "cause_of_action": "买卖合同纠纷",
    }, timeout=120)
    data = r.json() if r.status_code == 200 else {}
    groups = data.get("evidence_groups", [])
    check("证据清单（回归）",
          r.status_code == 200 and len(groups) >= 1,
          f"HTTP {r.status_code}, {len(groups)} 组证据")

    r = s.post(f"{BASE}/litigation/costs", headers=auth, json={
        "claim_amount": 380000, "case_type": "civil",
    }, timeout=30)
    data = r.json() if r.status_code == 200 else {}
    check("费用计算（回归）",
          r.status_code == 200 and "court_fee" in data,
          f"HTTP {r.status_code}, 受理费 {data.get('court_fee')}")

    # ---------------- summary ----------------
    print("\n" + "=" * 60)
    passed = sum(1 for _, ok, _ in results if ok)
    print(f"总计: {passed}/{len(results)} 通过")
    if passed < len(results):
        sys.exit(1)


if __name__ == "__main__":
    main()
