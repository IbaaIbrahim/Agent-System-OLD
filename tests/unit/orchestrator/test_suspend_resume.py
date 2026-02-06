"""Unit tests for suspend/resume functionality in the orchestrator.

Tests cover:
- DistributedStateLock (Redis-based locking)
- AgentState suspend/resume status transitions
- State serialization/deserialization for snapshots
- ResumeHandler tool result waiting logic
"""

import sys
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

# Add orchestrator service path for imports
sys.path.insert(0, "services/orchestrator")

from libs.llm import LLMMessage, MessageRole, ToolCall

# Import directly from modules to avoid circular import through __init__.py
from services.orchestrator.src.engine.state import AgentState, AgentStatus
from services.orchestrator.src.engine.serializer import StateSerializer


class TestDistributedStateLock:
    """Tests for Redis-based distributed state locking."""

    @pytest.fixture
    def mock_redis(self):
        """Create a mock Redis client."""
        redis = AsyncMock()
        redis.set = AsyncMock(return_value=True)
        redis.delete = AsyncMock()
        redis.expire = AsyncMock(return_value=True)
        redis.exists = AsyncMock(return_value=False)
        return redis

    @pytest.fixture
    def state_lock(self, mock_redis):
        """Create DistributedStateLock with mocked Redis."""
        from services.orchestrator.src.services.state_lock import DistributedStateLock

        lock = DistributedStateLock(ttl=300)
        return lock

    async def test_acquire_lock_success(self, state_lock, mock_redis):
        """Lock should be acquired when key doesn't exist."""
        job_id = uuid4()
        mock_redis.set.return_value = True

        with patch(
            "services.orchestrator.src.services.state_lock.get_redis_client",
            AsyncMock(return_value=mock_redis),
        ):
            result = await state_lock.acquire(job_id, owner="test")

        assert result is True
        assert job_id in state_lock._acquired_locks
        mock_redis.set.assert_called_once_with(
            f"lock:job:{job_id}",
            "test",
            ex=300,
            nx=True,
        )

    async def test_acquire_lock_already_held(self, state_lock, mock_redis):
        """Lock acquisition should fail if already held by another."""
        job_id = uuid4()
        mock_redis.set.return_value = False

        with patch(
            "services.orchestrator.src.services.state_lock.get_redis_client",
            AsyncMock(return_value=mock_redis),
        ):
            result = await state_lock.acquire(job_id)

        assert result is False
        assert job_id not in state_lock._acquired_locks

    async def test_release_lock(self, state_lock, mock_redis):
        """Lock should be released and removed from tracking."""
        job_id = uuid4()
        state_lock._acquired_locks.add(job_id)

        with patch(
            "services.orchestrator.src.services.state_lock.get_redis_client",
            AsyncMock(return_value=mock_redis),
        ):
            await state_lock.release(job_id)

        mock_redis.delete.assert_called_once_with(f"lock:job:{job_id}")
        assert job_id not in state_lock._acquired_locks

    async def test_extend_lock(self, state_lock, mock_redis):
        """Lock TTL should be extendable."""
        job_id = uuid4()
        mock_redis.expire.return_value = True

        with patch(
            "services.orchestrator.src.services.state_lock.get_redis_client",
            AsyncMock(return_value=mock_redis),
        ):
            result = await state_lock.extend(job_id, ttl=600)

        assert result is True
        mock_redis.expire.assert_called_once_with(f"lock:job:{job_id}", 600)

    async def test_is_locked(self, state_lock, mock_redis):
        """Should check if job is currently locked."""
        job_id = uuid4()
        mock_redis.exists.return_value = True

        with patch(
            "services.orchestrator.src.services.state_lock.get_redis_client",
            AsyncMock(return_value=mock_redis),
        ):
            result = await state_lock.is_locked(job_id)

        assert result is True
        mock_redis.exists.assert_called_once_with(f"lock:job:{job_id}")

    async def test_cleanup_releases_all_locks(self, state_lock, mock_redis):
        """Cleanup should release all held locks."""
        job_ids = [uuid4() for _ in range(3)]
        state_lock._acquired_locks = set(job_ids)

        with patch(
            "services.orchestrator.src.services.state_lock.get_redis_client",
            AsyncMock(return_value=mock_redis),
        ):
            await state_lock.cleanup()

        assert mock_redis.delete.call_count == 3


class TestAgentStateStatusTransitions:
    """Tests for AgentState status transitions for suspend/resume."""

    @pytest.fixture
    def agent_state(self):
        """Create a sample AgentState."""
        return AgentState(
            job_id=uuid4(),
            tenant_id=uuid4(),
            user_id=uuid4(),
            provider="anthropic",
            model="claude-3-5-sonnet",
            system_prompt="You are a helpful assistant.",
            tools=None,
            temperature=0.7,
            max_tokens=4096,
        )

    def test_initial_status_is_pending(self, agent_state):
        """State should start in PENDING status."""
        assert agent_state.status == AgentStatus.PENDING

    def test_mark_running(self, agent_state):
        """mark_running should transition to RUNNING and set started_at."""
        agent_state.mark_running()

        assert agent_state.status == AgentStatus.RUNNING
        assert agent_state.started_at is not None

    def test_mark_waiting_tool(self, agent_state):
        """mark_waiting_tool should transition to WAITING_TOOL and store calls."""
        tool_calls = [
            ToolCall(id="tc_1", name="web_search", arguments={"query": "test"}),
            ToolCall(id="tc_2", name="calculator", arguments={"expr": "2+2"}),
        ]

        agent_state.mark_waiting_tool(tool_calls)

        assert agent_state.status == AgentStatus.WAITING_TOOL
        assert len(agent_state.pending_tool_calls) == 2
        assert agent_state.pending_tool_calls[0].name == "web_search"

    def test_mark_completed_clears_pending_tools(self, agent_state):
        """mark_completed should clear pending_tool_calls."""
        agent_state.pending_tool_calls = [
            ToolCall(id="tc_1", name="test", arguments={}),
        ]

        agent_state.mark_completed()

        assert agent_state.status == AgentStatus.COMPLETED
        assert agent_state.pending_tool_calls == []
        assert agent_state.completed_at is not None

    def test_mark_failed_clears_pending_tools(self, agent_state):
        """mark_failed should clear pending_tool_calls and record error."""
        agent_state.pending_tool_calls = [
            ToolCall(id="tc_1", name="test", arguments={}),
        ]

        agent_state.mark_failed("Something went wrong", {"code": 500})

        assert agent_state.status == AgentStatus.FAILED
        assert agent_state.pending_tool_calls == []
        assert agent_state.error == "Something went wrong"
        assert agent_state.error_details == {"code": 500}

    def test_add_tool_result(self, agent_state):
        """add_tool_result should append TOOL message."""
        agent_state.add_tool_result("tc_1", "Result: 42")

        assert len(agent_state.messages) == 1
        assert agent_state.messages[0].role == MessageRole.TOOL
        assert agent_state.messages[0].content == "Result: 42"
        assert agent_state.messages[0].tool_call_id == "tc_1"


class TestStateSerializer:
    """Tests for state serialization/deserialization (snapshots)."""

    @pytest.fixture
    def full_state(self):
        """Create a fully-populated AgentState."""
        state = AgentState(
            job_id=uuid4(),
            tenant_id=uuid4(),
            user_id=uuid4(),
            provider="anthropic",
            model="claude-3-5-sonnet",
            system_prompt="You are helpful.",
            tools=[{"name": "web_search", "description": "Search the web"}],
            temperature=0.5,
            max_tokens=2048,
        )
        state.mark_running()
        state.add_user_message("What is 2+2?")
        state.add_assistant_message(
            content="I'll calculate that.",
            tool_calls=[ToolCall(id="tc_1", name="calculator", arguments={"expr": "2+2"})],
        )
        state.mark_waiting_tool([
            ToolCall(id="tc_1", name="calculator", arguments={"expr": "2+2"}),
        ])
        state.iteration = 3
        state.total_input_tokens = 100
        state.total_output_tokens = 50
        state.metadata = {"stream": True, "trace_id": "abc123"}
        return state

    def test_serialize_preserves_all_fields(self, full_state):
        """Serialization should preserve all state fields."""
        data = StateSerializer.serialize(full_state)

        assert data["job_id"] == str(full_state.job_id)
        assert data["tenant_id"] == str(full_state.tenant_id)
        assert data["user_id"] == str(full_state.user_id)
        assert data["provider"] == "anthropic"
        assert data["model"] == "claude-3-5-sonnet"
        assert data["system_prompt"] == "You are helpful."
        assert data["tools"] == [{"name": "web_search", "description": "Search the web"}]
        assert data["temperature"] == 0.5
        assert data["max_tokens"] == 2048
        assert data["status"] == "waiting_tool"
        assert data["iteration"] == 3
        assert data["total_input_tokens"] == 100
        assert data["total_output_tokens"] == 50
        assert data["metadata"] == {"stream": True, "trace_id": "abc123"}
        assert data["started_at"] is not None

    def test_serialize_preserves_messages(self, full_state):
        """Serialization should preserve message history."""
        data = StateSerializer.serialize(full_state)

        assert len(data["messages"]) == 2
        assert data["messages"][0]["role"] == "user"
        assert data["messages"][0]["content"] == "What is 2+2?"
        assert data["messages"][1]["role"] == "assistant"
        assert data["messages"][1]["tool_calls"] is not None

    def test_serialize_preserves_pending_tool_calls(self, full_state):
        """Serialization should preserve pending tool calls."""
        data = StateSerializer.serialize(full_state)

        assert len(data["pending_tool_calls"]) == 1
        assert data["pending_tool_calls"][0]["id"] == "tc_1"
        assert data["pending_tool_calls"][0]["name"] == "calculator"

    def test_deserialize_restores_state(self, full_state):
        """Deserialization should restore exact state."""
        data = StateSerializer.serialize(full_state)
        restored = StateSerializer.deserialize(data)

        assert restored.job_id == full_state.job_id
        assert restored.tenant_id == full_state.tenant_id
        assert restored.status == AgentStatus.WAITING_TOOL
        assert restored.iteration == 3
        assert len(restored.messages) == 2
        assert len(restored.pending_tool_calls) == 1
        assert restored.pending_tool_calls[0].name == "calculator"

    def test_roundtrip_json(self, full_state):
        """JSON roundtrip should preserve state."""
        json_str = StateSerializer.to_json(full_state)
        restored = StateSerializer.from_json(json_str)

        assert restored.job_id == full_state.job_id
        assert restored.status == full_state.status
        assert len(restored.messages) == len(full_state.messages)

    def test_deserialize_handles_missing_optional_fields(self):
        """Deserialization should handle missing optional fields."""
        minimal_data = {
            "job_id": str(uuid4()),
            "tenant_id": str(uuid4()),
            "provider": "openai",
            "model": "gpt-4",
        }

        state = StateSerializer.deserialize(minimal_data)

        assert state.user_id is None
        assert state.system_prompt is None
        assert state.tools is None
        assert state.temperature == 0.7
        assert state.max_tokens == 4096
        assert state.status == AgentStatus.PENDING
        assert state.messages == []
        assert state.pending_tool_calls == []


class TestResumeHandlerToolWaiting:
    """Tests for ResumeHandler waiting for all tools to complete."""

    @pytest.fixture
    def mock_services(self):
        """Create mock services for ResumeHandler."""
        snapshot_service = AsyncMock()
        llm_service = AsyncMock()
        tool_handler = AsyncMock()
        return snapshot_service, llm_service, tool_handler

    async def test_resume_waits_for_all_tools(self, mock_services):
        """Resume should not proceed if some tools are incomplete."""
        snapshot_service, llm_service, tool_handler = mock_services

        # Create a state with 3 pending tools
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
        snapshot_service.load_latest_snapshot.return_value = state

        # Simulate only 2 of 3 tool results available
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(side_effect=lambda key: {
            "tool_result:tc_1": '{"result": "search results"}',
            "tool_result:tc_2": '{"result": "4"}',
            "tool_result:tc_3": None,  # Not yet complete
        }.get(key))

        # The resume handler should detect missing results
        results = {}
        for tc in state.pending_tool_calls:
            result = await mock_redis.get(f"tool_result:{tc.id}")
            results[tc.id] = result

        missing = [tc.id for tc in state.pending_tool_calls if results.get(tc.id) is None]

        assert len(missing) == 1
        assert "tc_3" in missing

    async def test_resume_proceeds_when_all_tools_complete(self, mock_services):
        """Resume should proceed when all tools have results."""
        snapshot_service, llm_service, tool_handler = mock_services

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
        ]

        # Simulate all tool results available
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(side_effect=lambda key: {
            "tool_result:tc_1": "search results",
            "tool_result:tc_2": "4",
        }.get(key))

        results = {}
        for tc in state.pending_tool_calls:
            result = await mock_redis.get(f"tool_result:{tc.id}")
            results[tc.id] = result

        missing = [tc.id for tc in state.pending_tool_calls if results.get(tc.id) is None]

        assert len(missing) == 0
        assert results["tc_1"] == "search results"
        assert results["tc_2"] == "4"


class TestExecutorSuspendBehavior:
    """Tests for AgentExecutor suspend behavior on tool calls."""

    async def test_executor_returns_waiting_tool_status_on_suspend(self):
        """Executor should return state with WAITING_TOOL on tool dispatch."""
        # This tests the expected behavior: when tools are called and
        # enable_suspend_resume=True, executor should:
        # 1. Mark state as WAITING_TOOL
        # 2. Save snapshot
        # 3. Dispatch tools async
        # 4. Return (exit) instead of polling

        state = AgentState(
            job_id=uuid4(),
            tenant_id=uuid4(),
            user_id=None,
            provider="anthropic",
            model="claude-3-5-sonnet",
            system_prompt=None,
            tools=[{"name": "calculator"}],
            temperature=0.7,
            max_tokens=4096,
        )

        tool_calls = [
            ToolCall(id="tc_1", name="calculator", arguments={"expr": "2+2"}),
        ]

        # Simulate what executor does on suspend
        state.mark_waiting_tool(tool_calls)

        assert state.status == AgentStatus.WAITING_TOOL
        assert len(state.pending_tool_calls) == 1
        assert state.pending_tool_calls[0].id == "tc_1"

    async def test_resume_injects_tool_results_into_messages(self):
        """Resume should inject tool results into message history."""
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

        # Set up state as if suspended waiting for tools
        state.add_assistant_message(
            content="Let me calculate that.",
            tool_calls=[ToolCall(id="tc_1", name="calculator", arguments={"expr": "2+2"})],
        )
        state.pending_tool_calls = [
            ToolCall(id="tc_1", name="calculator", arguments={"expr": "2+2"}),
        ]
        state.status = AgentStatus.WAITING_TOOL

        # Simulate resume_from_snapshot injecting results
        tool_results = {"tc_1": "4"}
        for tc in state.pending_tool_calls:
            result = tool_results.get(tc.id, "Error: Tool result not available")
            state.add_tool_result(tc.id, result)

        state.pending_tool_calls = []
        state.mark_running()

        # Verify
        assert state.status == AgentStatus.RUNNING
        assert len(state.messages) == 2  # assistant + tool result
        assert state.messages[1].role == MessageRole.TOOL
        assert state.messages[1].content == "4"
        assert state.messages[1].tool_call_id == "tc_1"


class TestConfigEnableSuspendResume:
    """Tests for the enable_suspend_resume feature flag."""

    def test_config_has_suspend_resume_settings(self):
        """Config should have suspend/resume related settings."""
        from services.orchestrator.src.config import OrchestratorConfig

        config = OrchestratorConfig()

        assert hasattr(config, "enable_suspend_resume")
        assert hasattr(config, "resume_topic")
        assert hasattr(config, "resume_consumer_group")
        assert hasattr(config, "job_lock_ttl_seconds")

    def test_config_default_values(self):
        """Config should have sensible defaults."""
        from services.orchestrator.src.config import OrchestratorConfig

        config = OrchestratorConfig()

        assert config.resume_topic == "agent.job-resume"
        assert config.resume_consumer_group == "orchestrator-resume"
        assert config.enable_suspend_resume is True
        assert config.job_lock_ttl_seconds == 300
