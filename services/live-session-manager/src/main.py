"""Live Session Manager — orchestrates real-time voice and vision pipelines."""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from libs.common import AgentSystemError, get_logger, setup_logging
from libs.db import close_db, init_db
from libs.messaging.kafka import get_producer
from libs.messaging.redis import RedisPubSub, get_redis_client

from .config import get_config
from .handlers.frame_handler import listen_frame_events
from .session.manager import SessionManager
from .vision.frame_processor import FrameProcessor

logger = get_logger(__name__)

# Global session manager
session_manager = SessionManager()


async def listen_control_events() -> None:
    """Listen for session control events on Redis."""
    logger.info("Starting control events listener")
    pubsub = RedisPubSub()
    await pubsub.connect()
    await pubsub.subscribe("live_sessions:control")
    logger.info("Subscribed to control channel")

    try:
        async for _channel, data in pubsub.listen():
            logger.debug("Received control event", channel=_channel, data=data)
            action = data.get("action")
            session_id = data.get("session_id")

            if not action:
                continue

            if action == "start":
                logger.info("Starting session from control event", session_id=session_id)
                try:
                    await session_manager.start_session(data)
                except ValueError as e:
                    logger.error("Failed to start session", session_id=session_id, error=str(e))
            elif action == "end" and session_id:
                await session_manager.end_session(session_id)
            elif action == "pause" and session_id:
                await session_manager.pause_session(session_id)
            elif action == "resume" and session_id:
                await session_manager.resume_session(session_id)
            elif action == "interrupt" and session_id:
                await session_manager.interrupt_session(session_id)
            else:
                logger.warning("Unknown control action", action=action)

    except asyncio.CancelledError:
        pass
    finally:
        await pubsub.disconnect()
        logger.info("Control events listener stopped")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan handler."""
    config = get_config()

    setup_logging(
        service_name="live-session-manager",
        log_level=config.log_level,
        log_format=config.log_format,
    )

    logger.info("Starting Live Session Manager")

    await init_db()
    await get_redis_client()
    await get_producer()

    # Start background listeners
    control_task = asyncio.create_task(listen_control_events())
    frame_task = asyncio.create_task(
        listen_frame_events(session_manager, FrameProcessor())
    )

    logger.info("Live Session Manager started successfully")

    yield

    logger.info("Shutting down Live Session Manager")

    # Cancel background tasks
    control_task.cancel()
    frame_task.cancel()
    for task in [control_task, frame_task]:
        try:
            await task
        except asyncio.CancelledError:
            pass

    await session_manager.close_all()
    await close_db()
    logger.info("Live Session Manager shutdown complete")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    config = get_config()

    app = FastAPI(
        title="Agent System Live Session Manager",
        description="Real-time voice + vision pipeline orchestrator",
        version="1.0.0",
        docs_url="/docs" if config.debug else None,
        redoc_url=None,
        lifespan=lifespan,
    )

    @app.exception_handler(AgentSystemError)
    async def agent_system_error_handler(
        request: Request, exc: AgentSystemError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=exc.to_dict(),
        )

    @app.get("/health")
    async def health_check() -> dict:
        return {
            "status": "healthy",
            "service": "live-session-manager",
            "version": "1.0.0",
            "active_sessions": session_manager.active_count,
        }

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    config = get_config()
    uvicorn.run(
        "services.live_session_manager.src.main:app",
        host="0.0.0.0",
        port=config.live_session_manager_port,
        reload=config.debug,
    )
