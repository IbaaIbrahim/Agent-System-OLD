"""SSE streaming endpoint."""

import asyncio
import json
import uuid
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from libs.common import get_logger
from libs.common.auth import verify_stream_ott
from libs.common.config import get_settings
from libs.common.exceptions import AuthenticationError
from libs.messaging.redis import RedisPubSub, get_redis_client

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
        # Send an immediate SSE comment so the client receives a chunk right away (avoids blank
        # stream due to buffering and confirms the connection is live).
        yield ": connected\n\n"

        # Send initial retry interval
        yield format_sse_event(
            data={"status": "connected", "job_id": str(job_id)},
            event_type="open",
            retry=config.sse_retry_ms,
        )

        last_yielded_id = last_event_id

        # Always handle catch-up to ensure no events missed between job creation and SSE connection
        catchup_handler = CatchupHandler()
        catchup_count = 0
        async for event in catchup_handler.get_catchup_events(job_id, last_event_id):
            if not connection.is_active:
                break

            event_id = event.get("id")
            yield format_sse_event(
                data=event["data"],
                event_type=event["type"],
                event_id=event_id,
            )
            catchup_count += 1

            if event_id:
                last_yielded_id = event_id
                connection.last_event_id = event_id

        if catchup_count > 0:
            logger.info(
                "Stream-edge: catchup yielded events",
                job_id=str(job_id),
                count=catchup_count,
            )

        # Listen for real-time events
        logger.info(
            "Stream-edge: subscribed to Redis, waiting for events",
            job_id=str(job_id),
        )
        keepalive_task = asyncio.create_task(
            keepalive_generator(connection, config.sse_keepalive_interval)
        )

        first_received = True
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

                if first_received:
                    logger.info(
                        "Stream-edge: first event from Redis",
                        job_id=str(job_id),
                        event_type=event_type,
                    )
                    first_received = False

                # De-duplicate: skip if already yielded via catch-up
                if event_id and last_yielded_id:
                    # Redis Stream IDs are timestamp-seq, so simple string comparison works
                    # for most cases, but we should be careful.
                    # If event_id <= last_yielded_id, skip it.
                    if event_id <= last_yielded_id:
                        continue

                logger.debug(
                    "Stream-edge: yielding to client",
                    job_id=str(job_id),
                    event_type=event_type,
                )
                yield format_sse_event(
                    data=payload,
                    event_type=event_type,
                    event_id=event_id,
                )

                # Update last event ID
                if event_id:
                    last_yielded_id = event_id
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


@router.get("/stream")
async def stream_events_authenticated(
    request: Request,
    token: str = Query(..., description="One-time stream token from chat completions"),
) -> StreamingResponse:
    """Stream events for a job via SSE, authenticated by one-time token.

    The token is a short-lived JWT issued by the API Gateway when creating
    a chat completion. It encodes the job_id and auth context.

    This endpoint validates the token signature, checks it hasn't been
    used before (one-time via Redis SETNX), extracts the job_id, and
    subscribes to the event stream.

    For reconnections (Last-Event-ID header present), consumed tokens
    are accepted as long as the signature and expiry are still valid.
    """
    # Step 1: Verify token signature and expiry
    try:
        ott_payload = verify_stream_ott(token)
    except AuthenticationError as e:
        raise HTTPException(status_code=401, detail=str(e.message))

    # Step 2: One-time consumption check via Redis SETNX
    redis = await get_redis_client()
    settings = get_settings()
    ott_key = f"ott:{ott_payload.jti}"
    was_set = await redis.set(ott_key, "1", ex=settings.ott_ttl_seconds, nx=True)

    if not was_set:
        # Token already consumed — only allow reconnections
        last_event_id = request.headers.get("Last-Event-ID")
        if not last_event_id:
            raise HTTPException(
                status_code=401,
                detail="Stream token has already been used",
            )

    # Step 3: Extract job_id and stream
    job_id = uuid.UUID(ott_payload.job_id)
    last_event_id = request.headers.get("Last-Event-ID")
    connection_id = str(uuid.uuid4())

    logger.info(
        "Authenticated SSE stream requested",
        job_id=str(job_id),
        tenant_id=ott_payload.tenant_id,
        connection_id=connection_id,
        last_event_id=last_event_id,
    )

    return StreamingResponse(
        event_generator(request, job_id, connection_id, last_event_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/events/{job_id}", deprecated=True)
async def stream_events(
    request: Request,
    job_id: uuid.UUID,
) -> StreamingResponse:
    """[DEPRECATED] Stream events for a job via Server-Sent Events.

    Use GET /api/v1/stream?token=... instead. This endpoint will be
    removed in a future release.
    """
    logger.warning(
        "Deprecated unauthenticated endpoint accessed",
        job_id=str(job_id),
        endpoint="/events/{job_id}",
        client_ip=request.client.host if request.client else "unknown",
    )

    # Check Last-Event-ID header for reconnection
    last_event_id = request.headers.get("Last-Event-ID")

    # Generate unique connection ID
    connection_id = str(uuid.uuid4())

    return StreamingResponse(
        event_generator(request, job_id, connection_id, last_event_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
