"""Health check endpoints."""

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import text

from libs.common import get_logger
from libs.db import get_session_context
from libs.messaging.redis import get_redis_client

logger = get_logger(__name__)

router = APIRouter()


class HealthResponse(BaseModel):
    """Health check response."""

    status: str
    service: str
    version: str
    dependencies: dict[str, str]


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Check service health and dependencies."""
    dependencies = {}

    # Check PostgreSQL
    try:
        async with get_session_context() as session:
            await session.execute(text("SELECT 1"))
        dependencies["postgres"] = "healthy"
    except Exception as e:
        dependencies["postgres"] = f"unhealthy: {str(e)}"
        logger.error("PostgreSQL health check failed", error=str(e))

    # Check Redis
    try:
        redis = await get_redis_client()
        await redis.client.ping()
        dependencies["redis"] = "healthy"
    except Exception as e:
        dependencies["redis"] = f"unhealthy: {str(e)}"
        logger.error("Redis health check failed", error=str(e))

    # Determine overall status
    all_healthy = all(
        status == "healthy" for status in dependencies.values()
    )

    return HealthResponse(
        status="healthy" if all_healthy else "degraded",
        service="api-gateway",
        version="1.0.0",
        dependencies=dependencies,
    )


@router.get("/health/live")
async def liveness() -> dict[str, str]:
    """Kubernetes liveness probe."""
    return {"status": "alive"}


@router.get("/health/ready")
async def readiness() -> dict[str, str]:
    """Kubernetes readiness probe."""
    # Check if we can connect to critical dependencies
    try:
        async with get_session_context() as session:
            await session.execute(text("SELECT 1"))

        redis = await get_redis_client()
        await redis.client.ping()

        return {"status": "ready"}
    except Exception as e:
        logger.error("Readiness check failed", error=str(e))
        return {"status": "not ready", "error": str(e)}
