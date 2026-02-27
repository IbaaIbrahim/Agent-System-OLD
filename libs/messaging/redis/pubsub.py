"""Redis Pub/Sub for real-time event streaming."""

import asyncio
import json as json_module
from collections.abc import AsyncIterator, Callable, Coroutine
from typing import Any
from uuid import UUID

import redis.asyncio as redis

from libs.common.logging import get_logger
from libs.messaging.redis.client import get_redis_client

logger = get_logger(__name__)


class UUIDEncoder(json_module.JSONEncoder):
    """JSON encoder that handles UUID objects."""

    def default(self, obj: Any) -> Any:
        if isinstance(obj, UUID):
            return str(obj)
        return super().default(obj)


MessageHandler = Callable[[str, dict[str, Any]], Coroutine[Any, Any, None]]


class RedisPubSub:
    """Redis Pub/Sub wrapper for event streaming."""

    def __init__(self) -> None:
        self._pubsub: redis.client.PubSub | None = None
        self._handlers: dict[str, MessageHandler] = {}
        self._running = False

    async def connect(self) -> None:
        """Connect to Redis pub/sub."""
        client = await get_redis_client()
        self._pubsub = client.client.pubsub()
        logger.info("Redis pub/sub connected")

    async def disconnect(self) -> None:
        """Disconnect from Redis pub/sub."""
        if self._pubsub is not None:
            self._running = False
            await self._pubsub.close()
            self._pubsub = None
            logger.info("Redis pub/sub disconnected")

    async def subscribe(
        self,
        *channels: str,
        handler: MessageHandler | None = None,
    ) -> None:
        """Subscribe to one or more channels.

        Args:
            channels: Channel names to subscribe to
            handler: Optional message handler for all channels
        """
        if self._pubsub is None:
            raise RuntimeError("Pub/sub not connected. Call connect() first.")

        await self._pubsub.subscribe(*channels)

        if handler:
            for channel in channels:
                self._handlers[channel] = handler

        logger.info("Subscribed to channels", channels=channels)

    async def psubscribe(
        self,
        *patterns: str,
        handler: MessageHandler | None = None,
    ) -> None:
        """Subscribe to channels matching patterns.

        Args:
            patterns: Channel patterns (e.g., "job:*")
            handler: Optional message handler for all patterns
        """
        if self._pubsub is None:
            raise RuntimeError("Pub/sub not connected. Call connect() first.")

        await self._pubsub.psubscribe(*patterns)

        if handler:
            for pattern in patterns:
                self._handlers[pattern] = handler

        logger.info("Subscribed to patterns", patterns=patterns)

    async def unsubscribe(self, *channels: str) -> None:
        """Unsubscribe from channels."""
        if self._pubsub is None:
            return

        await self._pubsub.unsubscribe(*channels)

        for channel in channels:
            self._handlers.pop(channel, None)

        logger.info("Unsubscribed from channels", channels=channels)

    async def publish(self, channel: str, message: dict[str, Any]) -> int:
        """Publish a message to a channel.

        Args:
            channel: Target channel
            message: Message payload

        Returns:
            Number of subscribers that received the message
        """
        client = await get_redis_client()
        # Use module-level json import - reference it explicitly to avoid any shadowing issues
        json_message = json_module.dumps(message, cls=UUIDEncoder)
        count = await client.client.publish(channel, json_message)
        logger.debug(
            "Message published",
            channel=channel,
            subscribers=count,
        )
        return count

    async def listen(self) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        """Listen for messages on subscribed channels.

        Yields:
            Tuple of (channel, message_data)
        """
        if self._pubsub is None:
            raise RuntimeError("Pub/sub not connected. Call connect() first.")

        self._running = True

        while self._running:
            try:
                message = await self._pubsub.get_message(
                    ignore_subscribe_messages=True,
                    timeout=1.0,
                )

                if message is None:
                    continue

                if message["type"] not in ("message", "pmessage"):
                    continue

                channel = message.get("channel", message.get("pattern", ""))
                if isinstance(channel, bytes):
                    channel = channel.decode("utf-8")

                data = message["data"]
                if isinstance(data, bytes):
                    data = data.decode("utf-8")

                try:
                    parsed_data = json_module.loads(data)
                except json_module.JSONDecodeError:
                    parsed_data = {"raw": data}

                yield channel, parsed_data

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error in pub/sub listener", error=str(e))
                await asyncio.sleep(1)

    async def run(self) -> None:
        """Run the message processing loop with registered handlers."""
        async for channel, data in self.listen():
            # Find matching handler
            handler = self._handlers.get(channel)

            # Check pattern handlers
            if handler is None:
                for pattern, h in self._handlers.items():
                    if self._pattern_matches(pattern, channel):
                        handler = h
                        break

            if handler:
                try:
                    await handler(channel, data)
                except Exception as e:
                    logger.error(
                        "Error in message handler",
                        channel=channel,
                        error=str(e),
                    )

    def _pattern_matches(self, pattern: str, channel: str) -> bool:
        """Check if a channel matches a pattern."""
        if "*" not in pattern:
            return pattern == channel

        # Simple glob matching
        parts = pattern.split("*")
        if len(parts) == 2:
            return channel.startswith(parts[0]) and channel.endswith(parts[1])

        return False

    async def __aenter__(self) -> "RedisPubSub":
        await self.connect()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.disconnect()
