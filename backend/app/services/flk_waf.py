"""FLK WAF handling for runtime services (P1.5 regulation monitor).

The flk.npc.gov.cn API sits behind a text-CAPTCHA WAF that issues a
``wzws_cid`` cookie once solved. This module provides:

- challenge detection for arbitrary responses
- an async auto-solver (ddddocr OCR, runs in a worker thread)
- a Redis-backed shared session so all backend replicas reuse one
  solved cookie instead of each hitting the challenge
- a WAF-aware POST helper that retries once after re-solving

Degrades gracefully: if ddddocr is unavailable or solving fails, callers
get the challenge/failed response and log a clear error.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

FLK_BASE = "https://flk.npc.gov.cn"
FLK_HOST = "flk.npc.gov.cn"

_CID_REDIS_KEY = "flk:waf_cid"
_CID_TTL_S = 2 * 3600  # wzws_cid stays valid for hours; refresh while in use

_BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/152.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": f"{FLK_BASE}/",
}

_redis: Any = None


def _get_redis() -> Any:
    global _redis
    if _redis is None:
        import redis.asyncio as redis_asyncio

        _redis = redis_asyncio.from_url(settings.REDIS_URL, decode_responses=True)
    return _redis


async def get_shared_cid() -> str | None:
    try:
        return await _get_redis().get(_CID_REDIS_KEY)
    except Exception as exc:
        logger.warning("flk_waf: redis get cid failed: %s", exc)
        return None


async def set_shared_cid(cid: str) -> None:
    try:
        await _get_redis().set(_CID_REDIS_KEY, cid, ex=_CID_TTL_S)
    except Exception as exc:
        logger.warning("flk_waf: redis set cid failed: %s", exc)


_ocr: Any = None


def _get_ocr() -> Any:
    global _ocr
    if _ocr is None:
        import ddddocr

        _ocr = ddddocr.DdddOcr(show_ad=False)
    return _ocr


def is_challenge(resp: httpx.Response) -> bool:
    """True if the response is a WAF challenge page."""
    ct = resp.headers.get("content-type", "")
    if "text/html" not in ct:
        return False
    body = resp.text[:4096]
    return "访问验证" in body or "waf_text_captcha" in body


async def solve_waf(max_attempts: int = 15) -> str:
    """Solve the WAF challenge and return the passing wzws_cid.

    Raises RuntimeError if unsolvable, ImportError-ish if ddddocr missing.
    """
    ocr = await asyncio.to_thread(_get_ocr)  # fail fast on missing ddddocr

    async with httpx.AsyncClient(
        headers=_BROWSER_HEADERS, timeout=30, follow_redirects=False,
    ) as client:
        for attempt in range(1, max_attempts + 1):
            r = await client.get(f"{FLK_BASE}/waf_text_captcha")
            if r.status_code != 200 or r.content[:2] != b"\xff\xd8":
                logger.warning("flk_waf: captcha fetch abnormal: %s len=%d",
                               r.status_code, len(r.content))
                await asyncio.sleep(1.0)
                continue
            cid = client.cookies.get("wzws_cid")
            if not cid:
                logger.warning("flk_waf: no wzws_cid after captcha fetch")
                await asyncio.sleep(1.0)
                continue

            text = (await asyncio.to_thread(ocr.classification, r.content)).strip()
            if not text or len(text) < 3:
                logger.info("flk_waf: attempt %d ocr too short '%s'", attempt, text)
                continue

            rv = await client.get(
                f"{FLK_BASE}/waf_text_verify.html",
                params={"captcha": text},
                cookies={"wzws_cid": cid},
            )
            if rv.status_code in (301, 302):
                new_cid = None
                for sc in rv.headers.get_list("set-cookie"):
                    if sc.startswith("wzws_cid="):
                        new_cid = sc.split("=", 1)[1].split(";", 1)[0]
                        break
                if new_cid or cid:
                    logger.info("flk_waf: solved on attempt %d", attempt)
                    return new_cid or cid

            logger.info("flk_waf: attempt %d ocr '%s' rejected (status=%s)",
                        attempt, text, rv.status_code)
            await asyncio.sleep(0.8)

    raise RuntimeError(f"flk_waf: auto-solve failed after {max_attempts} attempts")


async def attach_shared_cid(client: httpx.AsyncClient) -> None:
    """Seed the client with the Redis-shared WAF cookie, if any."""
    cid = await get_shared_cid()
    if cid:
        client.cookies.set("wzws_cid", cid, domain=FLK_HOST)


async def post_json_waf_aware(
    client: httpx.AsyncClient, url: str, payload: dict[str, Any],
) -> httpx.Response:
    """POST JSON; on a WAF challenge, solve once and retry with the fresh cid."""
    resp = await client.post(url, json=payload)
    if not is_challenge(resp):
        return resp

    logger.info("flk_waf: challenge on %s — auto-solving", url)
    try:
        cid = await solve_waf()
    except Exception as exc:
        logger.error("flk_waf: auto-solve failed, giving up this request: %s", exc)
        return resp

    await set_shared_cid(cid)
    client.cookies.set("wzws_cid", cid, domain=FLK_HOST)
    return await client.post(url, json=payload)
