"""WebSocket message handler.

Routes incoming WS messages to appropriate handlers and manages
the bidirectional bridge between clients and the Live Session Manager.
"""

import asyncio
import json
import uuid
from dataclasses import dataclass, field

from fastapi import WebSocket, WebSocketDisconnect

from libs.common import get_logger
from libs.messaging.redis import RedisPubSub, get_redis_client

from ..middleware.auth import AuthContext

logger = get_logger(__name__)


@dataclass
class LiveConnection:
    """Tracks state for an active WebSocket connection."""

    connection_id: str
    websocket: WebSocket
    auth: AuthContext
    session_id: str | None = None
    is_active: bool = True
    _redis_task: asyncio.Task | None = field(default=None, repr=False)


class ConnectionRegistry:
    """Manages active WebSocket connections."""

    def __init__(self) -> None:
        self._connections: dict[str, LiveConnection] = {}
        self._tenant_counts: dict[str, int] = {}

    def add(self, conn: LiveConnection) -> None:
        self._connections[conn.connection_id] = conn
        tenant_key = str(conn.auth.tenant_id) if conn.auth.tenant_id else "platform"
        self._tenant_counts[tenant_key] = self._tenant_counts.get(tenant_key, 0) + 1

    def remove(self, connection_id: str) -> None:
        conn = self._connections.pop(connection_id, None)
        if conn:
            conn.is_active = False
            tenant_key = str(conn.auth.tenant_id) if conn.auth.tenant_id else "platform"
            self._tenant_counts[tenant_key] = max(
                0, self._tenant_counts.get(tenant_key, 1) - 1
            )

    def get(self, connection_id: str) -> LiveConnection | None:
        return self._connections.get(connection_id)

    def tenant_connection_count(self, tenant_id: uuid.UUID) -> int:
        return self._tenant_counts.get(str(tenant_id), 0)

    @property
    def total_connections(self) -> int:
        return len(self._connections)

    async def close_all(self) -> None:
        for conn in list(self._connections.values()):
            conn.is_active = False
            if conn._redis_task and not conn._redis_task.done():
                conn._redis_task.cancel()
            try:
                await conn.websocket.close()
            except Exception:
                pass
        self._connections.clear()
        self._tenant_counts.clear()


# Global registry
registry = ConnectionRegistry()


async def handle_ws_connection(websocket: WebSocket, auth: AuthContext) -> None:
    """Handle an authenticated WebSocket connection.

    Listens for messages from the client, routes them, and
    subscribes to Redis for server-to-client events.
    """
    connection_id = str(uuid.uuid4())
    conn = LiveConnection(
        connection_id=connection_id,
        websocket=websocket,
        auth=auth,
    )
    registry.add(conn)

    logger.info(
        "WebSocket connection established",
        connection_id=connection_id,
        tenant_id=str(auth.tenant_id),
        user_id=str(auth.user_id) if auth.user_id else None,
        auth_tier=auth.auth_tier,
    )
    logger.debug("Starting message loop for connection", connection_id=connection_id)

    try:
        # Send connection confirmation
        await websocket.send_json({
            "type": "connected",
            "connection_id": connection_id,
        })

        # Main message loop
        while conn.is_active:
            try:
                raw = await websocket.receive_text()
            except WebSocketDisconnect:
                break

            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_json({
                    "type": "error",
                    "code": "INVALID_JSON",
                    "message": "Message must be valid JSON",
                })
                continue

            msg_type = message.get("type")
            if not msg_type:
                await websocket.send_json({
                    "type": "error",
                    "code": "MISSING_TYPE",
                    "message": "Message must include 'type' field",
                })
                continue

            await _route_message(conn, message)

    except Exception as e:
        logger.error(
            "WebSocket error",
            connection_id=connection_id,
            error=str(e),
        )
    finally:
        # Clean up Redis subscription
        if conn._redis_task and not conn._redis_task.done():
            conn._redis_task.cancel()
            try:
                await conn._redis_task
            except asyncio.CancelledError:
                pass

        registry.remove(connection_id)
        logger.info(
            "WebSocket connection closed",
            connection_id=connection_id,
        )


async def _route_message(conn: LiveConnection, message: dict) -> None:
    """Route an incoming WebSocket message to the appropriate handler."""
    msg_type = message["type"]

    if msg_type == "start_session":
        await _handle_start_session(conn, message)
    elif msg_type == "audio":
        await _handle_audio(conn, message)
    elif msg_type == "screen_frame":
        await _handle_screen_frame(conn, message)
    elif msg_type == "control":
        await _handle_control(conn, message)
    elif msg_type == "ping":
        await conn.websocket.send_json({"type": "pong"})
    else:
        await conn.websocket.send_json({
            "type": "error",
            "code": "UNKNOWN_TYPE",
            "message": f"Unknown message type: {msg_type}",
        })


async def _handle_start_session(conn: LiveConnection, message: dict) -> None:
    """Start a new live session and subscribe to its Redis events."""
    if conn.session_id:
        await conn.websocket.send_json({
            "type": "error",
            "code": "SESSION_EXISTS",
            "message": "A session is already active on this connection",
        })
        return

    session_id = str(uuid.uuid4())
    conn.session_id = session_id

    # Store session info in Redis for the Live Session Manager to pick up
    redis = await get_redis_client()
    session_data = {
        "session_id": session_id,
        "connection_id": conn.connection_id,
        "tenant_id": str(conn.auth.tenant_id),
        "user_id": str(conn.auth.user_id) if conn.auth.user_id else None,
        "partner_id": str(conn.auth.partner_id) if conn.auth.partner_id else None,
        "language": message.get("language", "en"),
        "tts_voice_id": message.get("tts_voice_id"),
        "conversation_id": message.get("conversation_id"),
    }
    await redis.set(
        f"live_session:{session_id}",
        json.dumps(session_data),
        ex=3600,  # 1 hour TTL
    )

    # Publish session start event for the Live Session Manager
    event = {"action": "start", **session_data}
    logger.debug("Publishing session start event", session_id=session_id, event_data=event)
    count = await redis.publish(
        "live_sessions:control",
        json.dumps(event),
    )
    logger.info("Session start event published", subscribers=count, session_id=session_id)

    # Subscribe to events for this session
    conn._redis_task = asyncio.create_task(
        _subscribe_session_events(conn, session_id)
    )

    await conn.websocket.send_json({
        "type": "session_started",
        "session_id": session_id,
    })

    logger.info(
        "Live session started",
        session_id=session_id,
        connection_id=conn.connection_id,
        tenant_id=str(conn.auth.tenant_id),
    )


async def _subscribe_session_events(conn: LiveConnection, session_id: str) -> None:
    """Subscribe to Redis Pub/Sub for live session events and forward to WS."""
    pubsub = RedisPubSub()
    await pubsub.connect()
    channel = f"live_session:{session_id}"
    await pubsub.subscribe(channel)

    try:
        async for _channel, event_data in pubsub.listen():
            if not conn.is_active:
                break

            # Forward event to WebSocket client
            try:
                await conn.websocket.send_json(event_data)
            except Exception:
                break
    except asyncio.CancelledError:
        pass
    finally:
        await pubsub.unsubscribe(channel)
        await pubsub.disconnect()


async def _handle_audio(conn: LiveConnection, message: dict) -> None:
    """Forward audio chunk to Live Session Manager via Redis."""
    if not conn.session_id:
        await conn.websocket.send_json({
            "type": "error",
            "code": "NO_SESSION",
            "message": "Start a session before sending audio",
        })
        return

    redis = await get_redis_client()
    audio_data = message.get("data", "")
    logger.debug("Received audio from client", session_id=conn.session_id, data_len=len(audio_data))
    await redis.publish(
        f"live_session:{conn.session_id}:audio_in",
        json.dumps({
            "data": audio_data,
            "seq": message.get("seq", 0),
            "sample_rate": message.get("sample_rate", 16000),
        }),
    )


async def _handle_screen_frame(conn: LiveConnection, message: dict) -> None:
    """Forward screen frame to Live Session Manager via Redis."""
    if not conn.session_id:
        await conn.websocket.send_json({
            "type": "error",
            "code": "NO_SESSION",
            "message": "Start a session before sending frames",
        })
        return

    redis = await get_redis_client()
    await redis.publish(
        f"live_session:{conn.session_id}:frames",
        json.dumps({
            "data": message.get("data", ""),
            "context": message.get("context", "Describe what is visible on the screen."),
            "timestamp": message.get("timestamp"),
        }),
    )


async def _handle_control(conn: LiveConnection, message: dict) -> None:
    """Handle control messages (interrupt, pause, resume, end)."""
    action = message.get("action")
    if not action:
        await conn.websocket.send_json({
            "type": "error",
            "code": "MISSING_ACTION",
            "message": "Control message must include 'action' field",
        })
        return

    if not conn.session_id:
        await conn.websocket.send_json({
            "type": "error",
            "code": "NO_SESSION",
            "message": "No active session",
        })
        return

    redis = await get_redis_client()

    if action == "end":
        # Publish end event and clean up
        await redis.publish(
            "live_sessions:control",
            json.dumps({
                "action": "end",
                "session_id": conn.session_id,
            }),
        )
        # Cancel Redis subscription
        if conn._redis_task and not conn._redis_task.done():
            conn._redis_task.cancel()
        conn.session_id = None

        await conn.websocket.send_json({"type": "session_ended"})
    elif action in ("pause", "resume", "interrupt"):
        await redis.publish(
            "live_sessions:control",
            json.dumps({
                "action": action,
                "session_id": conn.session_id,
            }),
        )
        await conn.websocket.send_json({
            "type": "status",
            "state": "paused" if action == "pause" else "listening",
        })
    else:
        await conn.websocket.send_json({
            "type": "error",
            "code": "INVALID_ACTION",
            "message": f"Unknown action: {action}",
        })
