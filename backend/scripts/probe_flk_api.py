# -*- coding: utf-8 -*-
"""Probe the current flk.npc.gov.cn API surface (site has been redesigned)."""
import re
import sys

import httpx

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://flk.npc.gov.cn/",
}


def probe_js_bundle(client: httpx.Client) -> None:
    r = client.get("https://flk.npc.gov.cn/", timeout=20)
    m = re.search(r'src="(/assets/index-[^"]+\.js)"', r.text)
    if not m:
        print("no js bundle found in index html")
        return
    js_url = "https://flk.npc.gov.cn" + m.group(1)
    print("bundle:", js_url)
    r2 = client.get(js_url, timeout=40)
    print("bundle status:", r2.status_code, "length:", len(r2.text))
    apis = set(re.findall(r'["\'](/[a-zA-Z0-9_\-/]*(?:api|list|detail|search)[a-zA-Z0-9_\-/]*)["\']', r2.text))
    for a in sorted(apis)[:60]:
        print("  ", a)
    # Also look for axios baseURL patterns
    bases = set(re.findall(r'baseURL\s*[:=]\s*["\']([^"\']{1,80})["\']', r2.text))
    print("baseURLs:", bases)


def try_legacy_paths(client: httpx.Client) -> None:
    candidates = [
        ("POST", "/api/list"),
        ("GET", "/api/law/list"),
        ("POST", "/api/law/list"),
        ("GET", "/api/v1/laws"),
        ("GET", "/api/search"),
        ("POST", "/api/search"),
        ("GET", "/api/list?type=flfg"),
    ]
    for method, path in candidates:
        try:
            params = {
                "sortTr": "fffbrq_s", "searchType": "title;vague",
                "searchWord": "", "catPul": "minfa", "pcodeJie": "",
                "pcodeLei": "", "times": "", "page": 1, "pageSize": 5,
                "type": "flfg",
            }
            if method == "GET":
                r = client.get("https://flk.npc.gov.cn" + path, params=params, timeout=20)
            else:
                r = client.post("https://flk.npc.gov.cn" + path, json=params, timeout=20)
            ct = r.headers.get("content-type", "")
            is_json = "json" in ct or r.text[:1] in "{["
            print(f"{method} {path} -> {r.status_code} ct={ct[:40]} json={is_json} len={len(r.text)}")
            if is_json and r.status_code == 200:
                print("   body[:400]:", r.text[:400])
        except Exception as exc:
            print(f"{method} {path} -> ERROR {exc}")


if __name__ == "__main__":
    with httpx.Client(headers=HEADERS, follow_redirects=True) as client:
        try:
            probe_js_bundle(client)
        except Exception as exc:
            print("js probe error:", exc)
        print("-" * 60)
        try:
            try_legacy_paths(client)
        except Exception as exc:
            print("legacy probe error:", exc)
