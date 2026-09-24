"""Shared pytest fixtures for the Legal Intelligent Assistance System test suite.

Provides async SQLite session, FastAPI TestClient, sample user, and auth headers.
All tests run without external services (no PostgreSQL, Redis, or Milvus needed).
"""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from jose import jwt
from passlib.context import CryptContext
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.core.database import Base, get_db
from app.main import app
from app.models.user import User

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"
TEST_JWT_SECRET = "test-jwt-secret-key-for-testing-only"

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


# ---------------------------------------------------------------------------
# Event loop fixture (required for pytest-asyncio on Windows)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def event_loop():
    """Create an event loop for the test session."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# ---------------------------------------------------------------------------
# Rate limiting (disabled for the whole suite)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True, scope="session")
def _disable_rate_limiting():
    """Bypass IP/user rate limiting: every test request originates from the
    same testclient IP, so the 120 req/min cap trips long before the suite
    finishes (observed as spurious 429s instead of 401/200 assertions)."""
    from app.middleware.rate_limiter import RateLimiter

    async def _always_allowed(self, key: str, max_requests: int, window_seconds: int) -> bool:
        return True

    original = RateLimiter.is_allowed
    RateLimiter.is_allowed = _always_allowed
    yield
    RateLimiter.is_allowed = original


# ---------------------------------------------------------------------------
# Async engine & session fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="function")
async def async_engine():
    """Create a fresh in-memory SQLite engine for each test function."""
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        # Import all models so they are registered with Base.metadata
        from app.models import (  # noqa: F401
            User, UserRole,
            Conversation,
            Message, MessageRole,
            Feedback,
            Document, DocumentStatus,
            AuditLog,
        )
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def async_session(async_engine) -> AsyncGenerator[AsyncSession, None]:
    """Yield an async SQLAlchemy session backed by in-memory SQLite."""
    session_factory = async_sessionmaker(
        bind=async_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )
    async with session_factory() as session:
        yield session


# ---------------------------------------------------------------------------
# FastAPI TestClient fixture (async httpx client)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="function")
async def test_client(async_session) -> AsyncGenerator[AsyncClient, None]:
    """Create an async httpx TestClient with DB dependency overridden."""

    async def _override_get_db():
        yield async_session

    app.dependency_overrides[get_db] = _override_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client

    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Sample user fixture
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="function")
async def sample_user(async_session) -> User:
    """Create and persist a test user in the database."""
    user = User(
        username="testuser",
        email="testuser@example.com",
        hashed_password=pwd_context.hash("TestPass123"),
        role="user",
        is_active=True,
    )
    async_session.add(user)
    await async_session.flush()
    await async_session.refresh(user)
    return user


# ---------------------------------------------------------------------------
# Auth headers fixture
# ---------------------------------------------------------------------------


@pytest.fixture(scope="function")
def auth_headers(sample_user) -> dict[str, str]:
    """Return valid JWT Authorization headers for the sample user."""
    token = jwt.encode(
        {
            "sub": str(sample_user.id),
            # exp must stay within 30 days: strict JWT validation rejects
            # far-future exp as a hardening measure
            "exp": datetime.now(timezone.utc) + timedelta(hours=1),
            "iat": datetime.now(timezone.utc),
        },
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )
    return {"Authorization": f"Bearer {token}"}
