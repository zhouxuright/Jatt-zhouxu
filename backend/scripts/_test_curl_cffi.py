import json
import sys

from curl_cffi import requests as creq

cp = json.load(open('/tmp/flk_download_checkpoint.json'))
failed_bbbs = list(cp['failed'].keys())[:10]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://flk.npc.gov.cn/detail",
    "Origin": "https://flk.npc.gov.cn",
}

ok, bad = 0, 0
for bbbs in failed_bbbs:
    try:
        r = creq.get(
            "https://flk.npc.gov.cn/law-search/download/pc",
            params={"format": "docx", "bbbs": bbbs},
            headers=HEADERS,
            impersonate="chrome",
            timeout=30,
        )
        ct = r.headers.get("content-type", "")
        try:
            j = r.json()
            has_url = bool((j.get("data") or {}).get("url"))
            if has_url:
                ok += 1
                print(f"OK   {bbbs[:12]} json url present")
            else:
                bad += 1
                print(f"BAD  {bbbs[:12]} json no-url: {j.get('msg')}")
        except Exception:
            bad += 1
            print(f"BAD  {bbbs[:12]} {ct} len={len(r.text)}")
    except Exception as exc:
        bad += 1
        print(f"ERR  {bbbs[:12]} {type(exc).__name__}: {str(exc)[:60]}")

print(f"\nRESULT: {ok}/{ok + bad} succeeded")
