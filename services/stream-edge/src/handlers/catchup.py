"""Catch-up handler for reconnecting clients."""

import time
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

from libs.common import get_logger
from libs.messaging.redis import RedisStreams, get_redis_client

from services.stream_edge.src.config import get_config

logger = get_logger(__name__)


class CatchupHandler:
    """Handles catch-up logic for reconnecting SSE clients.

    Implements a hot/cold catch-up strategy:
    - Hot: Recent events from Redis streams (within hot window)
    - Cold: Older events from database (beyond hot window)
    """

    def __init__(self) -> None:
        self.streams = RedisStreams()
        self.config = get_config()

    async def get_catchup_events(
        self,
        job_id: UUID,
        last_event_id: str | None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Get events for catch-up after reconnection.

        Args:
            job_id: Job ID to get events for
            last_event_id: Last event ID the client received

        Yields:
            Event dictionaries with type, data, and id
        """
        if not last_event_id:
            # No last event ID, start from beginning of hot window
            async for event in self._hot_catchup(job_id, "0"):
                yield event
            return

        # Parse last event ID to determine if hot or cold
        timestamp = self._extract_timestamp(last_event_id)
        now = time.time()
        hot_window_start = now - self.config.catchup_hot_window_seconds

        if timestamp and timestamp >= hot_window_start:
            # Event is within hot window, use Redis streams
            async for event in self._hot_catchup(job_id, last_event_id):
                yield event
        else:
            # Event is older, need cold catch-up from database
            async for event in self._cold_catchup(job_id, last_event_id):
                yield event
            # Then continue with hot events
            async for event in self._hot_catchup(job_id, "0"):
                yield event

    async def _hot_catchup(
        self,
        job_id: UUID,
        after_id: str,
    ) -> AsyncIterator[dict[str, Any]]:
        """Get events from Redis streams (hot path).

        Args:
            job_id: Job ID
            after_id: Start after this event ID

        Yields:
            Event dictionaries
        """
        stream_key = f"events:{job_id}"

        try:
            entries = await self.streams.read_after(
                stream=stream_key,
                after_id=after_id,
                count=self.config.catchup_max_events,
            )

            for entry in entries:
                event_type = entry.data.get("type", "message")
                event_data = entry.data.get("data", {})

                yield {
                    "type": event_type,
                    "data": event_data,
                    "id": entry.id,
                }

            logger.debug(
                "Hot catch-up complete",
                job_id=str(job_id),
                events_count=len(entries),
            )

        except Exception as e:
            logger.error(
                "Error in hot catch-up",
                job_id=str(job_id),
                error=str(e),
            )

    async def _cold_catchup(
        self,
        job_id: UUID,
        after_id: str,
    ) -> AsyncIterator[dict[str, Any]]:
        """Get events from database (cold path).

        Args:
            job_id: Job ID
            after_id: Start after this event ID

        Yields:
            Event dictionaries
        """
        # For cold catch-up, we'd typically query the database
        # This is a simplified implementation that could be extended
        from libs.db import get_session_context
        from libs.db.models import ChatMessage
        from sqlalchemy import select

        try:
            # Parse sequence number from event ID if possible
            sequence_num = 0
            if after_id and "-" in after_id:
                try:
                    # Event IDs might be in format "timestamp-sequence"
                    sequence_num = int(after_id.split("-")[-1])
                except ValueError:
                    pass

            async with get_session_context() as session:
                result = await session.execute(
                    select(ChatMessage)
                    .where(ChatMessage.job_id == job_id)
                    .where(ChatMessage.sequence_num > sequence_num)
                    .order_by(ChatMessage.sequence_num)
                    .limit(self.config.catchup_max_events)
                )
                messages = result.scalars().all()

                for msg in messages:
                    yield {
                        "type": "message",
                        "data": {
                            "role": msg.role.value,
                            "content": msg.content,
                            "tool_calls": msg.tool_calls,
                            "tool_call_id": msg.tool_call_id,
                        },
                        "id": f"{int(msg.created_at.timestamp() * 1000)}-{msg.sequence_num}",
                    }

                logger.debug(
                    "Cold catch-up complete",
                    job_id=str(job_id),
                    messages_count=len(messages),
                )

        except Exception as e:
            logger.error(
                "Error in cold catch-up",
                job_id=str(job_id),
                error=str(e),
            )

    def _extract_timestamp(self, event_id: str) -> float | None:
        """Extract timestamp from event ID.

        Redis stream IDs are in format "timestamp-sequence"
        """
        try:
            parts = event_id.split("-")
            if parts:
                return int(parts[0]) / 1000  # Convert ms to seconds
        except (ValueError, IndexError):
            pass
        return None


async def store_event(
    job_id: UUID,
    event_type: str,
    data: dict[str, Any],
) -> str:
    """Store an event in Redis streams for catch-up.

    Args:
        job_id: Job ID
        event_type: Event type
        data: Event payload

    Returns:
        Generated event ID
    """
    streams = RedisStreams()
    stream_key = f"events:{job_id}"

    event_id = await streams.add(
        stream=stream_key,
        data={
            "type": event_type,
            "data": data,
        },
    )

    return event_id
