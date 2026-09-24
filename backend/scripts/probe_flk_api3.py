# -*- coding: utf-8 -*-
"""Extract request parameter shapes for /law-search/search/list from the JS bundle."""
import re

import httpx

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0"}

client = httpx.Client(headers=HEADERS, timeout=40)
js = client.get("https://flk.npc.gov.cn/assets/index-Y9B5oxpu.js").text

# Locate occurrences of search/list and print surrounding context
for m in re.finditer(r'search/list', js):
    start = max(0, m.start() - 600)
    end = min(len(js), m.end() + 900)
    chunk = js[start:end]
    print("=" * 80)
    print(chunk)
    print()

# Look for the request payload keys the frontend sends, e.g. objects with
# keys like "searchWord", "catPul", "sortTr", "pageSize", "page", "type"
print("#" * 80)
for key in ("catPul", "sortTr", "searchWord", "pcodeJie", "pcodeLei", "times", "gbrqStart", "sxrqStart", "flfgCx"):
    hits = [m.start() for m in re.finditer(re.escape(key), js)]
    print(f"{key}: {len(hits)} hits")
    if hits:
        ctx = js[max(0, hits[0] - 150): hits[0] + 200]
        print("   ctx:", ctx.replace("\n", " ")[:350])
