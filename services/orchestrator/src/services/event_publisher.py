"""Service for publishing job events to Redis Pub/Sub and Streams."""

from typing import Any
from uuid import UUID

from libs.common import get_logger
from libs.messaging.redis import RedisPubSub, RedisStreams

logger = get_logger(__name__)


class EventPublisher:
    """Publishes job events for real-time streaming and persistence."""

    def __init__(self) -> None:
        self.pubsub = RedisPubSub()
        self.streams = RedisStreams()

    async def publish_event(
        self,
        job_id: UUID,
        event_type: str,
        data: dict[str, Any],
    ) -> None:
        """Publish an event to both Pub/Sub and Streams.

        Args:
            job_id: Job ID
            event_type: Event type (e.g., "delta", "start", "complete")
            data: Event payload
        """
        # Publish to pub/sub for real-time delivery (SSE)
        # Note: We don't call pubsub.connect() because we only use publish(),
        # which uses the direct Redis client and doesn't require a dedicated 
        # PubSub listener connection (and thus doesn't log spammy connects).
        channel = f"job:{job_id}"
        message = {
            "type": event_type,
            "data": data,
        }

        try:
            await self.pubsub.publish(channel, message)
        except Exception as e:
            logger.error(
                "Failed to publish to Pub/Sub",
                job_id=str(job_id),
                event_type=event_type,
                error=str(e),
            )

        # Store in streams for durable catch-up
        stream_key = f"events:{job_id}"
        try:
            event_id = await self.streams.add(
                stream=stream_key,
                data={
                    "type": event_type,
                    "data": data,
                },
            )
            # Update message with event ID for consistency if needed by future logic
            message["id"] = event_id
        except Exception as e:
            logger.error(
                "Failed to add to Redis stream",
                job_id=str(job_id),
                event_type=event_type,
                error=str(e),
            )
