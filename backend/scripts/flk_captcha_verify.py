# -*- coding: utf-8 -*-
"""提交 WAF 验证码验证并测试是否解封。
用法: python flk_captcha_verify.py <captcha_text> <wzws_cid>
"""
import sys

sys.path.insert(0, "/app")

import httpx

captcha_text = sys.argv[1]
cid = sys.argv[2]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/152.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://flk.npc.gov.cn/",
}

with httpx.Client(
    headers=HEADERS, timeout=30, follow_redirects=True,
    cookies={"wzws_cid": cid},
) as c:
    r = c.get(
        "https://flk.npc.gov.cn/waf_text_verify.html",
        params={"captcha": captcha_text},
    )
    print(f"verify: status={r.status_code} content-type={r.headers.get('content-type')}")
    print(f"cookies now: {list(c.cookies.keys())}")
    print(f"set-cookie: {r.headers.get_list('set-cookie')}")
    body = r.text[:500].replace("\n", " ")
    print(f"body[:500]: {body}")

    # test API access
    r2 = c.get(
        "https://flk.npc.gov.cn/law-search/download/pc",
        params={"format": "docx", "bbbs": "ff8081817918fa96017920845025033c"},
        headers={"Accept": "application/json, text/plain, */*",
                 "Referer": "https://flk.npc.gov.cn/detail",
                 "Origin": "https://flk.npc.gov.cn"},
    )
    ct = r2.headers.get("content-type", "?")
    print(f"\napi test: status={r2.status_code} content-type={ct}")
    if "json" in ct:
        j = r2.json()
        print(f"api JSON response: {str(j)[:200]}")
        print("SUCCESS - unblocked!")
    else:
        print(f"still blocked, body[:200]: {r2.text[:200]}")
