"""Redis stream reader for consuming events."""

import asyncio
from uuid import UUID

from libs.common import get_logger
from libs.messaging.redis import RedisStreams, get_redis_client

from ..config import get_config
from .postgres_writer import PostgresWriter

logger = get_logger(__name__)


class RedisStreamReader:
    """Reads events from Redis streams for archiving."""

    def __init__(
        self,
        writer: PostgresWriter,
        consumer_group: str,
        consumer_name: str,
    ) -> None:
        self.writer = writer
        self.consumer_group = consumer_group
        self.consumer_name = consumer_name
        self.config = get_config()
        self.streams = RedisStreams()
        self._running = False

    async def start(self) -> None:
        """Start consuming events from Redis streams."""
        self._running = True
        logger.info(
            "Starting Redis stream reader",
            consumer_group=self.consumer_group,
            consumer_name=self.consumer_name,
        )

        while self._running:
            try:
                # Find all event streams
                stream_keys = await self._discover_streams()

                if not stream_keys:
                    await asyncio.sleep(1)
                    continue

                # Process each stream
                for stream_key in stream_keys:
                    if not self._running:
                        break

                    await self._process_stream(stream_key)

                # Small delay between iterations
                await asyncio.sleep(0.1)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error in stream reader", error=str(e))
                await asyncio.sleep(5)

    def stop(self) -> None:
        """Stop the reader."""
        self._running = False

    async def _discover_streams(self) -> list[str]:
        """Discover active event streams.

        Returns:
            List of stream keys matching pattern
        """
        redis = await get_redis_client()
        # Scan for keys matching events:* pattern
        keys = []
        cursor = 0

        while True:
            cursor, batch = await redis.client.scan(
                cursor=cursor,
                match="events:*",
                count=100,
            )
            keys.extend(batch)
            if cursor == 0:
                break

        return keys

    async def _process_stream(self, stream_key: str) -> None:
        """Process events from a single stream.

        Args:
            stream_key: Redis stream key (e.g., "events:job-uuid")
        """
        # Extract job ID from stream key
        job_id_str = stream_key.replace("events:", "")

        try:
            job_id = UUID(job_id_str)
        except ValueError:
            logger.warning(f"Invalid stream key: {stream_key}")
            return

        # Ensure consumer group exists
        try:
            await self.streams.create_group(
                stream=stream_key,
                group=self.consumer_group,
                start_id="0",
                mkstream=False,
            )
        except Exception:
            pass  # Group may already exist

        # Read pending entries first, then new ones
        entries = await self.streams.read_group(
            stream=stream_key,
            group=self.consumer_group,
            consumer=self.consumer_name,
            count=self.config.batch_size,
            block_ms=100,
        )

        if not entries:
            return

        # Process entries
        for entry in entries:
            event_data = {
                "job_id": job_id,
                "event_id": entry.id,
                "event_type": entry.data.get("type", "unknown"),
                "data": entry.data.get("data", {}),
                "timestamp_ms": entry.timestamp_ms,
            }

            # Add to writer batch
            await self.writer.add_event(event_data)

            # Acknowledge the entry
            await self.streams.ack(stream_key, self.consumer_group, entry.id)

        logger.debug(
            "Processed stream entries",
            stream=stream_key,
            count=len(entries),
        )

    async def cleanup_old_streams(self) -> None:
        """Clean up old streams that are no longer needed."""
        redis = await get_redis_client()
        stream_keys = await self._discover_streams()

        retention_ms = self.config.stream_retention_hours * 3600 * 1000
        current_time_ms = int(asyncio.get_event_loop().time() * 1000)
        cutoff_time_ms = current_time_ms - retention_ms

        for stream_key in stream_keys:
            try:
                # Get stream info
                info = await self.streams.info(stream_key)
                last_entry_id = info.get("last-entry-id", "0-0")

                # Parse timestamp from entry ID
                if "-" in last_entry_id:
                    last_timestamp = int(last_entry_id.split("-")[0])
                    if last_timestamp < cutoff_time_ms:
                        # Stream is old, delete it
                        await redis.delete(stream_key)
                        logger.info(f"Deleted old stream: {stream_key}")

            except Exception as e:
                logger.warning(f"Error cleaning stream {stream_key}: {e}")
