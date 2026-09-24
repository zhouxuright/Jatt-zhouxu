# -*- coding: utf-8 -*-
"""测试：代理轮换能否绕过 FLK WAF；直接连接是否仍被拦截。"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, "/app")

import httpx

POOL = "http://proxy_pool:5010"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/152.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://flk.npc.gov.cn/detail",
    "Origin": "https://flk.npc.gov.cn",
}

BBBS = "ff8081817918"  # a known failed one (truncated? use full from checkpoint)
cp = json.load(open("/tmp/flk_download_checkpoint.json", encoding="utf-8"))
failed_items = list(cp["failed"].items())
BBBS_FULL = failed_items[0][0]
print(f"testing bbbs={BBBS_FULL} title={failed_items[0][1]['title'][:30]}")

URL = "https://flk.npc.gov.cn/law-search/download/pc"


def try_request(client, label):
    try:
        r = client.get(URL, params={"format": "docx", "bbbs": BBBS_FULL}, timeout=20)
        ct = r.headers.get("content-type", "?")
        if "json" in ct:
            j = r.json()
            url = (j.get("data") or {}).get("url")
            return f"{label}: JSON ok, url={'yes' if url else 'no-url ' + str(j.get('msg'))[:50]}"
        return f"{label}: BLOCKED ({ct}, len={len(r.text)})"
    except Exception as exc:
        return f"{label}: ERROR {type(exc).__name__}: {str(exc)[:80]}"


# 1. direct
with httpx.Client(headers=HEADERS, timeout=20, follow_redirects=True) as c:
    print(try_request(c, "direct"))

# 2. via proxies (test 8 CN + others)
with httpx.Client(timeout=5) as pc:
    try:
        r = pc.get(f"{POOL}/all/")
        proxies = r.json()
    except Exception as exc:
        proxies = []
        print(f"pool fetch error: {exc}")

print(f"pool size: {len(proxies)}")
cn = [p["proxy"] for p in proxies if p.get("region") == "CN"]
others = [p["proxy"] for p in proxies if p.get("region") != "CN"]
test_list = (cn[:5] + others[:5])[:10]
print(f"CN proxies: {len(cn)}, testing {len(test_list)}")

ok_count = 0
for px in test_list:
    with httpx.Client(
        headers=HEADERS, timeout=20, follow_redirects=True,
        proxy=f"http://{px}",
    ) as c:
        result = try_request(c, f"proxy {px}")
        print(result)
        if "JSON ok" in result:
            ok_count += 1
    time.sleep(0.3)

print(f"\nproxies returning JSON: {ok_count}/{len(test_list)}")
