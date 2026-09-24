# -*- coding: utf-8 -*-
"""FLK WAF 验证码自动求解器。

流程: fetch captcha (拿 wzws_cid) -> ddddocr 识别 -> verify
成功: 302 + 新 wzws_cid (通过令牌)
失败: 200 + 新挑战页 -> 重试

用法:
  from flk_waf_solver import solve_waf
  cid = solve_waf()          # 成功返回新 cid, 失败抛异常
  python flk_waf_solver.py   # 独立测试
"""
from __future__ import annotations

import logging
import sys
import time

sys.path.insert(0, "/tmp/ddddocr_lib")

import ddddocr
import httpx

logger = logging.getLogger("flk_waf")

BASE = "https://flk.npc.gov.cn"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/152.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": "https://flk.npc.gov.cn/",
}

_ocr = None


def _get_ocr():
    global _ocr
    if _ocr is None:
        _ocr = ddddocr.DdddOcr(show_ad=False)
    return _ocr


def solve_waf(max_attempts: int = 15, client: httpx.Client | None = None) -> str:
    """自动求解 WAF 挑战, 返回通过的 wzws_cid。"""
    own_client = client is None
    c = client or httpx.Client(headers=HEADERS, timeout=30, follow_redirects=False)

    try:
        for attempt in range(1, max_attempts + 1):
            # 1. 拿新验证码 (会 set 新 wzws_cid)
            r = c.get(f"{BASE}/waf_text_captcha")
            if r.status_code != 200 or not r.content[:2] == b"\xff\xd8":
                logger.warning("captcha fetch abnormal: %s len=%d", r.status_code, len(r.content))
                time.sleep(1.0)
                continue
            cid = c.cookies.get("wzws_cid")
            if not cid:
                logger.warning("no wzws_cid after captcha fetch")
                time.sleep(1.0)
                continue

            # 2. OCR
            text = _get_ocr().classification(r.content).strip()
            if not text or len(text) < 3:
                logger.info("attempt %d: ocr too short '%s', retry", attempt, text)
                continue

            # 3. 提交验证
            rv = c.get(
                f"{BASE}/waf_text_verify.html",
                params={"captcha": text},
                cookies={"wzws_cid": cid},
            )
            if rv.status_code in (301, 302):
                new_cid = None
                for sc in rv.headers.get_list("set-cookie"):
                    if sc.startswith("wzws_cid="):
                        new_cid = sc.split("=", 1)[1].split(";", 1)[0]
                        break
                if new_cid:
                    logger.info("WAF solved on attempt %d (ocr=%s)", attempt, text)
                    if own_client:
                        c.close()
                    return new_cid
                # 302 但没新 cookie: 可能已有有效 cid
                logger.info("verify 302 without new cookie, reusing cid")
                if own_client:
                    c.close()
                return cid

            logger.info("attempt %d: ocr '%s' rejected (status=%s), retrying",
                        attempt, text, rv.status_code)
            time.sleep(0.8)

        raise RuntimeError(f"WAF auto-solve failed after {max_attempts} attempts")
    finally:
        if own_client:
            c.close()


def is_challenge(resp: httpx.Response) -> bool:
    """判断响应是否为 WAF 挑战页。"""
    ct = resp.headers.get("content-type", "")
    return "text/html" in ct and ("访问验证" in resp.text or "waf_text_captcha" in resp.text)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    cid = solve_waf()
    print(f"\nSOLVED, new cid: {cid}")

    # 验证解封
    with httpx.Client(
        headers={**HEADERS, "Accept": "application/json, text/plain, */*",
                 "Referer": f"{BASE}/detail", "Origin": BASE},
        timeout=30, follow_redirects=True, cookies={"wzws_cid": cid},
    ) as c:
        r = c.get(
            f"{BASE}/law-search/download/pc",
            params={"format": "docx", "bbbs": "ff8081817918fa96017920845025033c"},
        )
        ct = r.headers.get("content-type", "?")
        print(f"api test: {r.status_code} {ct} -> {'UNBLOCKED' if 'json' in ct else 'STILL BLOCKED'}")
