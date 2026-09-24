# -*- coding: utf-8 -*-
"""诊断 monitor 的 FLK 同步：WAF 状态 + 搜索 API 返回数据的日期分布。"""
import asyncio
import json
import sys
from collections import Counter

sys.path.insert(0, "/app")
sys.path.insert(0, "/tmp")
sys.path.insert(0, "/tmp/ddddocr_lib")

import httpx

FLK_BASE = "https://flk.npc.gov.cn"
SEARCH_API = f"{FLK_BASE}/law-search/search/list"

FLK_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/152.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://flk.npc.gov.cn/search",
    "Origin": "https://flk.npc.gov.cn",
    "Content-Type": "application/json;charset=UTF-8",
}

FLK_CATEGORIES = {
    "法律": [101, 102, 110, 120, 130, 140, 150, 155, 160, 170, 180, 190, 195, 200],
    "行政法规": [201, 210, 215],
    "监察法规": [220],
    "司法解释": [311, 320, 330, 340, 350],
}


def payload(code_ids, page, size):
    return {
        "searchRange": 1, "sxrq": [], "gbrq": [], "searchType": 2, "sxx": [],
        "gbrqYear": [], "flfgCodeId": code_ids, "zdjgCodeId": [],
        "searchContent": "",
        "orderByParam": {"order": "-1", "sort": ""},
        "pageNum": page, "pageSize": size,
    }


async def main():
    from flk_waf_solver import solve_waf

    cookies = None
    async with httpx.AsyncClient(headers=FLK_HEADERS, timeout=30,
                                 follow_redirects=True) as client:
        for attempt in range(3):
            if cookies:
                client.cookies.update(cookies)
            resp = await client.post(SEARCH_API, json=payload(FLK_CATEGORIES["法律"], 1, 50))
            ct = resp.headers.get("content-type", "")
            if "json" in ct:
                break
            print(f"attempt {attempt}: challenged ({ct}), solving WAF...")
            cid = await asyncio.to_thread(solve_waf)
            cookies = {"wzws_cid": cid}
            print(f"solved, cid={cid[:16]}...")

        data = resp.json()
        rows = data.get("rows") or []
        print(f"\nfetched {len(rows)} rows (法律 page 1)")
        print(f"resultData keys: {list(data.keys())}")
        if rows:
            print(f"first row: {json.dumps(rows[0], ensure_ascii=False)[:200]}")
            dates = sorted((r.get("gbrq") or "?") for r in rows)
            print(f"gbrq range: {dates[0]} .. {dates[-1]}")
            years = Counter((r.get("gbrq") or "?")[:4] for r in rows)
            print(f"years: {dict(sorted(years.items(), reverse=True)[:8])}")
            # 最近90天内
            recent = [r for r in rows if (r.get("gbrq") or "") >= "2026-06-13"]
            print(f"rows in last 90 days: {len(recent)}")
            for r in recent[:10]:
                print(f"  {r.get('gbrq')} {r.get('title', '')[:40]} (sxx={r.get('sxx')})")


asyncio.run(main())
