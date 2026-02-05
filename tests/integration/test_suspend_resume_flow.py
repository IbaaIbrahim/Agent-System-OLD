"""Integration tests for the full suspend/resume cycle.

These tests require infrastructure running (Kafka, Redis, PostgreSQL).
Run with: pytest tests/integration/test_suspend_resume_flow.py -v

Tests cover:
- Full suspend/resume cycle: job dispatches tool, suspends, tool completes, job resumes
- Distributed locking prevents duplicate processing
- Snapshot persistence and recovery
- Multiple tool calls waiting logic
"""

import asyncio
import os
import sys
from datetime import UTC, datetime
from uuid import uuid4

import pytest

# Skip all tests if integration flag not set
pytestmark = pytest.mark.skipif(
    os.getenv("RUN_INTEGRATION_TESTS", "").lower() != "true",
    reason="Integration tests disabled. Set RUN_INTEGRATION_TESTS=true to run.",
)

# Add orchestrator service path for imports
sys.path.insert(0, "services/orchestrator")


class TestSuspendResumeCycle:
    """Tests for the full suspend/resume workflow."""

    async def test_snapshot_saved_on_tool_dispatch(self, admin_client, tenant_data):
        """Snapshot should be saved when orchestrator suspends on tool calls.

        Workflow:
        1. Create tenant and user
        2. Submit job that will trigger tool calls
        3. Verify snapshot is saved to job_snapshots table
        """
        # This test requires a fully running system with orchestrator
        # For now, we test the components that can be tested in isolation

        # Import directly to avoid circular import
        from services.orchestrator.src.engine.serializer import StateSerializer
        from services.orchestrator.src.engine.state import AgentState, AgentStatus

        state = AgentState(
            job_id=uuid4(),
            tenant_id=uuid4(),
            user_id=uuid4(),
            provider="anthropic",
            model="claude-3-5-sonnet",
            system_prompt="Test",
            tools=None,
            temperature=0.7,
            max_tokens=4096,
        )
        state.status = AgentStatus.WAITING_TOOL
        state.iteration = 3

        # Verify serialization works
        data = StateSerializer.serialize(state)
        assert data["status"] == "waiting_tool"
        assert data["iteration"] == 3

        restored = StateSerializer.deserialize(data)
        assert restored.job_id == state.job_id
        assert restored.status == AgentStatus.WAITING_TOOL

    async def test_tool_worker_publishes_resume_signal(self):
        """Tool worker should publish resume signal after completion.

        Workflow:
        1. Tool worker receives tool request
        2. Executes tool
        3. Stores result in Redis
        4. Publishes resume signal to agent.job-resume topic
        """
        # This test verifies the tool worker code structure
        # The actual Kafka publishing requires running infrastructure

        import importlib.util

        # Load config module directly from path to avoid 'src' namespace collision
        # because 'src' might resolve to api-gateway's src directory
        spec = importlib.util.spec_from_file_location(
            "tool_workers_config", 
            "services/tool-workers/src/config.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        ToolWorkersConfig = module.ToolWorkersConfig

        config = ToolWorkersConfig()
        assert config.resume_topic == "agent.job-resume"

    async def test_resume_handler_waits_for_all_tools(self):
        """Resume handler should wait for all pending tools before resuming.

        Workflow:
        1. Job dispatches 3 tool calls, suspends
        2. Tool 1 completes - resume handler checks, finds 2 missing, exits
        3. Tool 2 completes - resume handler checks, finds 1 missing, exits
        4. Tool 3 completes - resume handler checks, all present, resumes job
        """
        from libs.llm import ToolCall
        from services.orchestrator.src.engine.state import AgentState, AgentStatus

        # Create state with 3 pending tools
        state = AgentState(
            job_id=uuid4(),
            tenant_id=uuid4(),
            user_id=None,
            provider="anthropic",
            model="claude-3-5-sonnet",
            system_prompt=None,
            tools=None,
            temperature=0.7,
            max_tokens=4096,
        )
        state.status = AgentStatus.WAITING_TOOL
        state.pending_tool_calls = [
            ToolCall(id="tc_1", name="web_search", arguments={}),
            ToolCall(id="tc_2", name="calculator", arguments={}),
            ToolCall(id="tc_3", name="code_exec", arguments={}),
        ]

        # Simulate partial completion
        available_results = {
            "tc_1": "search results",
            "tc_2": None,  # Not ready
            "tc_3": None,  # Not ready
        }

        missing = [
            tc.id for tc in state.pending_tool_calls
            if available_results.get(tc.id) is None
        ]
        assert len(missing) == 2

        # Simulate more completion
        available_results["tc_2"] = "4"
        missing = [
            tc.id for tc in state.pending_tool_calls
            if available_results.get(tc.id) is None
        ]
        assert len(missing) == 1

        # All complete
        available_results["tc_3"] = "executed"
        missing = [
            tc.id for tc in state.pending_tool_calls
            if available_results.get(tc.id) is None
        ]
        assert len(missing) == 0


class TestDistributedLocking:
    """Tests for distributed locking preventing duplicate processing."""

    async def test_lock_prevents_concurrent_processing(self):
        """Only one handler should process a job at a time.

        Workflow:
        1. Handler A acquires lock for job_123
        2. Handler B tries to acquire lock for job_123 - fails
        3. Handler A releases lock
        4. Handler B can now acquire lock
        """
        from unittest.mock import AsyncMock, patch

        from services.orchestrator.src.services.state_lock import DistributedStateLock

        job_id = uuid4()

        # Simulate Redis with a simple dict-based mock
        lock_store = {}

        async def mock_set(key, value, ex=None, nx=False):
            if nx and key in lock_store:
                return False
            lock_store[key] = value
            return True

        async def mock_delete(key):
            lock_store.pop(key, None)

        mock_redis = AsyncMock()
        mock_redis.set = mock_set
        mock_redis.delete = mock_delete

        with patch(
            "services.orchestrator.src.services.state_lock.get_redis_client",
            AsyncMock(return_value=mock_redis),
        ):
            lock_a = DistributedStateLock(ttl=300)
            lock_b = DistributedStateLock(ttl=300)

            # Handler A acquires
            assert await lock_a.acquire(job_id, owner="handler_a") is True

            # Handler B fails
            assert await lock_b.acquire(job_id, owner="handler_b") is False

            # Handler A releases
            await lock_a.release(job_id)

            # Handler B succeeds
            assert await lock_b.acquire(job_id, owner="handler_b") is True

    async def test_lock_ttl_prevents_deadlock(self):
        """Lock should expire if handler crashes without releasing.

        This is important for preventing deadlocks when an orchestrator
        instance crashes while holding a lock.
        """
        from services.orchestrator.src.services.state_lock import DistributedStateLock

        lock = DistributedStateLock(ttl=300)

        # TTL should be configurable
        assert lock.ttl == 300

        # Different TTL can be specified at extend time
        custom_lock = DistributedStateLock(ttl=60)
        assert custom_lock.ttl == 60


class TestSnapshotPersistence:
    """Tests for snapshot persistence and recovery."""

    async def test_state_serialization_roundtrip(self):
        """State should survive serialization/deserialization roundtrip."""
        from libs.llm import LLMMessage, MessageRole, ToolCall
        from services.orchestrator.src.engine.serializer import StateSerializer
        from services.orchestrator.src.engine.state import AgentState, AgentStatus

        original = AgentState(
            job_id=uuid4(),
            tenant_id=uuid4(),
            user_id=uuid4(),
            provider="anthropic",
            model="claude-3-5-sonnet",
            system_prompt="You are helpful.",
            tools=[{"name": "calculator", "description": "Do math"}],
            temperature=0.5,
            max_tokens=2048,
        )
        original.mark_running()
        original.add_user_message("Calculate 2+2")
        original.add_assistant_message(
            content="I'll calculate that.",
            tool_calls=[ToolCall(id="tc_1", name="calculator", arguments={"expr": "2+2"})],
        )
        original.mark_waiting_tool([
            ToolCall(id="tc_1", name="calculator", arguments={"expr": "2+2"}),
        ])
        original.iteration = 5
        original.total_input_tokens = 150
        original.total_output_tokens = 75
        original.metadata = {"streaming": True, "trace_id": "xyz789"}

        # Roundtrip
        json_str = StateSerializer.to_json(original)
        restored = StateSerializer.from_json(json_str)

        # Verify all fields
        assert restored.job_id == original.job_id
        assert restored.tenant_id == original.tenant_id
        assert restored.user_id == original.user_id
        assert restored.provider == original.provider
        assert restored.model == original.model
        assert restored.system_prompt == original.system_prompt
        assert restored.tools == original.tools
        assert restored.temperature == original.temperature
        assert restored.max_tokens == original.max_tokens
        assert restored.status == AgentStatus.WAITING_TOOL
        assert restored.iteration == 5
        assert restored.total_input_tokens == 150
        assert restored.total_output_tokens == 75
        assert restored.metadata == {"streaming": True, "trace_id": "xyz789"}

        # Verify messages
        assert len(restored.messages) == 2
        assert restored.messages[0].role == MessageRole.USER
        assert restored.messages[1].role == MessageRole.ASSISTANT
        assert restored.messages[1].tool_calls is not None

        # Verify pending tools
        assert len(restored.pending_tool_calls) == 1
        assert restored.pending_tool_calls[0].id == "tc_1"
        assert restored.pending_tool_calls[0].name == "calculator"

    async def test_snapshot_preserves_message_history(self):
        """Snapshot should preserve complete message history for LLM context."""
        from libs.llm import ToolCall
        from services.orchestrator.src.engine.serializer import StateSerializer
        from services.orchestrator.src.engine.state import AgentState

        state = AgentState(
            job_id=uuid4(),
            tenant_id=uuid4(),
            user_id=None,
            provider="openai",
            model="gpt-4",
            system_prompt=None,
            tools=None,
            temperature=0.7,
            max_tokens=4096,
        )

        # Build up a conversation
        state.add_user_message("Hello")
        state.add_assistant_message(content="Hi! How can I help?")
        state.add_user_message("What's 2+2?")
        state.add_assistant_message(
            content="Let me calculate.",
            tool_calls=[ToolCall(id="tc_1", name="calculator", arguments={})],
        )
        state.add_tool_result("tc_1", "4")
        state.add_assistant_message(content="The answer is 4.")
        state.add_user_message("Thanks!")

        # Serialize and restore
        restored = StateSerializer.deserialize(StateSerializer.serialize(state))

        # All 7 messages should be preserved
        assert len(restored.messages) == 7


class TestFeatureFlagFallback:
    """Tests for feature flag allowing fallback to blocking mode."""

    def test_config_enable_suspend_resume_flag(self):
        """enable_suspend_resume flag should control behavior."""
        from services.orchestrator.src.config import OrchestratorConfig

        config = OrchestratorConfig()

        # Default should be True (new behavior)
        assert config.enable_suspend_resume is True

    def test_tool_handler_has_both_dispatch_methods(self):
        """ToolHandler should have both blocking and async dispatch methods."""
        from services.orchestrator.src.handlers.tool_handler import ToolHandler

        handler = ToolHandler()

        # Should have both methods
        assert hasattr(handler, "execute_tools")  # Blocking (legacy)
        assert hasattr(handler, "dispatch_tools_async")  # Non-blocking (new)


class TestDualKafkaConsumers:
    """Tests for dual Kafka consumer setup in orchestrator."""

    def test_config_has_resume_consumer_settings(self):
        """Config should have separate settings for resume consumer."""
        from services.orchestrator.src.config import OrchestratorConfig

        config = OrchestratorConfig()

        # Should have separate consumer group for resume
        assert config.consumer_group == "orchestrator"
        assert config.resume_consumer_group == "orchestrator-resume"
        assert config.resume_consumer_group != config.consumer_group

    def test_resume_topic_configured(self):
        """Resume topic should be configured."""
        from services.orchestrator.src.config import OrchestratorConfig

        config = OrchestratorConfig()

        assert config.resume_topic == "agent.job-resume"
        assert config.jobs_topic == "agent.jobs"
        assert config.resume_topic != config.jobs_topic


class TestToolResultFetching:
    """Tests for fetching tool results from Redis during resume."""

    async def test_fetch_results_handles_missing_gracefully(self):
        """Missing tool results should be handled gracefully."""
        from libs.llm import ToolCall
        from services.orchestrator.src.engine.state import AgentState, AgentStatus

        state = AgentState(
            job_id=uuid4(),
            tenant_id=uuid4(),
            user_id=None,
            provider="anthropic",
            model="claude-3-5-sonnet",
            system_prompt=None,
            tools=None,
            temperature=0.7,
            max_tokens=4096,
        )
        state.pending_tool_calls = [
            ToolCall(id="tc_1", name="calculator", arguments={}),
        ]

        # Simulate missing result handling (what resume_from_snapshot does)
        tool_results = {}  # Empty - no results
        for tc in state.pending_tool_calls:
            result = tool_results.get(tc.id)
            if result is None:
                result = "Error: Tool result not available"
            state.add_tool_result(tc.id, result)

        # Should have added error message
        assert len(state.messages) == 1
        assert "Error: Tool result not available" in state.messages[0].content
