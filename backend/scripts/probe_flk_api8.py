# -*- coding: utf-8 -*-
"""Dump the full flfgDetails response structure to find the law body text."""
import json

import httpx

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/152.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://flk.npc.gov.cn/search",
    "Origin": "https://flk.npc.gov.cn",
    "Content-Type": "application/json;charset=UTF-8",
}

LIST_PAYLOAD = {
    "searchRange": 1,
    "sxrq": [],
    "gbrq": [],
    "searchType": 2,
    "sxx": [],
    "gbrqYear": [],
    "flfgCodeId": [101, 102, 110, 120, 130, 140, 150, 155, 160, 170, 180, 190, 195, 200],
    "zdjgCodeId": [],
    "searchContent": "",
    "orderByParam": {"order": "-1", "sort": ""},
    "pageNum": 1,
    "pageSize": 5,
}


def walk(obj, prefix="", depth=0, out=None):
    if out is None:
        out = []
    if depth > 4:
        return out
    if isinstance(obj, dict):
        for k, v in obj.items():
            path = f"{prefix}.{k}" if prefix else k
            if isinstance(v, (dict, list)):
                walk(v, path, depth + 1, out)
            else:
                s = str(v)
                preview = s[:80] if len(s) > 80 else s
                out.append(f"{path} = {type(v).__name__} [{len(s)}ch] {preview}")
    elif isinstance(obj, list):
        out.append(f"{prefix} = list[{len(obj)}]")
        if obj:
            walk(obj[0], f"{prefix}[0]", depth + 1, out)
    return out


def main() -> None:
    with httpx.Client(headers=HEADERS, timeout=30, follow_redirects=True) as client:
        r = client.post("https://flk.npc.gov.cn/law-search/search/list", json=LIST_PAYLOAD)
        rows = r.json()["rows"]
        bbbs = rows[0]["bbbs"]
        title = rows[0]["title"]
        print("law:", title)

        r2 = client.get(
            "https://flk.npc.gov.cn/law-search/search/flfgDetails",
            params={"bbbs": bbbs},
        )
        data = r2.json()
        for line in walk(data):
            print(line)

        # also dump one full list row
        print("\n--- list row structure ---")
        for line in walk(rows[0]):
            print(line)


if __name__ == "__main__":
    main()
