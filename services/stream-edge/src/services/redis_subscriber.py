"""Redis subscriber for job events."""

import asyncio
from typing import Any
from uuid import UUID

from libs.common import get_logger
from libs.messaging.redis import RedisPubSub

from ..handlers.connection import ConnectionManager

logger = get_logger(__name__)


class RedisEventSubscriber:
    """Subscribes to Redis pub/sub for job events and broadcasts to SSE connections."""

    def __init__(self, connection_manager: ConnectionManager) -> None:
        self.connection_manager = connection_manager
        self.pubsub = RedisPubSub()
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start the subscriber."""
        await self.pubsub.connect()
        await self.pubsub.psubscribe("job:*", handler=self._handle_event)
        self._running = True
        self._task = asyncio.create_task(self._run())
        logger.info("Redis event subscriber started")

    async def stop(self) -> None:
        """Stop the subscriber."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        await self.pubsub.disconnect()
        logger.info("Redis event subscriber stopped")

    async def _run(self) -> None:
        """Run the subscription loop."""
        try:
            await self.pubsub.run()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("Error in Redis subscriber", error=str(e))
            raise

    async def _handle_event(
        self,
        channel: str,
        data: dict[str, Any],
    ) -> None:
        """Handle an incoming event from Redis.

        Args:
            channel: Channel the event came from (e.g., "job:uuid")
            data: Event payload
        """
        try:
            # Extract job ID from channel
            if not channel.startswith("job:"):
                return

            job_id_str = channel[4:]  # Remove "job:" prefix
            job_id = UUID(job_id_str)

            event_type = data.get("type", "message")
            event_data = data.get("data", {})
            event_id = data.get("id")

            # Broadcast to all SSE connections for this job
            sent_count = await self.connection_manager.send_event(
                job_id=job_id,
                event_type=event_type,
                data=event_data,
                event_id=event_id,
            )

            logger.debug(
                "Event broadcast",
                job_id=str(job_id),
                event_type=event_type,
                connections=sent_count,
            )

        except Exception as e:
            logger.error(
                "Error handling Redis event",
                channel=channel,
                error=str(e),
            )


async def publish_event(
    job_id: UUID,
    event_type: str,
    data: dict[str, Any],
    event_id: str | None = None,
) -> None:
    """Publish an event to Redis for broadcasting.

    Args:
        job_id: Target job ID
        event_type: Event type
        data: Event payload
        event_id: Optional event ID for client resumption
    """
    pubsub = RedisPubSub()
    await pubsub.connect()

    channel = f"job:{job_id}"
    message = {
        "type": event_type,
        "data": data,
    }
    if event_id:
        message["id"] = event_id

    await pubsub.publish(channel, message)
    await pubsub.disconnect()
