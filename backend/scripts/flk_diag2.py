# -*- coding: utf-8 -*-
"""获取 FLK WAF 挑战页完整内容，判断挑战类型。"""
import sys

sys.path.insert(0, "/app")

import httpx

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/152.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://flk.npc.gov.cn/detail",
    "Origin": "https://flk.npc.gov.cn",
}

with httpx.Client(headers=HEADERS, timeout=30, follow_redirects=True) as client:
    r = client.get(
        "https://flk.npc.gov.cn/law-search/download/pc",
        params={"format": "docx", "bbbs": "ff8081817918"},
    )
    print("=== FULL CHALLENGE PAGE ===")
    print(r.text)
    print("=== RESPONSE HEADERS ===")
    for k, v in r.headers.items():
        print(f"{k}: {v}")
