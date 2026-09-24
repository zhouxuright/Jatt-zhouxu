# -*- coding: utf-8 -*-
"""Probe the new /law-search/* endpoints on flk.npc.gov.cn."""
import json

import httpx

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://flk.npc.gov.cn/",
    "Origin": "https://flk.npc.gov.cn",
    "Content-Type": "application/json",
}

BASE_CANDIDATES = [
    "https://flk.npc.gov.cn",
    "https://flk.npc.gov.cn/api",
    "https://api.flk.npc.gov.cn",
]


def try_list(base: str, client: httpx.Client) -> dict | None:
    payloads = [
        {"page": 1, "pageSize": 10, "searchWord": "", "sortTr": "fbrq_s;desc", "type": "flfg"},
        {"page": 1, "size": 10, "keyword": "", "type": "flfg"},
        {"pageNum": 1, "pageSize": 10, "searchType": "title;vague", "searchWord": "", "catPul": "minfa", "sortTr": "fffbrq_s;desc"},
        {},
    ]
    for i, payload in enumerate(payloads):
        try:
            r = client.post(f"{base}/law-search/search/list", json=payload, timeout=20)
            ct = r.headers.get("content-type", "")
            print(f"  POST {base}/law-search/search/list payload#{i} -> {r.status_code} ct={ct[:30]} len={len(r.text)}")
            if r.status_code == 200 and ("json" in ct or r.text[:1] in "{["):
                print("    body[:500]:", r.text[:500])
                return r.json() if r.text[:1] in "{[" else None
        except Exception as exc:
            print(f"  POST {base}/law-search/search/list payload#{i} -> ERROR {type(exc).__name__}: {str(exc)[:120]}")
    return None


def try_fljc(base: str, client: httpx.Client) -> None:
    """Try the classification (fljc = 法律检索?) endpoint which may list categories."""
    for method in ("GET", "POST"):
        try:
            if method == "GET":
                r = client.get(f"{base}/law-search/index/fljc", timeout=20)
            else:
                r = client.post(f"{base}/law-search/index/fljc", json={}, timeout=20)
            ct = r.headers.get("content-type", "")
            print(f"  {method} {base}/law-search/index/fljc -> {r.status_code} ct={ct[:30]} len={len(r.text)}")
            if r.status_code == 200 and r.text[:1] in "{[":
                print("    body[:500]:", r.text[:500])
                return
        except Exception as exc:
            print(f"  {method} {base}/law-search/index/fljc -> ERROR {str(exc)[:120]}")


if __name__ == "__main__":
    with httpx.Client(headers=HEADERS, follow_redirects=True) as client:
        for base in BASE_CANDIDATES:
            print(f"BASE: {base}")
            data = try_list(base, client)
            try_fljc(base, client)
            if data:
                print("SUCCESS with base:", base)
                break
