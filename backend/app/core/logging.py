"""Structured logging configuration for the Legal Intelligent Assistance System.

Provides:
- ``JsonFormatter`` for production JSON log lines.
- ``ColourFormatter`` for human-friendly coloured console output in development.
- ``setup_logging()`` — call once during application lifespan startup.
- ``RequestLoggingMiddleware`` — ASGI middleware that logs every HTTP request
  with method, path, status code, duration and user id.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from datetime import datetime, timezone
from starlette.requests import Request
from starlette.responses import Response


# ---------------------------------------------------------------------------
# JSON Formatter (production)
# ---------------------------------------------------------------------------
class JsonFormatter(logging.Formatter):
    """Output log records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Merge any extra fields attached to the record (e.g. user_id, request_id)
        for key in (
            "method",
            "path",
            "status_code",
            "duration_ms",
            "user_id",
            "request_id",
            "client_ip",
        ):
            value = getattr(record, key, None)
            if value is not None:
                log_entry[key] = value

        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_entry, ensure_ascii=False, default=str)


# ---------------------------------------------------------------------------
# Colour Formatter (development)
# ---------------------------------------------------------------------------
_COLOURS = {
    "DEBUG": "\033[36m",     # cyan
    "INFO": "\033[32m",      # green
    "WARNING": "\033[33m",   # yellow
    "ERROR": "\033[31m",     # red
    "CRITICAL": "\033[1;31m",  # bold red
}
_RESET = "\033[0m"


class ColourFormatter(logging.Formatter):
    """Human-friendly coloured console formatter for development."""

    def __init__(self) -> None:
        super().__init__(
            fmt="%(asctime)s %(colour)s[%(levelname)-8s]%(reset)s %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )

    def format(self, record: logging.LogRecord) -> str:
        record.colour = _COLOURS.get(record.levelname, "")  # type: ignore[attr-defined]
        record.reset = _RESET  # type: ignore[attr-defined]
        return super().format(record)


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
def setup_logging() -> None:
    """Configure the root logger and app loggers.

    In **production** (``APP_ENV != "development"``) the root logger uses
    ``JsonFormatter`` so that log aggregators can parse each line as JSON.
    In **development** a coloured, human-readable formatter is used instead.
    """
    from app.core.config import settings

    level = getattr(logging, settings.LOG_LEVEL, logging.INFO)
    is_dev = settings.APP_ENV == "development"

    root = logging.getLogger()
    root.setLevel(level)

    # Remove any pre-existing handlers to avoid duplicate output
    root.handlers.clear()

    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setLevel(level)

    if is_dev:
        handler.setFormatter(ColourFormatter())
    else:
        handler.setFormatter(JsonFormatter())

    root.addHandler(handler)

    # Quiet noisy third-party loggers
    for name in ("uvicorn.access", "sqlalchemy.engine", "httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)

    # Make sure the app-level logger reflects the configured level
    logging.getLogger("app").setLevel(level)

    logger = logging.getLogger(__name__)
    logger.info(
        "Logging configured: level=%s, format=%s",
        settings.LOG_LEVEL,
        "json" if not is_dev else "colour",
    )


# ---------------------------------------------------------------------------
# Request logging middleware
# ---------------------------------------------------------------------------
_SKIP_LOG_PREFIXES = (
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/metrics",
    "/static/",
    "/assets/",
)


class RequestLoggingMiddleware:
    """Pure ASGI middleware: log every HTTP request with method, path, status, duration, user.

    Converted from BaseHTTPMiddleware to avoid anyio cancel scope overhead.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")

        # Skip noisy / health-check endpoints
        for prefix in _SKIP_LOG_PREFIXES:
            if path == prefix or path.startswith(prefix):
                await self.app(scope, receive, send)
                return

        # Best-effort user extraction from Authorization header
        user_id: str | None = None
        headers_dict = dict(scope.get("headers", []))
        auth = headers_dict.get(b"authorization", b"").decode("latin-1", errors="replace")
        if auth.startswith("Bearer "):
            try:
                from jose import jwt
                from app.core.config import settings
                payload = jwt.decode(
                    auth[7:],
                    settings.JWT_SECRET_KEY,
                    algorithms=[settings.JWT_ALGORITHM],
                )
                user_id = payload.get("sub")
            except Exception:
                pass

        method = scope.get("method", "GET")
        client_ip = scope["client"][0] if scope.get("client") else None
        start = time.perf_counter()
        status_code = 500

        async def send_capture(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message.get("status_code", 500)
            await send(message)

        try:
            await self.app(scope, receive, send_capture)
        except Exception:
            status_code = 500
            raise
        finally:
            duration_ms = round((time.perf_counter() - start) * 1000, 2)
            extra = {
                "method": method,
                "path": path,
                "status_code": status_code,
                "duration_ms": duration_ms,
                "user_id": user_id,
                "client_ip": client_ip,
            }

            log_func = logging.getLogger("app.request")
            if status_code >= 500:
                log_func.error(
                    "%(method)s %(path)s -> %(status_code)s (%(duration_ms)sms) user=%(user_id)s",
                    extra,
                )
            elif status_code >= 400:
                log_func.warning(
                    "%(method)s %(path)s -> %(status_code)s (%(duration_ms)sms) user=%(user_id)s",
                    extra,
                )
            else:
                log_func.info(
                    "%(method)s %(path)s -> %(status_code)s (%(duration_ms)sms) user=%(user_id)s",
                    extra,
                )
