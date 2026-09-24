# -*- coding: utf-8 -*-
"""Verify the full FLK v2 pipeline from the container:
1. search/list (POST) with the real payload captured from the browser
2. flfgDetails (GET) for metadata + ossWordPath
3. download/pc (GET) for the DOCX body
"""
import io
import json
import zipfile

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


def main() -> None:
    with httpx.Client(headers=HEADERS, timeout=30, follow_redirects=True) as client:
        # 1. list
        r = client.post("https://flk.npc.gov.cn/law-search/search/list", json=LIST_PAYLOAD)
        print("list ->", r.status_code)
        data = r.json()
        print("total:", data.get("total"), "rows:", len(data.get("rows", [])))
        row = data["rows"][0]
        bbbs = row["bbbs"]
        print("first law:", row["title"], "bbbs:", bbbs)

        # 2. details
        r2 = client.get(
            "https://flk.npc.gov.cn/law-search/search/flfgDetails",
            params={"bbbs": bbbs},
        )
        print("\ndetails ->", r2.status_code)
        d = r2.json().get("data", {})
        oss = d.get("ossFile", {}) or {}
        word_path = oss.get("ossWordPath")
        print("title:", d.get("title"), "gbrq:", d.get("gbrq"), "sxrq:", d.get("sxrq"), "sxx:", d.get("sxx"))
        print("zdjg:", d.get("zdjgName"), "flxz:", d.get("flxz"))
        print("ossWordPath:", word_path)

        if not word_path:
            print("no word path, stop")
            return

        # 3. try download endpoints
        candidates = [
            ("GET", "https://flk.npc.gov.cn/law-search/download/pc", {"filePath": word_path}),
            ("GET", "https://flk.npc.gov.cn/law-search/download/pc", {"filePath": word_path, "bbbs": bbbs}),
            ("GET", "https://flk.npc.gov.cn/law-search/amazonFile/previewLink", {"filePath": word_path}),
        ]
        for method, url, params in candidates:
            try:
                r3 = client.get(url, params=params)
                ct = r3.headers.get("content-type", "")
                print(f"\nGET {url} params={params}")
                print(f"  -> {r3.status_code} ct={ct[:40]} len={len(r3.content)}")
                cd = r3.headers.get("content-disposition", "")
                print(f"  cd={cd[:80]}")
                if "json" in ct:
                    print("  body:", r3.text[:300])
                elif len(r3.content) > 1000 and r3.content[:2] == b"PK":
                    print("  *** DOCX (ZIP) received ***")
                    # try extracting text from the docx
                    try:
                        zf = zipfile.ZipFile(io.BytesIO(r3.content))
                        xml = zf.read("word/document.xml").decode("utf-8")
                        import re
                        text = re.sub(r"<[^>]+>", "", xml)
                        print("  extracted text (first 300):", text[:300])
                    except Exception as exc:
                        print("  zip parse error:", exc)
            except Exception as exc:
                print(f"  -> ERROR {str(exc)[:150]}")


if __name__ == "__main__":
    main()
