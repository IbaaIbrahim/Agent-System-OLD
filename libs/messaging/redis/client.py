"""Redis client with connection pooling."""

import json
from typing import Any
from uuid import UUID

import redis.asyncio as redis
from redis.asyncio.connection import ConnectionPool

from libs.common.config import get_settings
from libs.common.logging import get_logger

logger = get_logger(__name__)

# Global client instance
_client: "RedisClient | None" = None


class UUIDEncoder(json.JSONEncoder):
    """JSON encoder that handles UUID objects."""

    def default(self, obj: Any) -> Any:
        if isinstance(obj, UUID):
            return str(obj)
        return super().default(obj)


class RedisClient:
    """Async Redis client wrapper with connection pooling."""

    def __init__(self, url: str, pool_size: int = 10) -> None:
        self.url = url
        self.pool_size = pool_size
        self._pool: ConnectionPool | None = None
        self._client: redis.Redis | None = None

    async def connect(self) -> None:
        """Establish Redis connection."""
        if self._client is not None:
            return

        self._pool = ConnectionPool.from_url(
            self.url,
            max_connections=self.pool_size,
            decode_responses=True,
        )
        self._client = redis.Redis(connection_pool=self._pool)

        # Test connection
        await self._client.ping()
        logger.info("Redis connected", url=self.url)

    async def disconnect(self) -> None:
        """Close Redis connection."""
        if self._client is not None:
            await self._client.close()
            self._client = None
        if self._pool is not None:
            await self._pool.disconnect()
            self._pool = None
        logger.info("Redis disconnected")

    @property
    def client(self) -> redis.Redis:
        """Get the underlying Redis client."""
        if self._client is None:
            raise RuntimeError("Redis not connected. Call connect() first.")
        return self._client

    # Key-value operations
    async def get(self, key: str) -> str | None:
        """Get a value by key."""
        return await self.client.get(key)

    async def set(
        self,
        key: str,
        value: str,
        ex: int | None = None,
        px: int | None = None,
        nx: bool = False,
        xx: bool = False,
    ) -> bool:
        """Set a value with optional expiration.

        Args:
            key: Key name
            value: Value to set
            ex: Expiration in seconds
            px: Expiration in milliseconds
            nx: Only set if key doesn't exist
            xx: Only set if key exists

        Returns:
            True if set successfully
        """
        result = await self.client.set(
            key, value, ex=ex, px=px, nx=nx, xx=xx
        )
        return result is not None

    async def delete(self, *keys: str) -> int:
        """Delete one or more keys."""
        return await self.client.delete(*keys)

    async def exists(self, *keys: str) -> int:
        """Check if keys exist."""
        return await self.client.exists(*keys)

    async def expire(self, key: str, seconds: int) -> bool:
        """Set key expiration in seconds."""
        return await self.client.expire(key, seconds)

    async def ttl(self, key: str) -> int:
        """Get key TTL in seconds."""
        return await self.client.ttl(key)

    # JSON operations
    async def get_json(self, key: str) -> Any | None:
        """Get and deserialize JSON value."""
        value = await self.get(key)
        if value is None:
            return None
        return json.loads(value)

    async def set_json(
        self,
        key: str,
        value: Any,
        ex: int | None = None,
    ) -> bool:
        """Serialize and set JSON value."""
        json_str = json.dumps(value, cls=UUIDEncoder)
        return await self.set(key, json_str, ex=ex)

    # Hash operations
    async def hget(self, name: str, key: str) -> str | None:
        """Get a hash field value."""
        return await self.client.hget(name, key)

    async def hset(
        self,
        name: str,
        key: str | None = None,
        value: str | None = None,
        mapping: dict[str, str] | None = None,
    ) -> int:
        """Set hash field(s)."""
        return await self.client.hset(name, key, value, mapping=mapping)

    async def hgetall(self, name: str) -> dict[str, str]:
        """Get all hash fields and values."""
        return await self.client.hgetall(name)

    async def hdel(self, name: str, *keys: str) -> int:
        """Delete hash fields."""
        return await self.client.hdel(name, *keys)

    # List operations
    async def lpush(self, name: str, *values: str) -> int:
        """Push values to the left of a list."""
        return await self.client.lpush(name, *values)

    async def rpush(self, name: str, *values: str) -> int:
        """Push values to the right of a list."""
        return await self.client.rpush(name, *values)

    async def lrange(self, name: str, start: int, end: int) -> list[str]:
        """Get a range of list elements."""
        return await self.client.lrange(name, start, end)

    async def llen(self, name: str) -> int:
        """Get list length."""
        return await self.client.llen(name)

    # Atomic operations
    async def incr(self, key: str, amount: int = 1) -> int:
        """Increment a key by amount."""
        return await self.client.incrby(key, amount)

    async def decr(self, key: str, amount: int = 1) -> int:
        """Decrement a key by amount."""
        return await self.client.decrby(key, amount)

    # Pipeline for batch operations
    def pipeline(self) -> redis.client.Pipeline:
        """Create a pipeline for batch operations."""
        return self.client.pipeline()

    async def __aenter__(self) -> "RedisClient":
        await self.connect()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.disconnect()


async def get_redis_client() -> RedisClient:
    """Get or create the global Redis client."""
    global _client

    if _client is None:
        settings = get_settings()
        _client = RedisClient(
            url=settings.redis_url,
            pool_size=settings.redis_pool_size,
        )
        await _client.connect()

    return _client


async def close_redis_client() -> None:
    """Close the global Redis client."""
    global _client

    if _client is not None:
        await _client.disconnect()
        _client = None
