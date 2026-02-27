"""Handler for incoming screen frames."""

import asyncio
import json

from libs.common import get_logger
from libs.messaging.redis import RedisPubSub, get_redis_client

from ..session.manager import SessionManager
from ..session.state import SessionState
from ..vision.frame_processor import FrameProcessor

logger = get_logger(__name__)


async def listen_frame_events(
    session_manager: SessionManager,
    frame_processor: FrameProcessor,
) -> None:
    """Listen for screen frames on Redis and process them.

    Each session publishes frames to `live_session:{session_id}:frames`.
    We listen for all session frame channels and process them.
    """
    pubsub = RedisPubSub()
    await pubsub.connect()

    # Use pattern subscription for all sessions
    await pubsub.subscribe("live_session:*:frames")

    try:
        async for channel, data in pubsub.listen():
            # Extract session_id from channel (live_session:{id}:frames)
            parts = channel.split(":")
            if len(parts) < 3:
                continue
            session_id = parts[1]

            session = session_manager.get_session(session_id)
            if not session or session.state == SessionState.ENDED:
                continue

            frame_base64 = data.get("data", "")
            context = data.get("context", "Describe what is visible on screen.")

            if not frame_base64:
                continue

            session.screen_frames_count += 1

            # Process frame in background
            asyncio.create_task(
                _process_and_analyze_frame(
                    session_id=session_id,
                    frame_base64=frame_base64,
                    context=context,
                    tenant_id=session.tenant_id,
                    user_id=session.user_id,
                    frame_processor=frame_processor,
                )
            )

    except asyncio.CancelledError:
        pass
    finally:
        await pubsub.disconnect()


async def _process_and_analyze_frame(
    session_id: str,
    frame_base64: str,
    context: str,
    tenant_id: str,
    user_id: str | None,
    frame_processor: FrameProcessor,
) -> None:
    """Process a frame and notify client of the file_id."""
    try:
        file_id = await frame_processor.process_frame(
            frame_base64=frame_base64,
            tenant_id=tenant_id,
            user_id=user_id,
        )

        # Notify client that frame was captured
        redis = await get_redis_client()
        await redis.publish(
            f"live_session:{session_id}",
            json.dumps({
                "type": "frame_captured",
                "file_id": file_id,
                "context": context,
            }),
        )

        logger.debug(
            "Frame processed",
            session_id=session_id,
            file_id=file_id,
        )

    except Exception as e:
        logger.error(
            "Frame processing error",
            session_id=session_id,
            error=str(e),
        )
