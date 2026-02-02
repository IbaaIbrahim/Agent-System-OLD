"""Connection management for SSE streams."""

import asyncio
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from libs.common import get_logger

logger = get_logger(__name__)


@dataclass
class SSEConnection:
    """Represents an active SSE connection."""

    connection_id: str
    job_id: UUID
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_event_id: str | None = None
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=1000))
    is_active: bool = True


class ConnectionManager:
    """Manages SSE connections for streaming events."""

    def __init__(self) -> None:
        self._connections: dict[str, SSEConnection] = {}
        self._job_connections: dict[UUID, set[str]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def connect(
        self,
        connection_id: str,
        job_id: UUID,
        last_event_id: str | None = None,
    ) -> SSEConnection:
        """Register a new SSE connection.

        Args:
            connection_id: Unique connection identifier
            job_id: Job ID this connection is for
            last_event_id: Last event ID for resumption

        Returns:
            SSEConnection object
        """
        async with self._lock:
            connection = SSEConnection(
                connection_id=connection_id,
                job_id=job_id,
                last_event_id=last_event_id,
            )
            self._connections[connection_id] = connection
            self._job_connections[job_id].add(connection_id)

            logger.info(
                "SSE connection opened",
                connection_id=connection_id,
                job_id=str(job_id),
                last_event_id=last_event_id,
            )

            return connection

    async def disconnect(self, connection_id: str) -> None:
        """Remove an SSE connection.

        Args:
            connection_id: Connection to remove
        """
        async with self._lock:
            connection = self._connections.pop(connection_id, None)
            if connection:
                connection.is_active = False
                self._job_connections[connection.job_id].discard(connection_id)

                # Clean up empty job entry
                if not self._job_connections[connection.job_id]:
                    del self._job_connections[connection.job_id]

                logger.info(
                    "SSE connection closed",
                    connection_id=connection_id,
                    job_id=str(connection.job_id),
                )

    async def send_event(
        self,
        job_id: UUID,
        event_type: str,
        data: dict[str, Any],
        event_id: str | None = None,
    ) -> int:
        """Send an event to all connections for a job.

        Args:
            job_id: Target job ID
            event_type: SSE event type
            data: Event payload
            event_id: Optional event ID for resumption

        Returns:
            Number of connections that received the event
        """
        connection_ids = self._job_connections.get(job_id, set())
        sent_count = 0

        for conn_id in list(connection_ids):
            connection = self._connections.get(conn_id)
            if connection and connection.is_active:
                try:
                    event = {
                        "type": event_type,
                        "data": data,
                        "id": event_id,
                    }
                    await asyncio.wait_for(
                        connection.queue.put(event),
                        timeout=1.0,
                    )
                    sent_count += 1
                except TimeoutError:
                    logger.warning(
                        "Event queue full, dropping event",
                        connection_id=conn_id,
                        job_id=str(job_id),
                    )
                except Exception as e:
                    logger.error(
                        "Error sending event to connection",
                        connection_id=conn_id,
                        error=str(e),
                    )

        return sent_count

    async def broadcast_to_job(
        self,
        job_id: UUID,
        event_type: str,
        data: dict[str, Any],
    ) -> None:
        """Broadcast an event to all connections for a job."""
        await self.send_event(job_id, event_type, data)

    def get_connection(self, connection_id: str) -> SSEConnection | None:
        """Get a connection by ID."""
        return self._connections.get(connection_id)

    def get_job_connections(self, job_id: UUID) -> list[SSEConnection]:
        """Get all connections for a job."""
        connection_ids = self._job_connections.get(job_id, set())
        return [
            self._connections[cid]
            for cid in connection_ids
            if cid in self._connections
        ]

    def get_connection_count(self, job_id: UUID | None = None) -> int:
        """Get number of active connections."""
        if job_id:
            return len(self._job_connections.get(job_id, set()))
        return len(self._connections)

    async def close_all(self) -> None:
        """Close all connections."""
        async with self._lock:
            for connection in self._connections.values():
                connection.is_active = False
            self._connections.clear()
            self._job_connections.clear()
            logger.info("All SSE connections closed")
