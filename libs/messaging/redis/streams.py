"""Redis Streams for durable event storage and replay."""

import json
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

from libs.common.logging import get_logger
from libs.messaging.redis.client import get_redis_client

logger = get_logger(__name__)


class UUIDEncoder(json.JSONEncoder):
    """JSON encoder that handles UUID objects."""

    def default(self, obj: Any) -> Any:
        if isinstance(obj, UUID):
            return str(obj)
        return super().default(obj)


class StreamEntry:
    """Represents a Redis stream entry."""

    def __init__(self, entry_id: str, data: dict[str, Any]) -> None:
        self.id = entry_id
        self.data = data

    @property
    def timestamp_ms(self) -> int:
        """Extract timestamp from entry ID."""
        return int(self.id.split("-")[0])


class RedisStreams:
    """Redis Streams wrapper for durable event storage."""

    def __init__(self, max_len: int = 10000) -> None:
        """Initialize Redis streams.

        Args:
            max_len: Maximum stream length (older entries trimmed)
        """
        self.max_len = max_len

    async def add(
        self,
        stream: str,
        data: dict[str, Any],
        entry_id: str = "*",
        max_len: int | None = None,
    ) -> str:
        """Add an entry to a stream.

        Args:
            stream: Stream name
            data: Entry data
            entry_id: Entry ID (default: auto-generate)
            max_len: Override default max length

        Returns:
            Generated entry ID
        """
        client = await get_redis_client()

        # Serialize data values
        serialized = {
            k: json.dumps(v, cls=UUIDEncoder) if not isinstance(v, str) else v
            for k, v in data.items()
        }

        entry_id = await client.client.xadd(
            stream,
            serialized,
            id=entry_id,
            maxlen=max_len or self.max_len,
            approximate=True,
        )

        logger.debug(
            "Entry added to stream",
            stream=stream,
            entry_id=entry_id,
        )
        return entry_id

    async def read(
        self,
        stream: str,
        start_id: str = "0",
        count: int = 100,
    ) -> list[StreamEntry]:
        """Read entries from a stream.

        Args:
            stream: Stream name
            start_id: Start reading from this ID (exclusive)
            count: Maximum entries to return

        Returns:
            List of StreamEntry objects
        """
        client = await get_redis_client()

        # XREAD returns list of [stream_name, [[id, {fields}], ...]]
        result = await client.client.xread(
            {stream: start_id},
            count=count,
            block=None,
        )

        entries = []
        if result:
            for _, messages in result:
                for msg_id, fields in messages:
                    # Deserialize JSON values
                    data = {}
                    for k, v in fields.items():
                        try:
                            data[k] = json.loads(v)
                        except (json.JSONDecodeError, TypeError):
                            data[k] = v
                    entries.append(StreamEntry(msg_id, data))

        return entries

    async def read_range(
        self,
        stream: str,
        start: str = "-",
        end: str = "+",
        count: int | None = None,
    ) -> list[StreamEntry]:
        """Read entries in a range.

        Args:
            stream: Stream name
            start: Start ID (- for beginning)
            end: End ID (+ for end)
            count: Maximum entries

        Returns:
            List of StreamEntry objects
        """
        client = await get_redis_client()

        result = await client.client.xrange(
            stream,
            min=start,
            max=end,
            count=count,
        )

        entries = []
        for msg_id, fields in result:
            data = {}
            for k, v in fields.items():
                try:
                    data[k] = json.loads(v)
                except (json.JSONDecodeError, TypeError):
                    data[k] = v
            entries.append(StreamEntry(msg_id, data))

        return entries

    async def read_after(
        self,
        stream: str,
        after_id: str,
        count: int = 100,
    ) -> list[StreamEntry]:
        """Read entries after a specific ID.

        Args:
            stream: Stream name
            after_id: Read entries after this ID
            count: Maximum entries

        Returns:
            List of StreamEntry objects
        """
        # Use exclusive range start
        start = f"({after_id}" if not after_id.startswith("(") else after_id
        return await self.read_range(stream, start=start, count=count)

    async def listen(
        self,
        stream: str,
        start_id: str = "$",
        block_ms: int = 5000,
    ) -> AsyncIterator[StreamEntry]:
        """Listen for new entries in a stream.

        Args:
            stream: Stream name
            start_id: Start position ($ for new entries only)
            block_ms: Block timeout in milliseconds

        Yields:
            StreamEntry objects as they arrive
        """
        client = await get_redis_client()
        last_id = start_id

        while True:
            try:
                result = await client.client.xread(
                    {stream: last_id},
                    count=100,
                    block=block_ms,
                )

                if result:
                    for _, messages in result:
                        for msg_id, fields in messages:
                            data = {}
                            for k, v in fields.items():
                                try:
                                    data[k] = json.loads(v)
                                except (json.JSONDecodeError, TypeError):
                                    data[k] = v

                            last_id = msg_id
                            yield StreamEntry(msg_id, data)

            except Exception as e:
                logger.error("Error reading stream", stream=stream, error=str(e))
                raise

    async def length(self, stream: str) -> int:
        """Get stream length."""
        client = await get_redis_client()
        return await client.client.xlen(stream)

    async def trim(self, stream: str, max_len: int) -> int:
        """Trim stream to max length.

        Returns:
            Number of entries removed
        """
        client = await get_redis_client()
        return await client.client.xtrim(stream, maxlen=max_len, approximate=True)

    async def delete(self, stream: str, *entry_ids: str) -> int:
        """Delete entries from a stream.

        Returns:
            Number of entries deleted
        """
        client = await get_redis_client()
        return await client.client.xdel(stream, *entry_ids)

    async def info(self, stream: str) -> dict[str, Any]:
        """Get stream info."""
        client = await get_redis_client()
        return await client.client.xinfo_stream(stream)

    # Consumer group operations for distributed processing
    async def create_group(
        self,
        stream: str,
        group: str,
        start_id: str = "0",
        mkstream: bool = True,
    ) -> bool:
        """Create a consumer group.

        Args:
            stream: Stream name
            group: Group name
            start_id: Start ID for group
            mkstream: Create stream if doesn't exist

        Returns:
            True if group created
        """
        client = await get_redis_client()
        try:
            await client.client.xgroup_create(
                stream,
                group,
                id=start_id,
                mkstream=mkstream,
            )
            logger.info(
                "Consumer group created",
                stream=stream,
                group=group,
            )
            return True
        except Exception as e:
            if "BUSYGROUP" in str(e):
                return False
            raise

    async def read_group(
        self,
        stream: str,
        group: str,
        consumer: str,
        count: int = 10,
        block_ms: int | None = 5000,
    ) -> list[StreamEntry]:
        """Read entries as a consumer group member.

        Args:
            stream: Stream name
            group: Group name
            consumer: Consumer name
            count: Max entries to read
            block_ms: Block timeout

        Returns:
            List of StreamEntry objects
        """
        client = await get_redis_client()

        result = await client.client.xreadgroup(
            group,
            consumer,
            {stream: ">"},
            count=count,
            block=block_ms,
        )

        entries = []
        if result:
            for _, messages in result:
                for msg_id, fields in messages:
                    data = {}
                    for k, v in fields.items():
                        try:
                            data[k] = json.loads(v)
                        except (json.JSONDecodeError, TypeError):
                            data[k] = v
                    entries.append(StreamEntry(msg_id, data))

        return entries

    async def ack(self, stream: str, group: str, *entry_ids: str) -> int:
        """Acknowledge entries as processed.

        Returns:
            Number of entries acknowledged
        """
        client = await get_redis_client()
        return await client.client.xack(stream, group, *entry_ids)
