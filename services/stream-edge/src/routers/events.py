"""SSE streaming endpoint."""

import asyncio
import json
import uuid
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from libs.common import get_logger
from libs.messaging.redis import RedisPubSub

from ..config import get_config
from ..handlers.catchup import CatchupHandler
from ..handlers.connection import ConnectionManager

logger = get_logger(__name__)

router = APIRouter()


def format_sse_event(
    data: dict[str, Any],
    event_type: str | None = None,
    event_id: str | None = None,
    retry: int | None = None,
) -> str:
    """Format a Server-Sent Event message.

    Args:
        data: Event data (will be JSON serialized)
        event_type: Optional event type
        event_id: Optional event ID for resumption
        retry: Optional retry interval in milliseconds

    Returns:
        Formatted SSE message
    """
    lines = []

    if event_id:
        lines.append(f"id: {event_id}")

    if event_type:
        lines.append(f"event: {event_type}")

    if retry:
        lines.append(f"retry: {retry}")

    # JSON serialize the data
    json_data = json.dumps(data)
    lines.append(f"data: {json_data}")

    # SSE messages end with double newline
    return "\n".join(lines) + "\n\n"


async def event_generator(
    request: Request,
    job_id: uuid.UUID,
    connection_id: str,
    last_event_id: str | None,
):
    """Generate SSE events for a job.

    Args:
        request: FastAPI request
        job_id: Job to stream events for
        connection_id: Unique connection ID
        last_event_id: Last event ID for resumption

    Yields:
        Formatted SSE messages
    """
    config = get_config()
    connection_manager: ConnectionManager = request.app.state.connection_manager

    # Register connection
    connection = await connection_manager.connect(
        connection_id=connection_id,
        job_id=job_id,
        last_event_id=last_event_id,
    )

    # Set up Redis pub/sub for real-time events
    pubsub = RedisPubSub()
    await pubsub.connect()
    channel = f"job:{job_id}"
    await pubsub.subscribe(channel)

    try:
        # Send initial retry interval
        yield format_sse_event(
            data={"status": "connected", "job_id": str(job_id)},
            event_type="open",
            retry=config.sse_retry_ms,
        )

        # Handle catch-up if reconnecting
        if last_event_id:
            catchup_handler = CatchupHandler()
            async for event in catchup_handler.get_catchup_events(job_id, last_event_id):
                if not connection.is_active:
                    break
                yield format_sse_event(
                    data=event["data"],
                    event_type=event["type"],
                    event_id=event["id"],
                )

        # Listen for real-time events
        keepalive_task = asyncio.create_task(
            keepalive_generator(connection, config.sse_keepalive_interval)
        )

        try:
            async for _event_channel, event_data in pubsub.listen():
                if not connection.is_active:
                    break

                # Check if client disconnected
                if await request.is_disconnected():
                    break

                # Forward event to client
                event_type = event_data.get("type", "message")
                event_id = event_data.get("id")
                payload = event_data.get("data", event_data)

                yield format_sse_event(
                    data=payload,
                    event_type=event_type,
                    event_id=event_id,
                )

                # Update last event ID
                if event_id:
                    connection.last_event_id = event_id

                # Check for completion events
                if event_type in ("complete", "error", "cancelled"):
                    break

        finally:
            keepalive_task.cancel()
            try:
                await keepalive_task
            except asyncio.CancelledError:
                pass

    except asyncio.CancelledError:
        logger.debug("SSE stream cancelled", connection_id=connection_id)
    except Exception as e:
        logger.error(
            "Error in SSE stream",
            connection_id=connection_id,
            error=str(e),
        )
        yield format_sse_event(
            data={"error": str(e)},
            event_type="error",
        )
    finally:
        await pubsub.disconnect()
        await connection_manager.disconnect(connection_id)


async def keepalive_generator(connection, interval: int):
    """Send periodic keepalive comments to prevent timeout.

    Args:
        connection: SSE connection
        interval: Interval in seconds
    """
    while connection.is_active:
        await asyncio.sleep(interval)
        if connection.is_active:
            try:
                await connection.queue.put({
                    "type": "keepalive",
                    "data": {},
                    "id": None,
                })
            except Exception:
                break


@router.get("/events/{job_id}")
async def stream_events(
    request: Request,
    job_id: uuid.UUID,
) -> StreamingResponse:
    """Stream events for a job via Server-Sent Events.

    Connect to this endpoint to receive real-time events for a chat completion job.
    The stream will emit events as the LLM generates responses and tools are invoked.

    Supports automatic reconnection with catch-up via the Last-Event-ID header.

    Event types:
    - open: Connection established
    - message: Content chunk from LLM
    - tool_call: Tool invocation started
    - tool_result: Tool execution completed
    - complete: Job finished successfully
    - error: Job failed
    - keepalive: Connection keepalive (ignore these)
    """
    # Check Last-Event-ID header for reconnection
    last_event_id = request.headers.get("Last-Event-ID")

    # Generate unique connection ID
    connection_id = str(uuid.uuid4())

    logger.info(
        "SSE stream requested",
        job_id=str(job_id),
        connection_id=connection_id,
        last_event_id=last_event_id,
    )

    return StreamingResponse(
        event_generator(request, job_id, connection_id, last_event_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        },
    )
