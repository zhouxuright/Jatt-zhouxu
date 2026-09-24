# -*- coding: utf-8 -*-
"""Inspect flk_rows.json fields and one details response."""
import json

import httpx

with open("/tmp/flk_rows.json", encoding="utf-8") as f:
    rows = json.load(f)

print("=== row keys ===")
print(json.dumps(rows[0], ensure_ascii=False, indent=1))
print("\n=== sxx distribution ===")
from collections import Counter
print(Counter(r.get("sxx") for r in rows))

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/152.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://flk.npc.gov.cn/detail",
    "Origin": "https://flk.npc.gov.cn",
}

with httpx.Client(headers=HEADERS, timeout=30, follow_redirects=True) as client:
    bbbs = rows[0]["bbbs"]
    r = client.get(
        "https://flk.npc.gov.cn/law-search/search/flfgDetails",
        params={"bbbs": bbbs},
    )
    d = r.json().get("data", {})
    print("\n=== details keys ===")
    for k, v in d.items():
        if isinstance(v, (str, int, float)) or v is None:
            s = str(v)
            print(f"{k} = {s[:60]}")
        else:
            print(f"{k} = {type(v).__name__} {str(v)[:100]}")
