# -*- coding: utf-8 -*-
"""Probe 11: enumerate ALL law rows for the target categories, dump sxx stats
and per-sxx title samples, so we can nail down the status mapping."""
import json
from collections import defaultdict

import httpx

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/152.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://flk.npc.gov.cn/search",
    "Origin": "https://flk.npc.gov.cn",
    "Content-Type": "application/json;charset=UTF-8",
}
BASE = "https://flk.npc.gov.cn"

CATS = {
    "宪法": [100],
    "法律": [101, 102, 110, 120, 130, 140, 150, 155, 160, 170, 180, 190, 195, 200],
    "行政法规": [201, 210, 215],
    "监察法规": [220],
    "司法解释": [311, 320, 330, 340, 350],
}


def payload(code_ids, page=1, size=50):
    return {
        "searchRange": 1, "sxrq": [], "gbrq": [], "searchType": 2, "sxx": [],
        "gbrqYear": [], "flfgCodeId": code_ids, "zdjgCodeId": [],
        "searchContent": "", "orderByParam": {"order": "-1", "sort": ""},
        "pageNum": page, "pageSize": size,
    }


def main() -> None:
    all_rows = []
    with httpx.Client(headers=HEADERS, timeout=60, follow_redirects=True) as client:
        for cat, ids in CATS.items():
            page = 1
            while True:
                r = client.post(f"{BASE}/law-search/search/list", json=payload(ids, page, 50))
                j = r.json()
                rows = j.get("rows") or []
                total = j.get("total", 0)
                if not rows:
                    break
                for row in rows:
                    row["_cat"] = cat
                all_rows.extend(rows)
                if len(all_rows) >= total if cat == "宪法" else False:
                    pass
                if page * 50 >= total:
                    break
                page += 1
            print(f"{cat}: accumulated {len(all_rows)} (total says {total})")

    print(f"\nTOTAL rows: {len(all_rows)}")
    dist = defaultdict(list)
    for row in all_rows:
        dist[row.get("sxx")].append(row["title"])

    print("\nsxx -> count, samples:")
    for sxx, titles in sorted(dist.items(), key=lambda kv: str(kv[0])):
        print(f"\nsxx={sxx}: {len(titles)}")
        for t in titles[:6]:
            print(f"   {t}")

    with open("/tmp/flk_rows.json", "w", encoding="utf-8") as f:
        json.dump(all_rows, f, ensure_ascii=False)
    print("\nsaved /tmp/flk_rows.json")


if __name__ == "__main__":
    main()
