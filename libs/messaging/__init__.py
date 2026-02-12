"""Messaging utilities for Kafka and Redis.

Submodules are imported lazily to avoid forcing all services to install
every dependency (e.g. websocket-gateway needs Redis but not Kafka).
Use explicit imports: ``from libs.messaging.redis import ...``
or ``from libs.messaging.kafka import ...``.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from libs.messaging.kafka import (
        KafkaConsumer as KafkaConsumer,
        KafkaProducer as KafkaProducer,
        create_consumer as create_consumer,
        create_producer as create_producer,
    )
    from libs.messaging.redis import (
        RedisClient as RedisClient,
        RedisPubSub as RedisPubSub,
        RedisStreams as RedisStreams,
        get_redis_client as get_redis_client,
    )

__all__ = [
    # Kafka
    "KafkaProducer",
    "KafkaConsumer",
    "create_producer",
    "create_consumer",
    # Redis
    "RedisClient",
    "RedisPubSub",
    "RedisStreams",
    "get_redis_client",
]


def __getattr__(name: str):
    """Lazy import submodule attributes."""
    if name in ("KafkaProducer", "KafkaConsumer", "create_producer", "create_consumer"):
        mod = importlib.import_module("libs.messaging.kafka")
        return getattr(mod, name)
    if name in ("RedisClient", "RedisPubSub", "RedisStreams", "get_redis_client"):
        mod = importlib.import_module("libs.messaging.redis")
        return getattr(mod, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
