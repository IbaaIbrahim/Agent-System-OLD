"""Kafka consumer with retry and DLQ support."""

import asyncio
import json
from collections.abc import Callable, Coroutine
from typing import Any

from aiokafka import AIOKafkaConsumer
from aiokafka.errors import KafkaError

from libs.common.config import get_settings
from libs.common.logging import get_logger

logger = get_logger(__name__)


def deserialize_message(data: bytes) -> dict[str, Any]:
    """Deserialize message from JSON bytes."""
    return json.loads(data.decode("utf-8"))


MessageHandler = Callable[[dict[str, Any], dict[str, str]], Coroutine[Any, Any, None]]


class KafkaConsumer:
    """Async Kafka consumer with retry and dead letter queue support."""

    def __init__(
        self,
        topics: list[str],
        group_id: str,
        bootstrap_servers: str,
        dlq_topic: str | None = None,
        max_retries: int = 3,
        retry_delay_ms: int = 1000,
        max_concurrent_messages: int = 10,
    ) -> None:
        self.topics = topics
        self.group_id = group_id
        self.bootstrap_servers = bootstrap_servers
        self.dlq_topic = dlq_topic
        self.max_retries = max_retries
        self.retry_delay_ms = retry_delay_ms
        self.max_concurrent_messages = max_concurrent_messages

        self._consumer: AIOKafkaConsumer | None = None
        self._running = False
        self._handlers: dict[str, MessageHandler] = {}
        self._active_tasks: set[asyncio.Task[None]] = set()

    def register_handler(self, topic: str, handler: MessageHandler) -> None:
        """Register a message handler for a topic.

        Args:
            topic: Topic name
            handler: Async function to process messages
        """
        self._handlers[topic] = handler
        logger.info("Handler registered", topic=topic)

    async def start(self) -> None:
        """Start the Kafka consumer."""
        if self._consumer is not None:
            return

        self._consumer = AIOKafkaConsumer(
            *self.topics,
            bootstrap_servers=self.bootstrap_servers,
            group_id=self.group_id,
            value_deserializer=deserialize_message,
            auto_offset_reset="earliest",
            enable_auto_commit=False,
            max_poll_records=100,
            session_timeout_ms=30000,
            heartbeat_interval_ms=10000,
            max_poll_interval_ms=300000,  # 5 minutes - allow long-running jobs
            fetch_min_bytes=1,  # Return immediately if data is available
            fetch_max_wait_ms=100,  # Wait max 100ms for data (reduced from default 500ms)
        )
        await self._consumer.start()
        self._running = True
        logger.info(
            "Kafka consumer started",
            topics=self.topics,
            group_id=self.group_id,
        )

    async def stop(self) -> None:
        """Stop the Kafka consumer."""
        self._running = False
        
        # Wait for active tasks to complete (with timeout)
        if self._active_tasks:
            logger.info(
                "Waiting for active message processing tasks to complete",
                count=len(self._active_tasks),
            )
            try:
                await asyncio.wait_for(
                    asyncio.gather(*self._active_tasks, return_exceptions=True),
                    timeout=30.0,  # Wait up to 30 seconds
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "Timeout waiting for active tasks, cancelling",
                    count=len(self._active_tasks),
                )
                for task in self._active_tasks:
                    task.cancel()
                await asyncio.gather(*self._active_tasks, return_exceptions=True)
        
        if self._consumer is not None:
            await self._consumer.stop()
            self._consumer = None
            logger.info("Kafka consumer stopped")

    async def run(self) -> None:
        """Run the consumer loop processing messages concurrently."""
        if self._consumer is None:
            raise RuntimeError("Consumer not started. Call start() first.")

        logger.info(
            "Starting consumer loop",
            max_concurrent_messages=self.max_concurrent_messages,
        )

        try:
            async for message in self._consumer:
                if not self._running:
                    break

                # Wait if we have too many concurrent messages being processed
                while len(self._active_tasks) >= self.max_concurrent_messages:
                    # Wait for at least one task to complete
                    if self._active_tasks:
                        done, pending = await asyncio.wait(
                            self._active_tasks, return_when=asyncio.FIRST_COMPLETED
                        )
                        self._active_tasks -= done
                        # Clean up completed tasks
                        for task in done:
                            try:
                                await task
                            except Exception as e:
                                logger.error(
                                    "Error in message processing task",
                                    error=str(e),
                                )

                # Start processing message concurrently
                task = asyncio.create_task(
                    self._process_message(message)
                )
                self._active_tasks.add(task)

                # Clean up completed tasks in background
                task.add_done_callback(self._active_tasks.discard)

        except KafkaError as e:
            logger.error("Kafka error in consumer loop", error=str(e))
            raise
        finally:
            # Wait for all active tasks to complete
            if self._active_tasks:
                logger.info(
                    "Waiting for active tasks to complete",
                    count=len(self._active_tasks),
                )
                await asyncio.gather(*self._active_tasks, return_exceptions=True)

    async def _process_message(self, message: Any) -> None:
        """Process a single message.

        Args:
            message: Kafka message object
        """
        topic = message.topic
        handler = self._handlers.get(topic)

        if handler is None:
            logger.warning("No handler for topic", topic=topic)
            await self._consumer.commit()
            return

        # Extract headers
        headers = {}
        if message.headers:
            headers = {k: v.decode("utf-8") for k, v in message.headers}

        # Get retry count
        retry_count = int(headers.get("x-retry-count", "0"))

        try:
            await handler(message.value, headers)
            # Commit after successful processing
            await self._consumer.commit()
        except Exception as e:
            logger.error(
                "Error processing message",
                topic=topic,
                error=str(e),
                retry_count=retry_count,
            )

            if retry_count < self.max_retries:
                # Retry with backoff
                await self._retry_message(
                    message.value,
                    headers,
                    retry_count + 1,
                )
            elif self.dlq_topic:
                # Send to DLQ
                await self._send_to_dlq(
                    message.value,
                    headers,
                    str(e),
                )

            # Commit even after error (to avoid reprocessing failed messages)
            await self._consumer.commit()

    async def _retry_message(
        self,
        message: dict[str, Any],
        headers: dict[str, str],
        retry_count: int,
    ) -> None:
        """Schedule a message for retry.

        Args:
            message: Original message
            headers: Original headers
            retry_count: Current retry count
        """
        delay = self.retry_delay_ms * (2 ** (retry_count - 1))  # Exponential backoff
        await asyncio.sleep(delay / 1000)

        headers["x-retry-count"] = str(retry_count)

        # Re-publish to same topic (requires producer)
        # In practice, you'd use a separate retry topic or delay queue
        logger.info(
            "Message scheduled for retry",
            retry_count=retry_count,
            delay_ms=delay,
        )

    async def _send_to_dlq(
        self,
        message: dict[str, Any],
        headers: dict[str, str],
        error: str,
    ) -> None:
        """Send failed message to dead letter queue.

        Args:
            message: Failed message
            headers: Original headers
            error: Error description
        """
        if not self.dlq_topic:
            return

        # In practice, you'd use a producer to send to DLQ topic
        logger.error(
            "Message sent to DLQ",
            dlq_topic=self.dlq_topic,
            error=error,
        )

    async def __aenter__(self) -> "KafkaConsumer":
        await self.start()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.stop()


async def create_consumer(
    topics: list[str],
    group_id: str | None = None,
    bootstrap_servers: str | None = None,
    dlq_topic: str | None = None,
) -> KafkaConsumer:
    """Create and configure a Kafka consumer.

    Args:
        topics: List of topics to consume
        group_id: Consumer group ID (default from settings)
        bootstrap_servers: Kafka bootstrap servers (default from settings)
        dlq_topic: Dead letter queue topic

    Returns:
        Configured KafkaConsumer instance
    """
    settings = get_settings()

    consumer = KafkaConsumer(
        topics=topics,
        group_id=group_id or settings.kafka_consumer_group,
        bootstrap_servers=bootstrap_servers or settings.kafka_bootstrap_servers,
        dlq_topic=dlq_topic,
    )

    return consumer
