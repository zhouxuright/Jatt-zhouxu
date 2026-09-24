# -*- coding: utf-8 -*-
"""完整流程测试: 解封 cookie -> API -> url 字段 -> docx 下载 -> 文本提取。"""
import io
import json
import re
import sys
import zipfile

sys.path.insert(0, "/app")
sys.path.insert(0, "/tmp/ddddocr_lib")

import httpx

CID = "187a33f346015fe534909ee19afc73d2d2fa9b9ad310aa4628dccb858121797d1757f18749679a01cedd5e502c56576fa8853176c406b3d0901bee87e10941234807dd9c5a69a2467779d8f42b7377e6"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/152.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://flk.npc.gov.cn/detail",
    "Origin": "https://flk.npc.gov.cn",
}


def docx_text(data: bytes) -> str:
    zf = zipfile.ZipFile(io.BytesIO(data))
    xml = zf.read("word/document.xml").decode("utf-8")
    xml = re.sub(r"</w:p>", "\n", xml)
    return re.sub(r"<[^>]+>", "", xml)


with httpx.Client(headers=HEADERS, timeout=60, follow_redirects=True,
                 cookies={"wzws_cid": CID}) as c:
    r = c.get("https://flk.npc.gov.cn/law-search/download/pc",
              params={"format": "docx", "bbbs": "ff8081817918fa96017920845025033c"})
    j = r.json()
    print("JSON keys:", list((j.get("data") or {}).keys()))
    data = j.get("data") or {}
    url = data.get("url")
    print(f"url: {str(url)[:120]}")
    if not url:
        print("NO URL; full json:", json.dumps(j, ensure_ascii=False)[:500])
        sys.exit(1)
    r2 = c.get(url, timeout=120)
    print(f"docx download: status={r2.status_code} len={len(r2.content)} magic={r2.content[:2]}")
    if r2.content[:2] == b"PK":
        text = docx_text(r2.content)
        print(f"text extracted: {len(text)} chars")
        print(f"preview: {text[:150]}")
    else:
        print(f"not docx: {r2.content[:100]}")
