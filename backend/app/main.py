"""FastAPI application entry point for the Legal Intelligent Assistance System."""

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

from app.api.v1.router import router as v1_router
from app.core.config import settings
from app.core.database import check_db_connection, close_db, init_db
from app.core.logging import RequestLoggingMiddleware, setup_logging
from app.middleware.metrics import (
    METRICS_CONTENT_TYPE,
    PrometheusMiddleware,
    generate_metrics,
)
from app.middleware.rate_limiter import RateLimitMiddleware
from app.middleware.audit_log import AuditLogMiddleware
from app.middleware.security import SecurityHeadersMiddleware, InputSanitizationMiddleware

# ---------------------------------------------------------------------------
# Logging (initial basic config — replaced by setup_logging() in lifespan)
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan: startup and shutdown events."""
    logger.info("Starting %s v%s ...", settings.APP_NAME, settings.APP_VERSION)

    # Configure structured logging (JSON in prod, coloured console in dev)
    setup_logging()

    # Initialize database tables (development mode)
    try:
        await init_db()
        logger.info("Database tables initialized")
    except Exception as exc:
        logger.warning("Database initialization failed: %s (will try to continue)", exc)

    # Verify database connectivity on startup
    db_ok = await check_db_connection()
    if db_ok:
        logger.info("Database connection: OK")
    else:
        logger.warning("Database connection: FAILED (server will start anyway)")

    # Auto-migrate: add any missing columns detected between models and DB
    try:
        from scripts.auto_migrate import auto_migrate
        await auto_migrate()
        logger.info("Auto-migration complete")
    except Exception as exc:
        logger.warning("Auto-migration failed: %s (will try to continue)", exc)

    # Warm up AI models (BGE-M3 embedding + BGE-Reranker-V2-M3)
    # This loads models into memory once at startup, avoiding 30s+ first-request latency.
    # Gated by MODEL_WARMUP_ENABLED so resource-constrained deployments can
    # skip the ~4GB model load and load lazily on first request instead.
    import os
    if os.getenv("MODEL_WARMUP_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}:
        try:
            from app.services.model_registry import ModelRegistry
            await ModelRegistry.warmup()
            logger.info("AI models warmed up: BGE-M3 + BGE-Reranker-V2-M3")
        except Exception as exc:
            logger.warning("AI model warmup failed: %s (will load on first request)", exc)
    else:
        logger.info("AI model warmup disabled via MODEL_WARMUP_ENABLED")

    # P1.5 regulation change monitor (daily background cycle)
    if settings.REGULATION_MONITOR_ENABLED:
        import asyncio as _asyncio
        from app.services.regulation_monitor import regulation_monitor_loop
        monitor_task = _asyncio.create_task(
            regulation_monitor_loop(
                interval_hours=settings.REGULATION_MONITOR_INTERVAL_HOURS,
                days_back=settings.REGULATION_MONITOR_DAYS_BACK,
            )
        )
        logger.info(
            "Regulation monitor started (every %.1fh)",
            settings.REGULATION_MONITOR_INTERVAL_HOURS,
        )

    yield

    # Shutdown
    logger.info("Shutting down %s ...", settings.APP_NAME)
    try:
        monitor_task.cancel()
    except NameError:
        pass
    await close_db()
    logger.info("Shutdown complete.")


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------
app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="AI-powered legal consultation, contract review, and document generation system.",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)


# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------
# Parse CORS origins - support both comma-separated string and list
cors_origins = settings.CORS_ORIGINS
if isinstance(cors_origins, str):
    cors_origins = [o.strip() for o in cors_origins.split(",") if o.strip()]

# Restrict CORS methods/headers in production to reduce attack surface
cors_allow_methods = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
cors_allow_headers = ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=cors_allow_methods,
    allow_headers=cors_allow_headers,
)

# Security headers middleware (defensive headers on all responses)
app.add_middleware(SecurityHeadersMiddleware)

# Input sanitization middleware (body size limits, null byte stripping)
app.add_middleware(InputSanitizationMiddleware)

# Rate limiting middleware (IP + user based, Redis sliding window)
app.add_middleware(RateLimitMiddleware)

# Audit log middleware (logs API requests to audit_logs table)
app.add_middleware(AuditLogMiddleware)

# Request logging middleware (structured HTTP access logs)
app.add_middleware(RequestLoggingMiddleware)

# Prometheus metrics middleware (HTTP counters, histograms, gauges)
app.add_middleware(PrometheusMiddleware)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------
@app.get("/health", tags=["Health"])
async def health_check() -> JSONResponse:
    """Health check endpoint for load balancers and monitoring."""
    from app.services.model_registry import ModelRegistry
    from app.middleware.rate_limiter import get_circuit_breaker
    from app.services.semantic_cache import get_semantic_cache

    return JSONResponse(
        content={
            "status": "healthy",
            "app": settings.APP_NAME,
            "version": settings.APP_VERSION,
            "environment": settings.APP_ENV,
            "models": ModelRegistry.get_stats(),
            "circuit_breaker": get_circuit_breaker().get_state(),
        }
    )


@app.get("/", tags=["Root"])
async def root() -> dict[str, str]:
    """Root endpoint returning API information."""
    return {
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "docs": "/docs",
        "health": "/health",
        "metrics": "/metrics",
    }


# ---------------------------------------------------------------------------
# Prometheus metrics
# ---------------------------------------------------------------------------
@app.get("/metrics", tags=["Monitoring"])
async def metrics() -> Response:
    """Prometheus metrics endpoint for scraping."""
    return Response(content=generate_metrics(), media_type=METRICS_CONTENT_TYPE)


# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------
app.include_router(v1_router, prefix="/api/v1")


# ---------------------------------------------------------------------------
# Exception handlers
# ---------------------------------------------------------------------------
@app.exception_handler(Exception)
async def global_exception_handler(request, exc: Exception) -> JSONResponse:
    """Catch-all exception handler for unhandled errors."""
    logger.exception("Unhandled exception: %s", exc)
    return JSONResponse(
        status_code=500,
        content={
            "detail": "An internal server error occurred.",
            "error_type": type(exc).__name__,
        },
    )
