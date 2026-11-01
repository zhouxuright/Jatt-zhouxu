"""Generic HTTP response cache for idempotent GET API endpoints.

Why this exists
---------------
Only the LLM semantic cache existed before, so cheap, high-frequency reads
(law lists, dictionaries, tenant metadata, corpus registry, dashboard stats)
still hit PostgreSQL on every request.  This middleware adds a short-TTL
response cache in front of ``GET /api/*`` so those endpoints land in the
low-millisecond range under concurrency.

Correctness rules (deliberately conservative)
---------------------------------------------
* Only ``GET`` responses with status 200 and ``application/json`` are cached.
* The cache key includes a hash of the caller's ``Authorization`` header, so a
  response is never served across different users (and therefore never leaks
  across tenants).
* Any write (``POST``/``PUT``/``PATCH``/``DELETE``) bumps a global generation
  counter, which makes every previously cached entry unreachable at once
  (generation is part of the key).  Old entries simply expire by TTL.
* Streaming / strongly dynamic endpoints are excluded via
  ``RESPONSE_CACHE_EXCLUDE_PREFIXES`` in settings.
* Bodies larger than ``RESPONSE_CACHE_MAX_BODY_BYTES`` are not cached.

Fail-open: if Redis is unavailable the middleware falls back to a bounded
in-process dict, and if anything goes wrong it just passes the request through.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import time
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)

_GEN_KEY = "legal:rc:gen"
_KEY_PREFIX = "legal:rc:"
_MEM_MAX_ENTRIES = 512
_GEN_CACHE_SECONDS = 1.0
# How long a follower waits for the leader holding the same cache key. LLM-free
# read endpoints answer in well under a second; the cap only guards against a
# leader that dies without firing its event.
_SINGLEFLIGHT_WAIT_SECONDS = 15.0

# Bounded in-process fallback: key -> (expires_at, payload_bytes)
_mem_cache: dict[str, tuple[float, bytes]] = {}
_mem_generation = 0
_gen_cache: tuple[float, int] | None = None

# Single-flight: key -> event that the computing leader sets when it is done.
# Concurrent identical GETs (same key, cache cold) then wait for one computation
# instead of each recomputing the same expensive query (thundering herd).
_inflight: dict[str, asyncio.Event] = {}


def _header_value(scope: dict[str, Any], name: bytes) -> bytes:
    for key, value in scope.get("headers", []):
        if key.lower() == name:
            return value
    return b""


def _auth_fingerprint(scope: dict[str, Any]) -> str:
    auth = _header_value(scope, b"authorization")
    if not auth:
        return "anon"
    return hashlib.sha256(auth).hexdigest()[:16]


def _cache_key(scope: dict[str, Any], generation: int) -> str:
    path = scope.get("path", "")
    query = scope.get("query_string", b"").decode("latin-1", errors="replace")
    return f"{_KEY_PREFIX}{generation}:{path}?{query}:{_auth_fingerprint(scope)}"


def _redis() -> Any:
    try:
        from app.core.redis import get_redis
        return get_redis()
    except Exception:
        return None


class ResponseCacheMiddleware:
    """Pure ASGI middleware implementing a short-TTL GET response cache."""

    def __init__(self, app) -> None:
        self.app = app

    # ------------------------------------------------------------------
    # Generation / invalidation
    # ------------------------------------------------------------------
    async def _generation(self) -> int:
        global _gen_cache, _mem_generation
        now = time.monotonic()
        if _gen_cache is not None and now - _gen_cache[0] < _GEN_CACHE_SECONDS:
            return _gen_cache[1]

        generation = _mem_generation
        client = _redis()
        if client is not None:
            try:
                raw = await client.get(_GEN_KEY)
                generation = int(raw) if raw else 0
            except Exception:
                generation = _mem_generation
        _gen_cache = (now, generation)
        return generation

    async def _invalidate(self) -> None:
        """Make all cached entries unreachable after a write."""
        global _gen_cache, _mem_generation
        _mem_cache.clear()
        _mem_generation += 1
        _gen_cache = None
        client = _redis()
        if client is not None:
            try:
                await client.incr(_GEN_KEY)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Read / write
    # ------------------------------------------------------------------
    async def _get(self, key: str) -> tuple[int, str, bytes] | None:
        client = _redis()
        if client is not None:
            try:
                raw = await client.get(key)
                if raw:
                    payload = json.loads(raw)
                    return (
                        int(payload["s"]),
                        str(payload["t"]),
                        base64.b64decode(payload["b"]),
                    )
                return None
            except Exception:
                pass  # fall through to in-process cache

        entry = _mem_cache.get(key)
        if not entry:
            return None
        expires_at, raw_bytes = entry
        if expires_at < time.monotonic():
            _mem_cache.pop(key, None)
            return None
        payload = json.loads(raw_bytes)
        return int(payload["s"]), str(payload["t"]), base64.b64decode(payload["b"])

    async def _set(self, key: str, status: int, content_type: str, body: bytes) -> None:
        payload = json.dumps(
            {
                "s": status,
                "t": content_type,
                "b": base64.b64encode(body).decode("ascii"),
            },
            separators=(",", ":"),
        ).encode()

        client = _redis()
        if client is not None:
            try:
                await client.setex(key, settings.RESPONSE_CACHE_TTL_SECONDS, payload)
                return
            except Exception:
                pass

        if len(_mem_cache) >= _MEM_MAX_ENTRIES:
            # Evict the oldest entry (insertion-ordered dict).
            _mem_cache.pop(next(iter(_mem_cache)), None)
        _mem_cache[key] = (
            time.monotonic() + settings.RESPONSE_CACHE_TTL_SECONDS,
            payload,
        )

    async def _send_cached(
        self, send: Any, cached: tuple[int, str, bytes], cache_header: bytes
    ) -> None:
        status, content_type, body = cached
        await send({
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", content_type.encode("latin-1")),
                (b"content-length", str(len(body)).encode("latin-1")),
                (b"x-cache", cache_header),
            ],
        })
        await send({"type": "http.response.body", "body": body})

    # ------------------------------------------------------------------
    # ASGI entrypoint
    # ------------------------------------------------------------------
    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or not settings.RESPONSE_CACHE_ENABLED:
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "GET")
        path = scope.get("path", "")

        if method in ("POST", "PUT", "PATCH", "DELETE"):
            await self._invalidate()
            await self.app(scope, receive, send)
            return

        if method != "GET" or not path.startswith("/api/"):
            await self.app(scope, receive, send)
            return

        if any(path.startswith(p) for p in settings.RESPONSE_CACHE_EXCLUDE_PREFIXES):
            await self.app(scope, receive, send)
            return

        # Never route streaming endpoints through the cache/single-flight path:
        # a follower would otherwise wait for the leader's *whole* stream to end
        # before it even starts producing output.
        if path.endswith("/stream") or path.endswith("-stream"):
            await self.app(scope, receive, send)
            return

        key = _cache_key(scope, await self._generation())
        cached = await self._get(key)
        if cached is not None:
            await self._send_cached(send, cached, b"HIT")
            return

        # Single-flight: a concurrent identical request may already be computing
        # this key. Wait for it instead of hammering the database again.
        peer = _inflight.get(key)
        if peer is not None:
            try:
                await asyncio.wait_for(peer.wait(), timeout=_SINGLEFLIGHT_WAIT_SECONDS)
            except Exception:
                pass  # timeout / foreign loop / cancelled: fall back to computing
            cached = await self._get(key)
            if cached is not None:
                await self._send_cached(send, cached, b"HIT")
                return

        # Become the leader for this key (followers arriving now will wait on us).
        leader = asyncio.Event()
        _inflight[key] = leader

        start_headers: list[tuple[bytes, bytes]] = []
        status_code = 200
        content_type = ""
        chunks: list[bytes] = []
        total_bytes = 0
        cacheable = True

        async def send_capture(message: dict[str, Any]) -> None:
            nonlocal start_headers, status_code, content_type, total_bytes, cacheable
            if message["type"] == "http.response.start":
                headers = [tuple(h) for h in message.get("headers", [])]
                status_code = message.get("status", 200)
                content_type = ""
                for name, value in headers:
                    if name.lower() == b"content-type":
                        content_type = value.decode("latin-1")
                # Streaming (SSE) / non-JSON bodies are never cacheable — stop
                # buffering immediately instead of accumulating up to the size cap.
                if "application/json" not in content_type.lower():
                    cacheable = False
                start_headers = headers + [(b"x-cache", b"MISS")]
                await send({**message, "headers": start_headers})
                return

            if message["type"] == "http.response.body":
                body = message.get("body", b"")
                if body:
                    if total_bytes + len(body) > settings.RESPONSE_CACHE_MAX_BODY_BYTES:
                        cacheable = False
                        chunks.clear()
                    else:
                        total_bytes += len(body)
                        if cacheable:
                            chunks.append(body)
                await send(message)
                return

            await send(message)

        try:
            await self.app(scope, receive, send_capture)
        finally:
            try:
                if (
                    cacheable
                    and status_code == 200
                    and "application/json" in content_type.lower()
                    and chunks
                ):
                    await self._set(key, status_code, content_type, b"".join(chunks))
            finally:
                # Release followers even if caching failed or the app raised.
                if _inflight.get(key) is leader:
                    _inflight.pop(key, None)
                leader.set()
