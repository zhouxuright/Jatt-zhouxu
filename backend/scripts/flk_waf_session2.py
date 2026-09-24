# -*- coding: utf-8 -*-
"""提交验证后: 更新 state 中的 cid 并测试 API。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, "/app")

import httpx

NEW_CID = sys.argv[1]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/152.0.0.0 Safari/537.36",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": "https://flk.npc.gov.cn/",
}

with httpx.Client(
    headers=HEADERS, timeout=30, follow_redirects=True,
    cookies={"wzws_cid": NEW_CID},
) as c:
    r = c.get(
        "https://flk.npc.gov.cn/law-search/download/pc",
        params={"format": "docx", "bbbs": "ff8081817918fa96017920845025033c"},
        headers={"Accept": "application/json, text/plain, */*",
                 "Referer": "https://flk.npc.gov.cn/detail",
                 "Origin": "https://flk.npc.gov.cn"},
    )
    ct = r.headers.get("content-type", "?")
    print(f"api test: status={r.status_code} ct={ct}")
    if "json" in ct:
        print(f"JSON: {r.text[:300]}")
        print("UNBLOCKED!")
    else:
        print(f"still blocked: {''.join(r.text[:200].split())}")
