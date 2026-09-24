# -*- coding: utf-8 -*-
"""测试 ddddocr 对 WAF 验证码的识别准确率。"""
import sys

sys.path.insert(0, "/tmp/ddddocr_lib")

import ddddocr

ocr = ddddocr.DdddOcr(show_ad=False)

# 已知答案的验证码: yKT3 (验证成功过)
with open("/tmp/waf_captcha.jpg", "rb") as f:
    img = f.read()
result = ocr.classification(img)
print(f"known yKT3 -> recognized: {result}")

# 连续抓 5 个新验证码看识别结果
import httpx

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/152.0.0.0 Safari/537.36",
    "Referer": "https://flk.npc.gov.cn/",
}
with httpx.Client(headers=HEADERS, timeout=30) as c:
    for i in range(5):
        r = c.get("https://flk.npc.gov.cn/waf_text_captcha")
        res = ocr.classification(r.content)
        print(f"new captcha {i + 1}: recognized '{res}' (len={len(r.content)})")
