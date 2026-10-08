"""Security middleware -- headers hardening and request body sanitization.

Provides:
- SecurityHeadersMiddleware: Inject defensive HTTP headers into every response.
  (Pure ASGI middleware for minimal overhead.)
- InputSanitizationMiddleware: Reject oversized requests and strip null bytes.
"""

import logging
import re
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.config import settings

logger = logging.getLogger(__name__)

# Paths that accept file uploads (multipart/form-data) -- exempt from the
# regular API body-size limit; they use MAX_UPLOAD_SIZE_MB instead.
_UPLOAD_PREFIXES = (
    "/api/v1/contract/review",
    "/api/v1/contract/review-async",
    "/api/v1/document/upload",
)


# =============================================================================
# SecurityHeadersMiddleware (pure ASGI — no BaseHTTPMiddleware overhead)
# =============================================================================

# Pre-built header tuples (avoid re-creating bytes on every response)
_SECURITY_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"x-xss-protection", b"1; mode=block"),
    (b"referrer-policy", b"strict-origin-when-cross-origin"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
    (b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'"),
]

_HSTS_HEADER = (
    b"strict-transport-security",
    b"max-age=63072000; includeSubDomains; preload",
)


class SecurityHeadersMiddleware:
    """Add security-related headers to every HTTP response.

    Pure ASGI middleware (not BaseHTTPMiddleware) to avoid the overhead of
    anyio cancel scope wrapping on every request.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self._is_prod = settings.APP_ENV == "production"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: dict) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.extend(_SECURITY_HEADERS)
                if self._is_prod:
                    headers.append(_HSTS_HEADER)
                # Remove Server header to avoid leaking software info
                headers = [(k, v) for k, v in headers if k.lower() != b"server"]
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_headers)


# =============================================================================
# InputSanitizationMiddleware
# =============================================================================

# Regex: C0 control characters (U+0000 .. U+001F) except common whitespace
# (tab U+0009, LF U+000A, CR U+000D) which are valid in text bodies.
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")

# Null byte pattern (bytes)
_NULL_BYTES_RE = re.compile(rb"\x00")


class InputSanitizationMiddleware(BaseHTTPMiddleware):
    """Validate request size and sanitize incoming request bodies.

    - Enforces MAX_REQUEST_BODY_SIZE (default 5 MB) for API endpoints.
    - Enforces MAX_UPLOAD_SIZE_MB for upload endpoints.
    - Strips null bytes from JSON / text request bodies before they reach
      the route handler, preventing null-byte injection attacks.
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Only check requests that may carry a body
        if request.method in ("GET", "HEAD", "OPTIONS", "DELETE"):
            return await call_next(request)

        path = request.url.path

        # Determine applicable size limit
        is_upload = any(path.startswith(p) for p in _UPLOAD_PREFIXES)
        if is_upload:
            max_size = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
        else:
            max_size = settings.MAX_REQUEST_BODY_SIZE * 1024 * 1024

        # Read Content-Length header first for a fast reject
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                if int(content_length) > max_size:
                    logger.warning(
                        "Request body too large (Content-Length=%s, max=%d) for %s",
                        content_length, max_size, path,
                    )
                    return Response(
                        content='{"detail":"请求体过大"}',
                        status_code=413,
                        media_type="application/json",
                    )
            except ValueError:
                pass

        # Read the actual body to enforce the hard limit and sanitize
        body = await request.body()
        if len(body) > max_size:
            logger.warning(
                "Request body too large (%d bytes, max=%d) for %s",
                len(body), max_size, path,
            )
            return Response(
                content='{"detail":"请求体过大"}',
                status_code=413,
                media_type="application/json",
            )

        # Strip null bytes from non-multipart, non-binary bodies
        content_type = request.headers.get("content-type", "")
        if body and not content_type.startswith("multipart/form-data"):
            sanitized_body = _NULL_BYTES_RE.sub(b"", body)
            if sanitized_body != body:
                logger.info("Stripped null bytes from request body on %s", path)
                # Replace the body stream so downstream handlers see clean data
                _replace_body(request, sanitized_body)

        return await call_next(request)


def _replace_body(request: Request, new_body: bytes) -> None:
    """Replace the request body so downstream consumers see *new_body*.

    Starlette caches the body after the first ``request.body()`` call;
    we must update both the internal ``_body`` attribute and the receive
    channel so that ``request.json()``, ``request.form()``, etc. work.
    """
    request._body = new_body  # type: ignore[attr-defined]

    async def _receive() -> dict:
        return {"type": "http.request", "body": new_body, "more_body": False}

    request._receive = _receive  # type: ignore[attr-defined]
