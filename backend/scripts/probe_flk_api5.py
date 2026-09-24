# -*- coding: utf-8 -*-
"""Query the new FLK enumData endpoint and try search/list with informed payloads."""
import json

import httpx

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://flk.npc.gov.cn/",
    "Origin": "https://flk.npc.gov.cn",
    "Content-Type": "application/json;charset=UTF-8",
}


def main() -> None:
    with httpx.Client(headers=HEADERS, timeout=30, follow_redirects=True) as client:
        # 1. enumData
        r = client.get("https://flk.npc.gov.cn/law-search/search/enumData")
        print("enumData ->", r.status_code, "len", len(r.text))
        if r.status_code == 200:
            try:
                data = r.json()
                print(json.dumps(data, ensure_ascii=False, indent=1)[:3000])
            except Exception as exc:
                print("json error:", exc, r.text[:200])
        # 2. recommend (GET? POST?)
        for method in ("GET", "POST"):
            try:
                if method == "GET":
                    r2 = client.get("https://flk.npc.gov.cn/law-search/search/recommend")
                else:
                    r2 = client.post("https://flk.npc.gov.cn/law-search/search/recommend", json={})
                print(f"\nrecommend {method} -> {r2.status_code} len={len(r2.text)}")
                if r2.status_code == 200 and r2.text[:1] in "{[":
                    print(r2.text[:800])
                    break
            except Exception as exc:
                print(f"recommend {method} error:", str(exc)[:100])
        # 3. aggregateData (index stats)
        try:
            r3 = client.get("https://flk.npc.gov.cn/law-search/index/aggregateData")
            print("\naggregateData ->", r3.status_code, r3.text[:500])
        except Exception as exc:
            print("aggregateData error:", str(exc)[:100])


if __name__ == "__main__":
    main()
