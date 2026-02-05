"""Distributed lock service for job processing."""

from uuid import UUID

from libs.common import get_logger
from libs.messaging.redis import get_redis_client

logger = get_logger(__name__)


class DistributedStateLock:
    """Redis-based distributed lock for preventing concurrent job processing.

    Uses SETNX (SET if Not eXists) pattern with TTL for automatic cleanup.
    Multiple orchestrator instances competing for the same job will only
    allow one to proceed.
    """

    LOCK_PREFIX = "lock:job:"

    def __init__(self, ttl: int = 300) -> None:
        """Initialize lock service.

        Args:
            ttl: Lock time-to-live in seconds (default 5 minutes)
        """
        self.ttl = ttl
        self._acquired_locks: set[UUID] = set()

    async def acquire(self, job_id: UUID, owner: str | None = None) -> bool:
        """Acquire exclusive lock for job processing.

        Args:
            job_id: Job ID to lock
            owner: Optional owner identifier for debugging

        Returns:
            True if lock acquired successfully, False if already locked
        """
        redis = await get_redis_client()
        lock_key = f"{self.LOCK_PREFIX}{job_id}"
        lock_value = owner or "orchestrator"

        # SETNX: only set if key doesn't exist
        acquired = await redis.set(lock_key, lock_value, ex=self.ttl, nx=True)

        if acquired:
            self._acquired_locks.add(job_id)
            logger.info(
                "Lock acquired",
                job_id=str(job_id),
                ttl=self.ttl,
                owner=lock_value,
            )
        else:
            logger.debug(
                "Lock already held",
                job_id=str(job_id),
            )

        return bool(acquired)

    async def release(self, job_id: UUID) -> None:
        """Release lock for job.

        Args:
            job_id: Job ID to unlock
        """
        redis = await get_redis_client()
        lock_key = f"{self.LOCK_PREFIX}{job_id}"

        await redis.delete(lock_key)
        self._acquired_locks.discard(job_id)

        logger.debug("Lock released", job_id=str(job_id))

    async def extend(self, job_id: UUID, ttl: int | None = None) -> bool:
        """Extend lock TTL for long-running jobs.

        Args:
            job_id: Job ID
            ttl: New TTL in seconds (defaults to configured TTL)

        Returns:
            True if lock still exists and was extended
        """
        redis = await get_redis_client()
        lock_key = f"{self.LOCK_PREFIX}{job_id}"

        extended = await redis.expire(lock_key, ttl or self.ttl)

        if extended:
            logger.debug(
                "Lock extended",
                job_id=str(job_id),
                ttl=ttl or self.ttl,
            )

        return bool(extended)

    async def is_locked(self, job_id: UUID) -> bool:
        """Check if job is currently locked.

        Args:
            job_id: Job ID to check

        Returns:
            True if locked
        """
        redis = await get_redis_client()
        lock_key = f"{self.LOCK_PREFIX}{job_id}"
        return bool(await redis.exists(lock_key))

    async def cleanup(self) -> None:
        """Clean up all locks held by this instance.

        Should be called on orchestrator shutdown.
        """
        for job_id in list(self._acquired_locks):
            await self.release(job_id)

        logger.info("All locks cleaned up", count=len(self._acquired_locks))
