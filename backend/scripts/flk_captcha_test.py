# -*- coding: utf-8 -*-
"""测试 WAF 验证码流程：下载验证码图片，检查 cookie 机制。"""
import sys

sys.path.insert(0, "/app")

import httpx

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/152.0.0.0 Safari/537.36",
    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    "Referer": "https://flk.npc.gov.cn/",
}

with httpx.Client(headers=HEADERS, timeout=30, follow_redirects=True) as c:
    r = c.get("https://flk.npc.gov.cn/waf_text_captcha")
    print(f"captcha img: status={r.status_code} content-type={r.headers.get('content-type')} len={len(r.content)}")
    print(f"cookies after captcha: {dict(c.cookies)}")
    print(f"set-cookie headers: {r.headers.get_list('set-cookie')}")
    with open("/tmp/captcha_test.png", "wb") as f:
        f.write(r.content)
    print("saved /tmp/captcha_test.png")
    print(f"img magic: {r.content[:8].hex()}")
