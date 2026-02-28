"""Stream Edge - Data Plane entry point for SSE streaming."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from libs.common import AgentSystemError, get_logger, setup_logging
from libs.db import close_db, init_db
from libs.messaging.redis import get_redis_client

from .config import get_config
from .handlers.connection import ConnectionManager
from .routers import events

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan handler."""
    config = get_config()

    # Setup logging
    setup_logging(
        service_name="stream-edge",
        log_level=config.log_level,
        log_format=config.log_format,
    )

    logger.info("Starting Stream Edge")

    # Initialize connections
    await init_db()
    await get_redis_client()

    # Initialize connection manager
    app.state.connection_manager = ConnectionManager()

    logger.info("Stream Edge started successfully")

    try:
        yield
    finally:
        # Cleanup always runs (normal shutdown or Ctrl+C)
        try:
            logger.info("Shutting down Stream Edge")
            await app.state.connection_manager.close_all()
            await close_db()
            logger.info("Stream Edge shutdown complete")
        except asyncio.CancelledError:
            logger.info("Stream Edge shutdown complete (cancelled)")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    config = get_config()

    app = FastAPI(
        title="Agent System Stream Edge",
        description="SSE streaming endpoint for real-time events",
        version="1.0.0",
        docs_url="/docs" if config.debug else None,
        redoc_url=None,
        lifespan=lifespan,
    )

    # CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["GET", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID", "Last-Event-ID"],
    )

    # Exception handlers
    @app.exception_handler(AgentSystemError)
    async def agent_system_error_handler(
        request: Request, exc: AgentSystemError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=exc.to_dict(),
        )

    @app.exception_handler(Exception)
    async def generic_error_handler(
        request: Request, exc: Exception
    ) -> JSONResponse:
        logger.exception("Unhandled exception", path=request.url.path)
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": "An unexpected error occurred",
                }
            },
        )

    # Health endpoint
    @app.get("/health")
    async def health_check() -> dict:
        return {
            "status": "healthy",
            "service": "stream-edge",
            "version": "1.0.0",
        }

    # Include routers
    app.include_router(events.router, prefix="/api/v1", tags=["Events"])

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    config = get_config()
    uvicorn.run(
        "src.main:app",
        host="0.0.0.0",
        port=config.stream_edge_port,
        reload=config.debug,
    )
