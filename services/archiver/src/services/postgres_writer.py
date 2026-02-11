"""PostgreSQL batch writer for archiving events."""

import asyncio
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select, text

from libs.common import get_logger
from libs.db import get_session_context
from libs.db.models import ChatMessage, Job, JobStatus, MessageRole

logger = get_logger(__name__)


class PostgresWriter:
    """Batched writer for persisting events to PostgreSQL."""

    def __init__(
        self,
        batch_size: int = 100,
        flush_interval: int = 5,
    ) -> None:
        self.batch_size = batch_size
        self.flush_interval = flush_interval
        self._buffer: list[dict[str, Any]] = []
        self._lock = asyncio.Lock()
        self._flush_task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        """Start the periodic flush task."""
        self._running = True
        self._flush_task = asyncio.create_task(self._periodic_flush())

    async def stop(self) -> None:
        """Stop the writer and flush remaining data."""
        self._running = False
        if self._flush_task:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
        await self.flush()

    async def add_event(self, event: dict[str, Any]) -> None:
        """Add an event to the buffer.

        Args:
            event: Event data to archive
        """
        async with self._lock:
            self._buffer.append(event)

            if len(self._buffer) >= self.batch_size:
                await self._flush_buffer()

    async def flush(self) -> None:
        """Flush all buffered events to database."""
        async with self._lock:
            await self._flush_buffer()

    async def _flush_buffer(self) -> None:
        """Flush the buffer to PostgreSQL."""
        if not self._buffer:
            return

        events = self._buffer.copy()
        self._buffer.clear()

        logger.debug(f"Flushing {len(events)} events to PostgreSQL")

        try:
            await self._write_events(events)
        except Exception as e:
            logger.error(f"Failed to write events: {e}")
            # Put events back in buffer for retry
            self._buffer.extend(events)

    async def _write_events(self, events: list[dict[str, Any]]) -> None:
        """Write events to PostgreSQL.

        Handles two types of events:
        1. Conversational events (message, delta, tool_result, tool_call) → ChatMessage table
        2. Lifecycle events (start, complete, error, cancelled, suspended) → Job table updates

        Args:
            events: List of events to write
        """
        # Group events by job
        job_events: dict[UUID, list[dict[str, Any]]] = {}
        for event in events:
            job_id = event["job_id"]
            if job_id not in job_events:
                job_events[job_id] = []
            job_events[job_id].append(event)

        async with get_session_context() as session:
            for job_id, job_event_list in job_events.items():
                # Get current max sequence number for job
                result = await session.execute(
                    text("""
                        SELECT COALESCE(MAX(sequence_num), 0)
                        FROM jobs.chat_messages
                        WHERE job_id = :job_id
                    """),
                    {"job_id": job_id},
                )
                max_seq = result.scalar() or 0

                # Load job record for lifecycle event updates
                job_result = await session.execute(
                    select(Job).where(Job.id == job_id)
                )
                job = job_result.scalar_one_or_none()

                # Process events
                for event in job_event_list:
                    event_type = event["event_type"]
                    data = event["data"]

                    # CONVERSATIONAL EVENTS → ChatMessage table
                    # Note: "delta" events are for real-time streaming only, not persisted
                    if event_type == "message":
                        max_seq += 1
                        message = ChatMessage(
                            job_id=job_id,
                            sequence_num=max_seq,
                            role=self._map_role(data.get("role", "assistant")),
                            content=data.get("content"),
                            tool_calls=data.get("tool_calls"),
                            tool_call_id=data.get("tool_call_id"),
                            input_tokens=data.get("input_tokens"),
                            output_tokens=data.get("output_tokens"),
                            metadata_={
                                "event_id": event["event_id"],
                                "timestamp_ms": event["timestamp_ms"],
                            },
                        )
                        session.add(message)

                    elif event_type == "tool_result":
                        max_seq += 1
                        message = ChatMessage(
                            job_id=job_id,
                            sequence_num=max_seq,
                            role=MessageRole.TOOL,
                            content=data.get("result"),
                            tool_call_id=data.get("tool_call_id"),
                            metadata_={
                                "tool_name": data.get("tool_name"),
                                "event_id": event["event_id"],
                            },
                        )
                        session.add(message)

                    elif event_type == "tool_call":
                        # Tool calls are part of conversation history
                        max_seq += 1
                        message = ChatMessage(
                            job_id=job_id,
                            sequence_num=max_seq,
                            role=MessageRole.ASSISTANT,
                            content=None,  # Tool calls have no content
                            tool_calls=data.get("tool_calls"),  # LLM's tool requests
                            metadata_={
                                "event_id": event["event_id"],
                                "event_type": "tool_call",
                            },
                        )
                        session.add(message)

                    # LIFECYCLE EVENTS → Job table updates
                    elif event_type == "start":
                        if job:
                            job.status = JobStatus.RUNNING
                            # started_at is already set by orchestrator

                    elif event_type == "complete":
                        if job:
                            job.status = JobStatus.COMPLETED
                            job.completed_at = datetime.now(UTC)
                            job.total_input_tokens = data.get("total_input_tokens", 0)
                            job.total_output_tokens = data.get("total_output_tokens", 0)

                    elif event_type == "error":
                        if job:
                            job.status = JobStatus.FAILED
                            job.completed_at = datetime.now(UTC)
                            # Store error in metadata (Job doesn't have error field)
                            if job.metadata_ is None:
                                job.metadata_ = {}
                            job.metadata_["error"] = data.get("error", "Unknown error")
                            job.metadata_["error_details"] = data.get("details", {})

                    elif event_type == "cancelled":
                        if job:
                            job.status = JobStatus.CANCELLED
                            job.completed_at = datetime.now(UTC)

                    elif event_type == "suspended":
                        if job:
                            # Store suspension info in metadata
                            # (Job.status doesn't have WAITING_TOOL enum value)
                            if job.metadata_ is None:
                                job.metadata_ = {}
                            job.metadata_["suspended"] = True
                            job.metadata_["pending_tools"] = data.get("pending_tools", [])
                            job.metadata_["snapshot_sequence"] = data.get("snapshot_sequence")

                    else:
                        # Unknown event type - log warning but don't fail
                        logger.warning(
                            "Unknown event type",
                            event_type=event_type,
                            job_id=str(job_id),
                        )

            await session.commit()

        logger.info(
            "Events archived",
            event_count=len(events),
            job_count=len(job_events),
        )

    def _map_role(self, role: str) -> MessageRole:
        """Map role string to MessageRole enum."""
        role_map = {
            "system": MessageRole.SYSTEM,
            "user": MessageRole.USER,
            "assistant": MessageRole.ASSISTANT,
            "tool": MessageRole.TOOL,
        }
        return role_map.get(role, MessageRole.ASSISTANT)

    async def _periodic_flush(self) -> None:
        """Periodically flush the buffer."""
        while self._running:
            await asyncio.sleep(self.flush_interval)
            if self._running:
                await self.flush()
