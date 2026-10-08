"""Prometheus metrics middleware and endpoint for the Legal Intelligent Assistance System.

Tracks:
- ``http_requests_total`` — HTTP request count by method, path, status.
- ``http_request_duration_seconds`` — Request duration histogram.
- ``http_requests_in_progress`` — Gauge of currently active requests.
- ``llm_api_calls_total`` — Counter for LLM API invocations (provider + model).
- ``embedding_generations_total`` — Counter for embedding generation calls.
- ``rag_retrievals_total`` — Counter for RAG retrieval calls.
"""

from __future__ import annotations

import time

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    CONTENT_TYPE_LATEST,
    REGISTRY,
)

# ---------------------------------------------------------------------------
# Custom registry so we can also expose via the ``/metrics`` route handler
# ---------------------------------------------------------------------------
registry: CollectorRegistry = REGISTRY

# ---------------------------------------------------------------------------
# Metrics definitions
# ---------------------------------------------------------------------------

# HTTP metrics
HTTP_REQUESTS = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["method", "path", "status"],
    registry=registry,
)

HTTP_DURATION = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "path"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
    registry=registry,
)

HTTP_IN_PROGRESS = Gauge(
    "http_requests_in_progress",
    "Number of HTTP requests currently in progress",
    registry=registry,
)

# LLM metrics
LLM_API_CALLS = Counter(
    "llm_api_calls_total",
    "Total LLM API calls",
    ["provider", "model"],
    registry=registry,
)

# Embedding metrics
EMBEDDING_GENERATIONS = Counter(
    "embedding_generations_total",
    "Total embedding generation calls",
    ["model"],
    registry=registry,
)

# RAG metrics
RAG_RETRIEVALS = Counter(
    "rag_retrievals_total",
    "Total RAG retrieval calls",
    ["source"],
    registry=registry,
)


# ---------------------------------------------------------------------------
# Path normalisation — collapse UUIDs / numeric IDs so label cardinality stays low
# ---------------------------------------------------------------------------
import re

_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
_NUMERIC_ID_RE = re.compile(r"/\d+(?=/|$)")


def _normalise_path(path: str) -> str:
    """Replace UUIDs and numeric IDs with placeholders to keep label cardinality bounded."""
    path = _UUID_RE.sub(":id", path)
    path = _NUMERIC_ID_RE.sub("/:id", path)
    return path


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------
_SKIP_METRICS_PREFIXES = (
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/metrics",
    "/static/",
    "/assets/",
)


class PrometheusMiddleware:
    """Pure ASGI middleware that records Prometheus metrics for every HTTP request.

    Converted from BaseHTTPMiddleware to pure ASGI to avoid the overhead of
    anyio cancel scope wrapping on every request.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")

        # Skip health-check / docs / static endpoints
        for prefix in _SKIP_METRICS_PREFIXES:
            if path == prefix or path.startswith(prefix):
                await self.app(scope, receive, send)
                return

        normalised_path = _normalise_path(path)
        method = scope.get("method", "GET")
        status_code = 500

        HTTP_IN_PROGRESS.inc()
        start = time.perf_counter()

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
            duration = time.perf_counter() - start
            HTTP_REQUESTS.labels(method=method, path=normalised_path, status=status_code).inc()
            HTTP_DURATION.labels(method=method, path=normalised_path).observe(duration)
            HTTP_IN_PROGRESS.dec()


# ---------------------------------------------------------------------------
# Helper to generate the metrics payload (used by the /metrics route)
# ---------------------------------------------------------------------------
def generate_metrics() -> bytes:
    """Return the current Prometheus metrics payload."""
    return generate_latest(registry)


METRICS_CONTENT_TYPE = CONTENT_TYPE_LATEST
