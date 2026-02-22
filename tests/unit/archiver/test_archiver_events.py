"""Unit tests for archiver event handling."""

import sys
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from services.archiver.src.services.postgres_writer import PostgresWriter
import services.archiver.src.services.postgres_writer as pw_module


class TestPostgresWriterInit:
    """Tests for PostgresWriter initialization."""

    def test_init_defaults(self):
        """Test default initialization."""
        writer = PostgresWriter()
        assert writer.batch_size == 100
        assert writer.flush_interval == 5
        assert writer._buffer == []
        assert writer._running is False

    def test_init_custom_values(self):
        """Test initialization with custom values."""
        writer = PostgresWriter(batch_size=50, flush_interval=10)
        assert writer.batch_size == 50
        assert writer.flush_interval == 10


class TestMessageEvents:
    """Tests for conversational event handling (message, delta, tool_result, tool_call)."""

    @pytest.mark.asyncio
    async def test_handle_message_event(self):
        """Test handling of message event."""
        writer = PostgresWriter()
        job_id = uuid4()

        event = {
            "job_id": job_id,
            "event_id": "evt_1",
            "event_type": "message",
            "data": {
                "role": "assistant",
                "content": "Hello, how can I help you?",
                "input_tokens": 10,
                "output_tokens": 20,
            },
            "timestamp_ms": int(datetime.now(UTC).timestamp() * 1000),
        }

        # Mock the database session
        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=MagicMock(scalar=lambda: 0))
        mock_session.add = MagicMock()
        mock_session.commit = AsyncMock()

        with patch.object(pw_module, "get_session_context") as mock_context:
            mock_context.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_context.return_value.__aexit__ = AsyncMock(return_value=None)

            await writer._write_events([event])

        # Verify session.add was called
        assert mock_session.add.called

    @pytest.mark.asyncio
    async def test_handle_delta_event(self):
        """Delta events are streaming-only and should NOT be persisted."""
        writer = PostgresWriter()
        job_id = uuid4()

        event = {
            "job_id": job_id,
            "event_id": "evt_2",
            "event_type": "delta",
            "data": {
                "role": "assistant",
                "content": "streaming chunk",
            },
            "timestamp_ms": int(datetime.now(UTC).timestamp() * 1000),
        }

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=MagicMock(scalar=lambda: 5))
        mock_session.add = MagicMock()
        mock_session.commit = AsyncMock()

        with patch.object(pw_module, "get_session_context") as mock_context:
            mock_context.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_context.return_value.__aexit__ = AsyncMock(return_value=None)

            await writer._write_events([event])

        # Delta events are skipped (real-time streaming only, not archived)
        assert not mock_session.add.called

    @pytest.mark.asyncio
    async def test_handle_tool_result_event(self):
        """Test handling of tool_result event."""
        writer = PostgresWriter()
        job_id = uuid4()

        event = {
            "job_id": job_id,
            "event_id": "evt_3",
            "event_type": "tool_result",
            "data": {
                "result": "Search results: Python is great",
                "tool_call_id": "tc_123",
                "tool_name": "web_search",
            },
            "timestamp_ms": int(datetime.now(UTC).timestamp() * 1000),
        }

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=MagicMock(scalar=lambda: 2))
        mock_session.add = MagicMock()
        mock_session.commit = AsyncMock()

        with patch.object(pw_module, "get_session_context") as mock_context:
            mock_context.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_context.return_value.__aexit__ = AsyncMock(return_value=None)

            await writer._write_events([event])

        assert mock_session.add.called

    @pytest.mark.asyncio
    async def test_handle_tool_call_event(self):
        """Test handling of tool_call event."""
        writer = PostgresWriter()
        job_id = uuid4()

        event = {
            "job_id": job_id,
            "event_id": "evt_4",
            "event_type": "tool_call",
            "data": {
                "tool_calls": [
                    {
                        "id": "tc_123",
                        "name": "web_search",
                        "arguments": {"query": "Python tutorials"},
                    }
                ],
            },
            "timestamp_ms": int(datetime.now(UTC).timestamp() * 1000),
        }

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=MagicMock(scalar=lambda: 1))
        mock_session.add = MagicMock()
        mock_session.commit = AsyncMock()

        with patch.object(pw_module, "get_session_context") as mock_context:
            mock_context.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_context.return_value.__aexit__ = AsyncMock(return_value=None)

            await writer._write_events([event])

        assert mock_session.add.called


class TestLifecycleEvents:
    """Tests for lifecycle event handling (start, complete, error, cancelled, suspended)."""

    @pytest.mark.asyncio
    async def test_handle_start_event(self):
        """Test handling of start event updates Job status."""
        writer = PostgresWriter()
        job_id = uuid4()

        event = {
            "job_id": job_id,
            "event_id": "evt_start",
            "event_type": "start",
            "data": {"status": "running"},
            "timestamp_ms": int(datetime.now(UTC).timestamp() * 1000),
        }

        # Mock job object
        mock_job = MagicMock()
        mock_job.status = "pending"
        mock_job.metadata_ = {}

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(
            side_effect=[
                MagicMock(scalar=lambda: 0),  # max sequence
                MagicMock(scalar_one_or_none=lambda: mock_job),  # job lookup
            ]
        )
        mock_session.commit = AsyncMock()

        with patch.object(pw_module, "get_session_context") as mock_context:
            mock_context.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_context.return_value.__aexit__ = AsyncMock(return_value=None)

            await writer._write_events([event])

        # Verify job status was updated
        from libs.db.models import JobStatus
        assert mock_job.status == JobStatus.RUNNING

    @pytest.mark.asyncio
    async def test_handle_complete_event(self):
        """Test handling of complete event updates Job with token counts."""
        writer = PostgresWriter()
        job_id = uuid4()

        event = {
            "job_id": job_id,
            "event_id": "evt_complete",
            "event_type": "complete",
            "data": {
                "total_input_tokens": 150,
                "total_output_tokens": 300,
            },
            "timestamp_ms": int(datetime.now(UTC).timestamp() * 1000),
        }

        mock_job = MagicMock()
        mock_job.status = "running"
        mock_job.completed_at = None
        mock_job.total_input_tokens = 0
        mock_job.total_output_tokens = 0
        mock_job.metadata_ = {}

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(
            side_effect=[
                MagicMock(scalar=lambda: 5),
                MagicMock(scalar_one_or_none=lambda: mock_job),
            ]
        )
        mock_session.commit = AsyncMock()

        with patch.object(pw_module, "get_session_context") as mock_context:
            mock_context.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_context.return_value.__aexit__ = AsyncMock(return_value=None)

            await writer._write_events([event])

        from libs.db.models import JobStatus
        assert mock_job.status == JobStatus.COMPLETED
        assert mock_job.completed_at is not None
        assert mock_job.total_input_tokens == 150
        assert mock_job.total_output_tokens == 300

    @pytest.mark.asyncio
    async def test_handle_error_event(self):
        """Test handling of error event updates Job status and stores error."""
        writer = PostgresWriter()
        job_id = uuid4()

        event = {
            "job_id": job_id,
            "event_id": "evt_error",
            "event_type": "error",
            "data": {
                "error": "Rate limit exceeded",
                "details": {"retry_after": 60},
            },
            "timestamp_ms": int(datetime.now(UTC).timestamp() * 1000),
        }

        mock_job = MagicMock()
        mock_job.status = "running"
        mock_job.completed_at = None
        mock_job.metadata_ = {}

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(
            side_effect=[
                MagicMock(scalar=lambda: 3),
                MagicMock(scalar_one_or_none=lambda: mock_job),
            ]
        )
        mock_session.commit = AsyncMock()

        with patch.object(pw_module, "get_session_context") as mock_context:
            mock_context.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_context.return_value.__aexit__ = AsyncMock(return_value=None)

            await writer._write_events([event])

        from libs.db.models import JobStatus
        assert mock_job.status == JobStatus.FAILED
        assert mock_job.completed_at is not None
        assert mock_job.metadata_["error"] == "Rate limit exceeded"
        assert mock_job.metadata_["error_details"]["retry_after"] == 60

    @pytest.mark.asyncio
    async def test_handle_cancelled_event(self):
        """Test handling of cancelled event updates Job status."""
        writer = PostgresWriter()
        job_id = uuid4()

        event = {
            "job_id": job_id,
            "event_id": "evt_cancelled",
            "event_type": "cancelled",
            "data": {},
            "timestamp_ms": int(datetime.now(UTC).timestamp() * 1000),
        }

        mock_job = MagicMock()
        mock_job.status = "running"
        mock_job.completed_at = None
        mock_job.metadata_ = {}

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(
            side_effect=[
                MagicMock(scalar=lambda: 2),
                MagicMock(scalar_one_or_none=lambda: mock_job),
            ]
        )
        mock_session.commit = AsyncMock()

        with patch.object(pw_module, "get_session_context") as mock_context:
            mock_context.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_context.return_value.__aexit__ = AsyncMock(return_value=None)

            await writer._write_events([event])

        from libs.db.models import JobStatus
        assert mock_job.status == JobStatus.CANCELLED
        assert mock_job.completed_at is not None

    @pytest.mark.asyncio
    async def test_handle_suspended_event(self):
        """Test handling of suspended event stores suspension info in metadata."""
        writer = PostgresWriter()
        job_id = uuid4()

        event = {
            "job_id": job_id,
            "event_id": "evt_suspended",
            "event_type": "suspended",
            "data": {
                "pending_tools": ["web_search", "calculator"],
                "snapshot_sequence": 3,
            },
            "timestamp_ms": int(datetime.now(UTC).timestamp() * 1000),
        }

        mock_job = MagicMock()
        mock_job.status = "running"
        mock_job.metadata_ = {}

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(
            side_effect=[
                MagicMock(scalar=lambda: 4),
                MagicMock(scalar_one_or_none=lambda: mock_job),
            ]
        )
        mock_session.commit = AsyncMock()

        with patch.object(pw_module, "get_session_context") as mock_context:
            mock_context.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_context.return_value.__aexit__ = AsyncMock(return_value=None)

            await writer._write_events([event])

        assert mock_job.metadata_["suspended"] is True
        assert mock_job.metadata_["pending_tools"] == ["web_search", "calculator"]
        assert mock_job.metadata_["snapshot_sequence"] == 3


class TestUnknownEvents:
    """Tests for unknown event handling."""

    @pytest.mark.asyncio
    async def test_handle_unknown_event_gracefully(self):
        """Test that unknown event types are logged but don't cause failures."""
        writer = PostgresWriter()
        job_id = uuid4()

        event = {
            "job_id": job_id,
            "event_id": "evt_unknown",
            "event_type": "some_future_event_type",
            "data": {"foo": "bar"},
            "timestamp_ms": int(datetime.now(UTC).timestamp() * 1000),
        }

        mock_job = MagicMock()
        mock_job.metadata_ = {}

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(
            side_effect=[
                MagicMock(scalar=lambda: 0),
                MagicMock(scalar_one_or_none=lambda: mock_job),
            ]
        )
        mock_session.commit = AsyncMock()

        with patch.object(pw_module, "get_session_context") as mock_context:
            mock_context.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_context.return_value.__aexit__ = AsyncMock(return_value=None)

            # Should not raise an exception
            await writer._write_events([event])

        # Commit should still be called
        mock_session.commit.assert_called_once()


class TestRoleMapping:
    """Tests for role mapping."""

    def test_map_role_system(self):
        """Test mapping system role."""
        from libs.db.models import MessageRole

        writer = PostgresWriter()
        assert writer._map_role("system") == MessageRole.SYSTEM

    def test_map_role_user(self):
        """Test mapping user role."""
        from libs.db.models import MessageRole

        writer = PostgresWriter()
        assert writer._map_role("user") == MessageRole.USER

    def test_map_role_assistant(self):
        """Test mapping assistant role."""
        from libs.db.models import MessageRole

        writer = PostgresWriter()
        assert writer._map_role("assistant") == MessageRole.ASSISTANT

    def test_map_role_tool(self):
        """Test mapping tool role."""
        from libs.db.models import MessageRole

        writer = PostgresWriter()
        assert writer._map_role("tool") == MessageRole.TOOL

    def test_map_role_unknown_defaults_to_assistant(self):
        """Test that unknown role defaults to assistant."""
        from libs.db.models import MessageRole

        writer = PostgresWriter()
        assert writer._map_role("unknown_role") == MessageRole.ASSISTANT


class TestBatching:
    """Tests for event batching behavior."""

    @pytest.mark.asyncio
    async def test_add_event_to_buffer(self):
        """Test adding event to buffer."""
        writer = PostgresWriter(batch_size=10)

        event = {
            "job_id": uuid4(),
            "event_id": "evt_1",
            "event_type": "message",
            "data": {"content": "test"},
            "timestamp_ms": 123456,
        }

        # Mock _flush_buffer to prevent actual flush
        writer._flush_buffer = AsyncMock()

        await writer.add_event(event)

        assert len(writer._buffer) == 1
        assert writer._buffer[0] == event

    @pytest.mark.asyncio
    async def test_auto_flush_when_batch_size_reached(self):
        """Test automatic flush when batch size is reached."""
        writer = PostgresWriter(batch_size=2)
        writer._flush_buffer = AsyncMock()

        event1 = {"job_id": uuid4(), "event_id": "1", "event_type": "message", "data": {}, "timestamp_ms": 1}
        event2 = {"job_id": uuid4(), "event_id": "2", "event_type": "message", "data": {}, "timestamp_ms": 2}

        await writer.add_event(event1)
        assert writer._flush_buffer.call_count == 0

        await writer.add_event(event2)
        assert writer._flush_buffer.call_count == 1


class TestJobNotFound:
    """Tests for handling events when job is not found."""

    @pytest.mark.asyncio
    async def test_lifecycle_event_with_missing_job(self):
        """Test that lifecycle events are skipped when job is not found."""
        writer = PostgresWriter()
        job_id = uuid4()

        event = {
            "job_id": job_id,
            "event_id": "evt_start",
            "event_type": "start",
            "data": {},
            "timestamp_ms": int(datetime.now(UTC).timestamp() * 1000),
        }

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(
            side_effect=[
                MagicMock(scalar=lambda: 0),
                MagicMock(scalar_one_or_none=lambda: None),  # Job not found
            ]
        )
        mock_session.commit = AsyncMock()

        with patch.object(pw_module, "get_session_context") as mock_context:
            mock_context.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_context.return_value.__aexit__ = AsyncMock(return_value=None)

            # Should not raise, just skip the update
            await writer._write_events([event])

        mock_session.commit.assert_called_once()


class TestNullMetadata:
    """Tests for handling None metadata."""

    @pytest.mark.asyncio
    async def test_error_event_with_null_metadata(self):
        """Test error event initializes metadata if None."""
        writer = PostgresWriter()
        job_id = uuid4()

        event = {
            "job_id": job_id,
            "event_id": "evt_error",
            "event_type": "error",
            "data": {"error": "Test error"},
            "timestamp_ms": int(datetime.now(UTC).timestamp() * 1000),
        }

        mock_job = MagicMock()
        mock_job.status = "running"
        mock_job.completed_at = None
        mock_job.metadata_ = None  # Explicitly None

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(
            side_effect=[
                MagicMock(scalar=lambda: 0),
                MagicMock(scalar_one_or_none=lambda: mock_job),
            ]
        )
        mock_session.commit = AsyncMock()

        with patch.object(pw_module, "get_session_context") as mock_context:
            mock_context.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_context.return_value.__aexit__ = AsyncMock(return_value=None)

            await writer._write_events([event])

        # metadata_ should be initialized
        assert mock_job.metadata_ is not None
        assert "error" in mock_job.metadata_

    @pytest.mark.asyncio
    async def test_suspended_event_with_null_metadata(self):
        """Test suspended event initializes metadata if None."""
        writer = PostgresWriter()
        job_id = uuid4()

        event = {
            "job_id": job_id,
            "event_id": "evt_suspended",
            "event_type": "suspended",
            "data": {"pending_tools": ["tool1"]},
            "timestamp_ms": int(datetime.now(UTC).timestamp() * 1000),
        }

        mock_job = MagicMock()
        mock_job.status = "running"
        mock_job.metadata_ = None

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(
            side_effect=[
                MagicMock(scalar=lambda: 0),
                MagicMock(scalar_one_or_none=lambda: mock_job),
            ]
        )
        mock_session.commit = AsyncMock()

        with patch.object(pw_module, "get_session_context") as mock_context:
            mock_context.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_context.return_value.__aexit__ = AsyncMock(return_value=None)

            await writer._write_events([event])

        assert mock_job.metadata_ is not None
        assert mock_job.metadata_["suspended"] is True
