"""Database session management with async SQLAlchemy."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from libs.common.config import get_settings
from libs.common.logging import get_logger

logger = get_logger(__name__)

# Global engine and session factory
_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    """Get or create the database engine."""
    global _engine

    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(
            settings.database_url,
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_timeout=settings.db_pool_timeout,
            pool_pre_ping=True,
            echo=settings.debug,
        )
        logger.info("Database engine created")

    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Get or create the session factory."""
    global _session_factory

    if _session_factory is None:
        engine = get_engine()
        _session_factory = async_sessionmaker(
            bind=engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autocommit=False,
            autoflush=False,
        )
        logger.info("Session factory created")

    return _session_factory


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    """Get an async database session.

    Yields:
        AsyncSession: Database session

    Example:
        async with get_async_session() as session:
            result = await session.execute(query)
    """
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


# Alias for FastAPI dependency injection
AsyncSessionLocal = get_async_session


@asynccontextmanager
async def get_session_context() -> AsyncGenerator[AsyncSession, None]:
    """Context manager for database sessions.

    Example:
        async with get_session_context() as session:
            result = await session.execute(query)
    """
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db() -> None:
    """Initialize database connection pool."""
    engine = get_engine()
    # Test connection
    async with engine.begin() as conn:
        await conn.execute(text("SELECT 1"))
    logger.info("Database connection verified")


async def close_db() -> None:
    """Close database connection pool."""
    global _engine, _session_factory

    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
        logger.info("Database connection closed")


async def execute_raw(sql: str, params: dict[str, Any] | None = None) -> Any:
    """Execute raw SQL query.

    Args:
        sql: SQL query string
        params: Query parameters

    Returns:
        Query result
    """
    engine = get_engine()
    async with engine.begin() as conn:
        result = await conn.execute(text(sql), params or {})
        return result
