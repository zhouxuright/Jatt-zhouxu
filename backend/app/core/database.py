"""SQLAlchemy async engine, session factory, and database utilities."""

from typing import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings

# Build the database URL - support both PostgreSQL and SQLite
if settings.DATABASE_URL.startswith("sqlite"):
    # SQLite for development
    connect_args = {"check_same_thread": False}
    engine = create_async_engine(
        settings.DATABASE_URL,
        echo=settings.DATABASE_ECHO,
        connect_args=connect_args,
    )
else:
    # PostgreSQL for production
    engine = create_async_engine(
        settings.DATABASE_URL,
        echo=settings.DATABASE_ECHO,
        pool_size=settings.DATABASE_POOL_SIZE,
        max_overflow=settings.DATABASE_MAX_OVERFLOW,
        pool_pre_ping=True,
        pool_recycle=3600,
    )

async_session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy ORM models."""
    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields an async database session.

    The session is automatically closed when the request finishes.
    """
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def check_db_connection() -> bool:
    """Verify the database connection is healthy."""
    try:
        async with engine.connect() as conn:
            if settings.DATABASE_URL.startswith("sqlite"):
                await conn.execute(text("SELECT 1"))
            else:
                await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


async def close_db() -> None:
    """Dispose the engine and release all connections."""
    await engine.dispose()


async def init_db() -> None:
    """Create all database tables. Used for development setup."""
    # Import all models to ensure they are registered with Base.metadata
    from app.models import (  # noqa: F401
        User, UserRole,
        Conversation,
        Message, MessageRole,
        Feedback,
        Document, DocumentStatus, ContractReview,
        AuditLog,
    )

    async with engine.begin() as conn:
        if not settings.DATABASE_URL.startswith("sqlite"):
            # pg_trgm 扩展：支撑 court_cases 的 GIN 三元组索引（LIKE '%...%' 子串检索）
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        await conn.run_sync(Base.metadata.create_all)
