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
        # Store in streams for durable catch-up
        stream_key = f"events:{job_id}"
        event_id = None
        try:
            event_id = await self.streams.add(
                stream=stream_key,
                data={
                    "type": event_type,
                    "data": data,
                },
            )
        except Exception as e:
            logger.error(
                "Failed to add to Redis stream",
                job_id=str(job_id),
                event_type=event_type,
                error=str(e),
            )

        # Publish to pub/sub for real-time delivery (SSE)
        channel = f"job:{job_id}"
        message = {
            "type": event_type,
            "data": data,
            "id": event_id,
        }

        # Retry logic for Pub/Sub publish (up to 3 attempts)
        max_retries = 3
        for attempt in range(max_retries):
            try:
                subscriber_count = await self.pubsub.publish(channel, message)
                if subscriber_count == 0:
                    logger.debug(
                        "Published to Pub/Sub but no subscribers",
                        job_id=str(job_id),
                        event_type=event_type,
                        attempt=attempt + 1,
                    )
                else:
                    logger.debug(
                        "Published to Pub/Sub",
                        job_id=str(job_id),
                        event_type=event_type,
                        subscribers=subscriber_count,
                    )
                break  # Success, exit retry loop
            except Exception as e:
                if attempt == max_retries - 1:
                    # Last attempt failed
                    logger.error(
                        "Failed to publish to Pub/Sub after retries",
                        job_id=str(job_id),
                        event_type=event_type,
                        error=str(e),
                        attempts=max_retries,
                    )
                else:
                    # Retry with exponential backoff
                    import asyncio
                    wait_time = 0.1 * (2 ** attempt)  # 0.1s, 0.2s, 0.4s
                    logger.warning(
                        "Pub/Sub publish failed, retrying",
                        job_id=str(job_id),
                        event_type=event_type,
                        attempt=attempt + 1,
                        error=str(e),
                    )
                    await asyncio.sleep(wait_time)
