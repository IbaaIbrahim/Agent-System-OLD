"""API Gateway - Control Plane entry point."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from libs.common import AgentSystemError, get_logger, setup_logging
from libs.db import close_db, init_db
from libs.messaging.kafka import create_producer
from libs.messaging.redis import get_redis_client

from .config import get_config
from .middleware.auth import AuthMiddleware
from .middleware.rate_limit import RateLimitMiddleware
from .middleware.tenant import TenantMiddleware
from .routers import admin, auth, chat, health, jobs, partners, users

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

    def custom_openapi():
        if app.openapi_schema:
            return app.openapi_schema

        from fastapi.openapi.utils import get_openapi

        openapi_schema = get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            routes=app.routes,
        )

        # Add security schemes
        openapi_schema["components"]["securitySchemes"] = {
            "BearerAuth": {
                "type": "http",
                "scheme": "bearer",
                "bearerFormat": "JWT",
                "description": "For User JWT tokens or Platform Owner Master Key (prefix with Bearer)",
            },
            "ApiKeyAuth": {
                "type": "apiKey",
                "in": "header",
                "name": "Authorization",
                "description": "For Tenant API Keys (starts with sk-agent-, no Bearer prefix needed)",
            },
            "PartnerApiKeyAuth": {
                "type": "apiKey",
                "in": "header",
                "name": "Authorization",
                "description": "For Partner API Keys (starts with pk-agent-, prefix with Bearer)",
            },
        }

        # Apply security globally
        openapi_schema["security"] = [
            {"BearerAuth": []},
            {"ApiKeyAuth": []},
            {"PartnerApiKeyAuth": []},
        ]

        app.openapi_schema = openapi_schema
        return app.openapi_schema

    app.openapi = custom_openapi

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

    # Admin endpoints (platform owner only)
    app.include_router(admin.router, prefix="/api", tags=["Admin"])

    # Partner management endpoints (platform owner only)
    app.include_router(partners.router, prefix="/api", tags=["Partners"])

    # Authentication endpoints
    app.include_router(auth.router, prefix="/api", tags=["Auth"])

    # User management endpoints (tenant API key required)
    app.include_router(users.router, prefix="/api", tags=["Users"])

    # Chat and job endpoints
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
