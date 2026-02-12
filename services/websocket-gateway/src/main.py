"""WebSocket Gateway - Bidirectional real-time communication for Live Assistant."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from libs.common import AgentSystemError, get_logger, setup_logging
from libs.common.exceptions import AuthenticationError
from libs.messaging.redis import get_redis_client

from .config import get_config
from .handlers.ws_handler import handle_ws_connection, registry
from .middleware.auth import authenticate_ws_token

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan handler."""
    config = get_config()

    setup_logging(
        service_name="websocket-gateway",
        log_level=config.log_level,
        log_format=config.log_format,
    )

    logger.info("Starting WebSocket Gateway")

    redis = await get_redis_client()

    logger.info("WebSocket Gateway started successfully")

    yield

    logger.info("Shutting down WebSocket Gateway")
    await registry.close_all()
    await redis.close()
    logger.info("WebSocket Gateway shutdown complete")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    config = get_config()

    app = FastAPI(
        title="Agent System WebSocket Gateway",
        description="Bidirectional real-time communication for Live Assistant",
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
        allow_methods=["*"],
        allow_headers=["*"],
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

    # Health endpoint
    @app.get("/health")
    async def health_check() -> dict:
        return {
            "status": "healthy",
            "service": "websocket-gateway",
            "version": "1.0.0",
            "connections": registry.total_connections,
        }

    # WebSocket endpoint
    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        """Main WebSocket endpoint.

        Protocol:
        1. Client connects to ws://host:8002/ws
        2. First message must be: {"type": "auth", "token": "Bearer ..."}
        3. On success: {"type": "connected", "connection_id": "..."}
        4. Then: start_session, audio, screen_frame, control messages
        """
        await websocket.accept()

        try:
            # Wait for auth message (timeout 10s)
            import asyncio

            try:
                raw = await asyncio.wait_for(
                    websocket.receive_text(), timeout=10.0
                )
            except asyncio.TimeoutError:
                await websocket.send_json({
                    "type": "error",
                    "code": "AUTH_TIMEOUT",
                    "message": "Authentication timeout. Send auth message within 10 seconds.",
                })
                await websocket.close(code=4001, reason="Auth timeout")
                return

            import json

            try:
                auth_msg = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_json({
                    "type": "error",
                    "code": "INVALID_JSON",
                    "message": "First message must be valid JSON",
                })
                await websocket.close(code=4002, reason="Invalid JSON")
                return

            if auth_msg.get("type") != "auth" or not auth_msg.get("token"):
                await websocket.send_json({
                    "type": "error",
                    "code": "AUTH_REQUIRED",
                    "message": "First message must be: {\"type\": \"auth\", \"token\": \"Bearer ...\"}",
                })
                await websocket.close(code=4003, reason="Auth required")
                return

            # Authenticate
            try:
                auth_context = await authenticate_ws_token(auth_msg["token"])
            except AuthenticationError as e:
                await websocket.send_json({
                    "type": "error",
                    "code": "AUTH_FAILED",
                    "message": str(e.message),
                })
                await websocket.close(code=4004, reason="Auth failed")
                return

            # Check tenant connection limits
            if auth_context.tenant_id:
                current_count = registry.tenant_connection_count(
                    auth_context.tenant_id
                )
                if current_count >= get_config().max_connections_per_tenant:
                    await websocket.send_json({
                        "type": "error",
                        "code": "CONNECTION_LIMIT",
                        "message": "Too many active connections for this tenant",
                    })
                    await websocket.close(code=4005, reason="Connection limit")
                    return

            # Hand off to the connection handler
            await handle_ws_connection(websocket, auth_context)

        except WebSocketDisconnect:
            logger.debug("WebSocket disconnected during auth")
        except Exception as e:
            logger.error(
                "Unexpected WebSocket error",
                error=str(e),
                error_type=type(e).__name__,
                exc_info=True,
            )
            try:
                await websocket.close(code=1011, reason="Internal error")
            except Exception:
                pass

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    config = get_config()
    uvicorn.run(
        "src.main:app",
        host="0.0.0.0",
        port=config.websocket_gateway_port,
        reload=config.debug,
    )
