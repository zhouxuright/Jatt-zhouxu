"""Security middleware -- headers hardening and request body sanitization.

Provides:
- SecurityHeadersMiddleware: Inject defensive HTTP headers into every response.
- InputSanitizationMiddleware: Reject oversized requests and strip null bytes.
"""

import logging
import re
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

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
# SecurityHeadersMiddleware
# =============================================================================

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security-related headers to every HTTP response.

    Headers added:
    - X-Content-Type-Options: nosniff   -- prevent MIME-type sniffing
    - X-Frame-Options: DENY             -- prevent clickjacking via iframes
    - X-XSS-Protection: 1; mode=block   -- legacy XSS filter for old browsers
    - Strict-Transport-Security         -- force HTTPS (only in production)
    - Content-Security-Policy           -- restrict resource origins
    - Remove: Server header             -- hide server software version
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response: Response = await call_next(request)

        # --- Defensive response headers ---
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=()"
        )

        # Content-Security-Policy -- restrictive default; API serves JSON so
        # we block everything except self for scripts/styles.
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; "
            "frame-ancestors 'none'"
        )

        # HSTS -- only meaningful when behind an HTTPS reverse proxy
        if settings.APP_ENV == "production":
            response.headers["Strict-Transport-Security"] = (
                "max-age=63072000; includeSubDomains; preload"
            )

        # Remove the Server header to avoid leaking software/version info
        if "server" in response.headers:
            del response.headers["server"]

        return response


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
