# -*- coding: utf-8 -*-
"""WAF 验证码两步流程。
用法:
  python flk_waf_session.py fetch   -> 下载新验证码, 保存 cookie+图片
  python flk_waf_session.py verify <text>  -> 用保存的 cookie 提交验证
  python flk_waf_session.py test    -> 测试 API 是否已解封
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, "/app")

import httpx

STATE = Path("/tmp/waf_state.json")
IMG = Path("/tmp/waf_captcha.jpg")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/152.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": "https://flk.npc.gov.cn/",
}


def fetch():
    with httpx.Client(headers=HEADERS, timeout=30, follow_redirects=True) as c:
        r = c.get("https://flk.npc.gov.cn/waf_text_captcha")
        cid = c.cookies.get("wzws_cid")
        STATE.write_text(json.dumps({"wzws_cid": cid}), encoding="utf-8")
        IMG.write_bytes(r.content)
        print(f"fetched captcha: len={len(r.content)} cid={cid[:20]}...")


def verify(text: str):
    state = json.loads(STATE.read_text(encoding="utf-8"))
    with httpx.Client(
        headers=HEADERS, timeout=30, follow_redirects=False,
        cookies={"wzws_cid": state["wzws_cid"]},
    ) as c:
        r = c.get(
            "https://flk.npc.gov.cn/waf_text_verify.html",
            params={"captcha": text},
        )
        print(f"verify status={r.status_code} ct={r.headers.get('content-type')}")
        print(f"set-cookie: {r.headers.get_list('set-cookie')}")
        print(f"location: {r.headers.get('location')}")
        body = r.text
        # look for hints
        for kw in ("错误", "失败", "成功", "重新", "验证"):
            for seg in body.split("。"):
                if kw in seg:
                    clean = "".join(seg.split())[:120]
                    print(f"  hint[{kw}]: {clean}")
                    break
        if "访问验证" in body:
            print("=> returned challenge page (verification failed or new challenge)")
        else:
            print("=> non-challenge response!")
        print(f"body head: {''.join(body[:300].split())}")


def test():
    state = json.loads(STATE.read_text(encoding="utf-8"))
    with httpx.Client(
        headers=HEADERS, timeout=30, follow_redirects=True,
        cookies={"wzws_cid": state["wzws_cid"]},
    ) as c:
        r = c.get(
            "https://flk.npc.gov.cn/law-search/download/pc",
            params={"format": "docx", "bbbs": "ff8081817918fa96017920845025033c"},
            headers={"Accept": "application/json, text/plain, */*",
                     "Referer": "https://flk.npc.gov.cn/detail",
                     "Origin": "https://flk.npc.gov.cn"},
        )
        ct = r.headers.get("content-type", "?")
        print(f"api test: status={r.status_code} ct={ct}")
        if "json" in ct:
            print(f"JSON: {r.text[:200]}")
            print("UNBLOCKED!")
        else:
            print("still blocked")


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "fetch":
        fetch()
    elif cmd == "verify":
        verify(sys.argv[2])
    elif cmd == "test":
        test()
