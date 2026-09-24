# -*- coding: utf-8 -*-
"""Find call sites of the search/list wrapper to learn the payload keys."""
import re

import httpx

HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0"}

client = httpx.Client(headers=HEADERS, timeout=40)
js = client.get("https://flk.npc.gov.cn/assets/index-Y9B5oxpu.js").text

# search/list wrapper is J_e in this build
for fname in ("J_e", "Q_e"):
    print("#" * 30, fname, "call sites", "#" * 30)
    for m in re.finditer(re.escape(fname) + r"\(", js):
        start = max(0, m.start() - 700)
        end = min(len(js), m.end() + 400)
        chunk = js[start:end]
        # Only print chunks that look like real calls with object args
        if "{" in js[m.end(): m.end() + 400]:
            print("-" * 100)
            print(chunk)
            print()

# Also find enumData usage - GET endpoint returning enums, likely needed to build queries
print("#" * 30, "search around 'enumData'", "#" * 30)
m = re.search(r'enumData', js)
if m:
    # find the axios baseURL definition near Ar
    for mm in re.finditer(r'baseURL', js):
        print(js[max(0, mm.start() - 200): mm.end() + 200])
        print("---")
