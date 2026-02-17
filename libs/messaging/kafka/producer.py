"""Kafka producer with retry and serialization."""

import json
from typing import Any
from uuid import UUID

from aiokafka import AIOKafkaProducer

from libs.common.config import get_settings
from libs.common.logging import get_logger

logger = get_logger(__name__)

# Global producer instance
_producer: "KafkaProducer | None" = None


class UUIDEncoder(json.JSONEncoder):
    """JSON encoder that handles UUID objects."""

    def default(self, obj: Any) -> Any:
        if isinstance(obj, UUID):
            return str(obj)
        return super().default(obj)


def serialize_message(message: dict[str, Any]) -> bytes:
    """Serialize message to JSON bytes."""
    return json.dumps(message, cls=UUIDEncoder).encode("utf-8")


def serialize_key(key: str | None) -> bytes | None:
    """Serialize message key to bytes."""
    if key is None:
        return None
    return key.encode("utf-8")


class KafkaProducer:
    """Async Kafka producer with retry and error handling."""

    def __init__(
        self,
        bootstrap_servers: str,
        client_id: str = "agent-producer",
    ) -> None:
        self.bootstrap_servers = bootstrap_servers
        self.client_id = client_id
        self._producer: AIOKafkaProducer | None = None

    async def start(self) -> None:
        """Start the Kafka producer."""
        if self._producer is not None:
            return

        self._producer = AIOKafkaProducer(
            bootstrap_servers=self.bootstrap_servers,
            client_id=self.client_id,
            value_serializer=serialize_message,
            key_serializer=serialize_key,
            acks=1,  # Changed from "all" to 1 for lower latency (leader acknowledgment is sufficient)
            request_timeout_ms=10000,  # Reduced from 30s to 10s for faster failure detection
            max_request_size=10 * 1024 * 1024,  # 10MB
            linger_ms=0,  # Send immediately, no batching delay
            max_batch_size=16384,  # Small batch size for low latency (default is 16384)
        )
        await self._producer.start()
        logger.info(
            "Kafka producer started",
            bootstrap_servers=self.bootstrap_servers,
        )

    async def stop(self) -> None:
        """Stop the Kafka producer."""
        if self._producer is not None:
            await self._producer.stop()
            self._producer = None
            logger.info("Kafka producer stopped")

    async def send(
        self,
        topic: str,
        message: dict[str, Any],
        key: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        """Send a message to a Kafka topic.

        Args:
            topic: Target topic name
            message: Message payload as dict
            key: Optional partition key
            headers: Optional message headers
        """
        if self._producer is None:
            raise RuntimeError("Producer not started. Call start() first.")

        kafka_headers = None
        if headers:
            kafka_headers = [(k, v.encode("utf-8")) for k, v in headers.items()]

        try:
            # Info-level log about outgoing Kafka message
            logger.info(
                "Sending message to Kafka",
                topic=topic,
                key=key,
                payload_size=len(json.dumps(message)) if message else 0,
                headers_count=len(kafka_headers) if kafka_headers else 0,
            )
            await self._producer.send_and_wait(
                topic=topic,
                value=message,
                key=key,
                headers=kafka_headers,
            )
            logger.debug(
                "Message sent to Kafka",
                topic=topic,
                key=key,
            )
        except Exception as e:
            logger.error(
                "Failed to send message to Kafka",
                topic=topic,
                key=key,
                error=str(e),
            )
            raise

    async def send_batch(
        self,
        topic: str,
        messages: list[dict[str, Any]],
        key_field: str | None = None,
    ) -> None:
        """Send multiple messages to a Kafka topic.

        Args:
            topic: Target topic name
            messages: List of message payloads
            key_field: Optional field name to use as partition key
        """
        if self._producer is None:
            raise RuntimeError("Producer not started. Call start() first.")

        batch = self._producer.create_batch()
        for message in messages:
            key = message.get(key_field) if key_field else None
            batch.append(
                value=serialize_message(message),
                key=serialize_key(str(key)) if key else None,
                headers=None,
            )

        await self._producer.send_batch(batch, topic)
        logger.debug(
            "Batch sent to Kafka",
            topic=topic,
            count=len(messages),
        )

    async def __aenter__(self) -> "KafkaProducer":
        await self.start()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.stop()


async def create_producer(
    bootstrap_servers: str | None = None,
    client_id: str = "agent-producer",
) -> KafkaProducer:
    """Create and start a Kafka producer.

    Args:
        bootstrap_servers: Kafka bootstrap servers (default from settings)
        client_id: Producer client ID

    Returns:
        Started KafkaProducer instance
    """
    global _producer

    if _producer is not None:
        return _producer

    settings = get_settings()
    _producer = KafkaProducer(
        bootstrap_servers=bootstrap_servers or settings.kafka_bootstrap_servers,
        client_id=client_id,
    )
    await _producer.start()
    return _producer


async def get_producer() -> KafkaProducer:
    """Get the global Kafka producer instance."""
    global _producer
    if _producer is None:
        return await create_producer()
    return _producer
