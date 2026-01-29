"""API Gateway - Control Plane entry point."""

from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from libs.common import setup_logging, get_logger, AgentSystemError
from libs.db import init_db, close_db
from libs.messaging.kafka import create_producer
from libs.messaging.redis import get_redis_client

from .config import get_config
from .routers import chat, jobs, health
from .middleware.auth import AuthMiddleware
from .middleware.rate_limit import RateLimitMiddleware
from .middleware.tenant import TenantMiddleware

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan handler."""
    config = get_config()

    # Setup logging
    setup_logging(
        service_name="api-gateway",
        log_level=config.log_level,
        log_format=config.log_format,
    )

    logger.info("Starting API Gateway")

    # Initialize connections
    await init_db()
    await get_redis_client()
    await create_producer()

    logger.info("API Gateway started successfully")

    yield

    # Cleanup
    logger.info("Shutting down API Gateway")
    await close_db()
    logger.info("API Gateway shutdown complete")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    config = get_config()

    app = FastAPI(
        title="Agent System API",
        description="Multi-tenant AI agent system with streaming support",
        version="1.0.0",
        docs_url="/docs" if config.debug else None,
        redoc_url="/redoc" if config.debug else None,
        lifespan=lifespan,
    )

    # CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # Configure appropriately for production
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Custom middleware (order matters - executed in reverse)
    app.add_middleware(TenantMiddleware)
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(AuthMiddleware)

    # Exception handler
    @app.exception_handler(AgentSystemError)
    async def agent_system_error_handler(
        request: Request, exc: AgentSystemError
    ) -> JSONResponse:
        logger.warning(
            "Request error",
            error_code=exc.code,
            error_message=exc.message,
            path=request.url.path,
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=exc.to_dict(),
        )

    @app.exception_handler(Exception)
    async def generic_error_handler(
        request: Request, exc: Exception
    ) -> JSONResponse:
        logger.exception(
            "Unhandled exception",
            path=request.url.path,
        )
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": "An unexpected error occurred",
                }
            },
        )

    # Include routers
    app.include_router(health.router, tags=["Health"])
    app.include_router(chat.router, prefix="/api/v1", tags=["Chat"])
    app.include_router(jobs.router, prefix="/api/v1", tags=["Jobs"])

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    config = get_config()
    uvicorn.run(
        "src.main:app",
        host="0.0.0.0",
        port=config.api_gateway_port,
        reload=config.debug,
    )
