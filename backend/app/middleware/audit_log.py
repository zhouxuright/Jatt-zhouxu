"""Audit log middleware -- logs API requests to the audit_logs table."""

import hashlib
import logging
import time
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.database import async_session_factory

logger = logging.getLogger(__name__)

# Paths that should not be logged
_SKIP_PATHS = {"/health", "/docs", "/redoc", "/openapi.json", "/"}
_SKIP_PREFIXES = ("/static/", "/assets/", "/metrics", "/api/v1/metrics")

# Sensitive paths where we log a truncated request summary
_SENSITIVE_PREFIXES = (
    "/api/v1/contract/review",
    "/api/v1/contract/templates/fill",
    "/api/v1/document/generate",
    "/api/v1/chat",
)

# Maximum length for request_summary stored in DB
_MAX_SUMMARY_LENGTH = 2000


def _decode_token(request: Request) -> dict:
    """Decode the JWT from the Authorization header; {} if absent/invalid."""
    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        return {}
    token = auth_header[7:]
    try:
        from jose import jwt
        from app.core.config import settings
        return jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
        )
    except Exception:
        return {}


def _extract_user_id(request: Request) -> str | None:
    """Extract user_id from the JWT Authorization header, if present."""
    return _decode_token(request).get("sub")


def _extract_tenant_id(request: Request) -> str | None:
    """Extract tenant_id (``tid`` claim) from the JWT.

    令牌自带租户标识，审计写入无需为每个请求额外查一次 users 表。
    匿名/未签发令牌的请求没有租户，如实留空。
    """
    return _decode_token(request).get("tid")


def _should_skip(request: Request) -> bool:
    """Return True if this request should not be audit-logged."""
    path = request.url.path
    if request.method == "OPTIONS":
        return True
    if path in _SKIP_PATHS:
        return True
    for prefix in _SKIP_PREFIXES:
        if path.startswith(prefix):
            return True
    return False


def _is_sensitive(request: Request) -> bool:
    """Return True if the request path is sensitive and should have its body summarized."""
    path = request.url.path
    for prefix in _SENSITIVE_PREFIXES:
        if path.startswith(prefix):
            return True
    return False


def _compute_body_hash(body_bytes: bytes) -> str | None:
    """Compute SHA256 hash of the first 1KB of the request body."""
    if not body_bytes:
        return None
    first_1kb = body_bytes[:1024]
    return hashlib.sha256(first_1kb).hexdigest()


def _determine_action(method: str, path: str) -> str:
    """Map HTTP method + path to a human-readable action string."""
    method_map = {
        "GET": "read",
        "POST": "create",
        "PUT": "update",
        "PATCH": "update",
        "DELETE": "delete",
    }
    action = method_map.get(method.upper(), method.lower())

    # Extract resource type from path (e.g. /api/v1/contract/review -> contract)
    parts = [p for p in path.strip("/").split("/") if p]
    resource = "unknown"
    if len(parts) >= 3:
        resource = parts[2]  # skip "api" and "v1"
    elif len(parts) >= 1:
        resource = parts[0]

    return f"{action}:{resource}"


async def _write_audit_log(
    user_id: str | None,
    action: str,
    resource_type: str | None,
    ip_address: str | None,
    user_agent: str | None,
    request_method: str | None,
    request_path: str | None,
    status_code: int | None,
    request_summary: str | None,
    request_body_hash: str | None = None,
    duration_ms: int | None = None,
    response_status: int | None = None,
    tenant_id: str | None = None,
) -> None:
    """Write a single audit log row in a background task."""
    try:
        from app.models.audit_log import AuditLog

        async with async_session_factory() as session:
            entry = AuditLog(
                user_id=user_id,
                tenant_id=tenant_id,
                action=action,
                resource_type=resource_type,
                ip_address=ip_address,
                user_agent=user_agent,
                request_method=request_method,
                request_path=request_path,
                status_code=status_code,
                request_summary=request_summary,
                request_body_hash=request_body_hash,
                duration_ms=duration_ms,
                response_status=response_status,
            )
            session.add(entry)
            await session.commit()
    except Exception:
        logger.exception("Failed to write audit log")


class AuditLogMiddleware(BaseHTTPMiddleware):
    """Starlette middleware that records API requests in the audit_logs table."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if _should_skip(request):
            return await call_next(request)

        # Extract metadata before calling next
        user_id = _extract_user_id(request)
        tenant_id = _extract_tenant_id(request)
        ip_address = request.client.host if request.client else None
        user_agent_str = request.headers.get("user-agent", "")[:512]
        request_method = request.method
        request_path = request.url.path[:512]
        action = _determine_action(request_method, request_path)

        # Extract resource_type from path
        parts = [p for p in request_path.strip("/").split("/") if p]
        resource_type = parts[2] if len(parts) >= 3 else (parts[0] if parts else None)

        # For sensitive endpoints, read and store a truncated request summary
        request_summary: str | None = None
        request_body_hash: str | None = None
        if request.method in ("POST", "PUT", "PATCH"):
            try:
                body_bytes = await request.body()
                if body_bytes:
                    # Compute body hash (SHA256 of first 1KB)
                    request_body_hash = _compute_body_hash(body_bytes)
                    # For sensitive endpoints, also store truncated summary
                    if _is_sensitive(request):
                        if b"\x00" in body_bytes[:_MAX_SUMMARY_LENGTH]:
                            # Binary payload (e.g. multipart audio upload) —
                            # NUL bytes are invalid in PostgreSQL TEXT columns
                            request_summary = f"<binary {len(body_bytes)} bytes>"
                        else:
                            text = body_bytes.decode("utf-8", errors="replace")
                            request_summary = text[:_MAX_SUMMARY_LENGTH].replace("\x00", "")
            except Exception:
                pass

        # Time the request
        start = time.perf_counter()
        response: Response | None = None
        status_code: int = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
        except Exception:
            status_code = 500
            raise
        finally:
            elapsed_ms = round((time.perf_counter() - start) * 1000)

            # Fire-and-forget background write
            import asyncio
            asyncio.create_task(
                _write_audit_log(
                    user_id=user_id,
                    action=action,
                    resource_type=resource_type,
                    ip_address=ip_address,
                    user_agent=user_agent_str,
                    request_method=request_method,
                    request_path=request_path,
                    status_code=status_code,
                    request_summary=request_summary,
                    request_body_hash=request_body_hash,
                    duration_ms=elapsed_ms,
                    response_status=status_code,
                    tenant_id=tenant_id,
                )
            )

            logger.debug(
                "Audit: %s %s -> %s (%dms) user=%s",
                request_method, request_path, status_code, elapsed_ms, user_id,
            )

        return response
