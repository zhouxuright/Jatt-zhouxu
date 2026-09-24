# -*- coding: utf-8 -*-
"""Probe 9: verify the complete download pipeline + per-category counts.

Verified flow (from browser capture):
1. POST /law-search/search/list            -> rows with bbbs
2. GET  /law-search/search/flfgDetails     -> metadata
3. GET  /law-search/download/pc?format=docx&bbbs=...  -> signed OBS url (JSON)
4. GET  signed OBS url                     -> DOCX bytes
"""
import io
import re
import zipfile

import httpx

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/152.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://flk.npc.gov.cn/detail",
    "Origin": "https://flk.npc.gov.cn",
    "Content-Type": "application/json;charset=UTF-8",
}

BASE = "https://flk.npc.gov.cn"


def payload(code_ids, page=1, size=10):
    return {
        "searchRange": 1,
        "sxrq": [],
        "gbrq": [],
        "searchType": 2,
        "sxx": [],
        "gbrqYear": [],
        "flfgCodeId": code_ids,
        "zdjgCodeId": [],
        "searchContent": "",
        "orderByParam": {"order": "-1", "sort": ""},
        "pageNum": page,
        "pageSize": size,
    }


def docx_text(data: bytes) -> str:
    zf = zipfile.ZipFile(io.BytesIO(data))
    xml = zf.read("word/document.xml").decode("utf-8")
    xml = re.sub(r"</w:p>", "\n", xml)
    text = re.sub(r"<[^>]+>", "", xml)
    return text


def main() -> None:
    with httpx.Client(headers=HEADERS, timeout=60, follow_redirects=True) as client:
        # --- 1. per-category counts (leaf codeIds only; parents don't expand) ---
        cats = {
            "宪法": [100],
            "法律": [101, 102, 110, 120, 130, 140, 150, 155, 160, 170, 180, 190, 195, 200],
            "行政法规": [201, 210, 215],
            "监察法规": [220],
            "地方法规": [221, 222, 230, 260, 270, 290, 295, 300, 305, 310],
            "司法解释": [311, 320, 330, 340, 350],
        }
        for name, ids in cats.items():
            r = client.post(f"{BASE}/law-search/search/list", json=payload(ids, 1, 1))
            j = r.json()
            print(f"{name}: total={j.get('total')}")

        # --- 2. flcaStatus enum ---
        r = client.get(f"{BASE}/law-search/search/enumData")
        enum = r.json()["data"]
        print("\nflcaStatus:", enum.get("flcaStatus"))

        # --- 3. full download verification for a LAW (法律) ---
        r = client.post(f"{BASE}/law-search/search/list", json=payload([101, 102, 110], 1, 1))
        row = r.json()["rows"][0]
        bbbs, title = row["bbbs"], row["title"]
        print(f"\n--- verify download: {title} ({bbbs}) ---")

        r3 = client.get(f"{BASE}/law-search/download/pc", params={"format": "docx", "bbbs": bbbs})
        j3 = r3.json()
        print("download/pc ->", j3.get("code"), j3.get("msg"))
        url = (j3.get("data") or {}).get("url")
        if not url:
            print("no url, abort")
            return
        print("oss url ok, fetching...")
        r4 = client.get(url)
        print(f"docx: {r4.status_code} len={len(r4.content)} pk={r4.content[:2] == b'PK'}")
        text = docx_text(r4.content)
        print("text length:", len(text))
        print("first 200 chars:", text[:200].replace("\n", " "))

        # --- 4. verify for an 行政法规 ---
        r = client.post(f"{BASE}/law-search/search/list", json=payload([210], 1, 1))
        rows = r.json().get("rows") or []
        if rows:
            row = rows[0]
            bbbs2, title2 = row["bbbs"], row["title"]
            print(f"\n--- verify 行政法规 download: {title2} ({bbbs2}) ---")
            r5 = client.get(f"{BASE}/law-search/download/pc", params={"format": "docx", "bbbs": bbbs2})
            j5 = r5.json()
            print("download/pc ->", j5.get("code"), j5.get("msg"))
            url2 = (j5.get("data") or {}).get("url")
            if url2:
                r6 = client.get(url2)
                print(f"docx: {r6.status_code} len={len(r6.content)} pk={r6.content[:2] == b'PK'}")
                if r6.content[:2] == b"PK":
                    t2 = docx_text(r6.content)
                    print("text length:", len(t2))
                    print("first 150:", t2[:150].replace("\n", " "))


if __name__ == "__main__":
    main()
