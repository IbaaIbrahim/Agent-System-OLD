"""Unit tests for multi-phase agent execution.

Tests cover:
- Phase data structures (phases.py) serialization roundtrip
- PhaseState helper methods
- PhaseExecutor triage routing
- PhaseExecutor evaluation loop capping
- PhaseExecutor JSON parsing
- Effort config multi-phase flags
- WAITING_USER status transition
"""

import json
import sys
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

# Add orchestrator service path for imports
sys.path.insert(0, "services/orchestrator")

from libs.llm import LLMMessage, LLMResponse, MessageRole, ToolCall

from services.orchestrator.src.engine.phases import (
    AgentPhase,
    PhaseState,
    SubTask,
    SubTaskStatus,
    SubTaskStrategy,
    TaskItem,
    TaskStatus,
)
from services.orchestrator.src.engine.state import AgentState, AgentStatus
from services.orchestrator.src.prompts.effort_levels import (
    EffortLevel,
    EFFORT_CONFIGS,
    get_effort_config,
)


# -------------------------------------------------------------------
# Phase data structures
# -------------------------------------------------------------------


class TestPhaseDataStructures:
    """Tests for phase enums and dataclasses."""

    def test_agent_phase_values(self):
        """All expected phases exist."""
        assert AgentPhase.TRIAGE.value == "triage"
        assert AgentPhase.DECOMPOSE.value == "decompose"
        assert AgentPhase.EXECUTE.value == "execute"
        assert AgentPhase.SYNTHESIZE.value == "synthesize"
        assert AgentPhase.EVALUATE.value == "evaluate"
        assert AgentPhase.RESPOND.value == "respond"
        assert AgentPhase.WAITING_USER.value == "waiting_user"
        assert AgentPhase.SIMPLE.value == "simple"

    def test_sub_task_roundtrip(self):
        """SubTask serialization roundtrip preserves all fields."""
        st = SubTask(
            id="st-1",
            description="Search for climate data",
            strategy=SubTaskStrategy.TOOL_CALL,
            status=SubTaskStatus.COMPLETED,
            dependencies=["st-0"],
            tool_name="web_search",
            tool_arguments={"query": "climate data 2025"},
            tool_call_id="tc-abc",
            result="Found 5 datasets",
        )

        data = st.to_dict()
        restored = SubTask.from_dict(data)

        assert restored.id == "st-1"
        assert restored.description == "Search for climate data"
        assert restored.strategy == SubTaskStrategy.TOOL_CALL
        assert restored.status == SubTaskStatus.COMPLETED
        assert restored.dependencies == ["st-0"]
        assert restored.tool_name == "web_search"
        assert restored.tool_arguments == {"query": "climate data 2025"}
        assert restored.tool_call_id == "tc-abc"
        assert restored.result == "Found 5 datasets"

    def test_sub_task_llm_call_roundtrip(self):
        """SubTask with LLM_CALL strategy roundtrips correctly."""
        st = SubTask(
            id="st-2",
            description="Analyze economic impact",
            strategy=SubTaskStrategy.LLM_CALL,
            llm_prompt="Analyze the following data for economic impact...",
        )

        data = st.to_dict()
        restored = SubTask.from_dict(data)

        assert restored.strategy == SubTaskStrategy.LLM_CALL
        assert restored.llm_prompt == "Analyze the following data for economic impact..."
        assert restored.tool_name is None

    def test_task_item_roundtrip(self):
        """TaskItem serialization roundtrip preserves all fields."""
        task = TaskItem(
            id="t-1",
            title="Research climate data",
            status=TaskStatus.COMPLETED,
            phase=AgentPhase.EXECUTE,
            sub_task_ids=["st-1", "st-2"],
            notes="Found comprehensive data",
        )

        data = task.to_dict()
        restored = TaskItem.from_dict(data)

        assert restored.id == "t-1"
        assert restored.title == "Research climate data"
        assert restored.status == TaskStatus.COMPLETED
        assert restored.phase == AgentPhase.EXECUTE
        assert restored.sub_task_ids == ["st-1", "st-2"]
        assert restored.notes == "Found comprehensive data"

    def test_task_item_without_optional_fields(self):
        """TaskItem with minimal data roundtrips correctly."""
        data = {"id": "t-1", "title": "Simple task"}
        restored = TaskItem.from_dict(data)

        assert restored.status == TaskStatus.PENDING
        assert restored.phase is None
        assert restored.sub_task_ids == []
        assert restored.notes is None


class TestPhaseState:
    """Tests for PhaseState serialization and helper methods."""

    def _make_phase_state(self) -> PhaseState:
        """Create a fully populated PhaseState."""
        return PhaseState(
            current_phase=AgentPhase.EXECUTE,
            phase_history=[
                {"phase": "triage", "completed_at": "2025-01-01T00:00:00"},
                {"phase": "decompose", "completed_at": "2025-01-01T00:01:00"},
            ],
            task_plan=[
                TaskItem(
                    id="t-1", title="Research", status=TaskStatus.COMPLETED,
                    phase=AgentPhase.EXECUTE,
                ),
                TaskItem(
                    id="t-2", title="Synthesize", status=TaskStatus.PENDING,
                    phase=AgentPhase.SYNTHESIZE,
                ),
            ],
            sub_tasks=[
                SubTask(
                    id="st-1", description="Search A",
                    strategy=SubTaskStrategy.TOOL_CALL,
                    status=SubTaskStatus.COMPLETED,
                    tool_name="web_search",
                    tool_arguments={"query": "A"},
                    result="Results A",
                ),
                SubTask(
                    id="st-2", description="Search B",
                    strategy=SubTaskStrategy.TOOL_CALL,
                    status=SubTaskStatus.COMPLETED,
                    tool_name="web_search",
                    tool_arguments={"query": "B"},
                    result="Results B",
                ),
                SubTask(
                    id="st-3", description="Analyze",
                    strategy=SubTaskStrategy.LLM_CALL,
                    status=SubTaskStatus.PENDING,
                    llm_prompt="Analyze A and B",
                ),
            ],
            execution_order=[["st-1", "st-2"], ["st-3"]],
            current_group_index=1,
            synthesis_guidance="Combine results logically",
            draft_response="Initial draft",
            evaluation_count=1,
            max_evaluations=3,
            pending_question=None,
            question_context=None,
            resume_phase=AgentPhase.SYNTHESIZE,
        )

    def test_phase_state_roundtrip(self):
        """Full PhaseState serialization roundtrip."""
        ps = self._make_phase_state()
        data = ps.to_dict()
        restored = PhaseState.from_dict(data)

        assert restored.current_phase == AgentPhase.EXECUTE
        assert len(restored.phase_history) == 2
        assert len(restored.task_plan) == 2
        assert restored.task_plan[0].title == "Research"
        assert len(restored.sub_tasks) == 3
        assert restored.execution_order == [["st-1", "st-2"], ["st-3"]]
        assert restored.current_group_index == 1
        assert restored.synthesis_guidance == "Combine results logically"
        assert restored.draft_response == "Initial draft"
        assert restored.evaluation_count == 1
        assert restored.max_evaluations == 3
        assert restored.resume_phase == AgentPhase.SYNTHESIZE

    def test_phase_state_json_roundtrip(self):
        """PhaseState survives JSON serialization (as in metadata JSONB)."""
        ps = self._make_phase_state()
        json_str = json.dumps(ps.to_dict())
        data = json.loads(json_str)
        restored = PhaseState.from_dict(data)

        assert restored.current_phase == AgentPhase.EXECUTE
        assert len(restored.sub_tasks) == 3

    def test_get_sub_task(self):
        """get_sub_task returns correct sub-task or None."""
        ps = self._make_phase_state()

        st = ps.get_sub_task("st-1")
        assert st is not None
        assert st.description == "Search A"

        assert ps.get_sub_task("nonexistent") is None

    def test_get_current_group_subtasks(self):
        """get_current_group_subtasks returns sub-tasks for current group."""
        ps = self._make_phase_state()
        # current_group_index is 1, so group is ["st-3"]
        group = ps.get_current_group_subtasks()
        assert len(group) == 1
        assert group[0].id == "st-3"

    def test_get_current_group_subtasks_out_of_range(self):
        """Returns empty list when group index is out of range."""
        ps = PhaseState(current_phase=AgentPhase.EXECUTE)
        ps.execution_order = [["st-1"]]
        ps.current_group_index = 5

        assert ps.get_current_group_subtasks() == []

    def test_all_groups_complete(self):
        """all_groups_complete returns True when index >= len(order)."""
        ps = PhaseState(current_phase=AgentPhase.EXECUTE)
        ps.execution_order = [["st-1"], ["st-2"]]

        ps.current_group_index = 0
        assert ps.all_groups_complete() is False

        ps.current_group_index = 2
        assert ps.all_groups_complete() is True

    def test_current_group_complete(self):
        """current_group_complete checks all sub-tasks in current group."""
        ps = PhaseState(current_phase=AgentPhase.EXECUTE)
        ps.sub_tasks = [
            SubTask(id="st-1", description="A", strategy=SubTaskStrategy.TOOL_CALL,
                    status=SubTaskStatus.COMPLETED),
            SubTask(id="st-2", description="B", strategy=SubTaskStrategy.TOOL_CALL,
                    status=SubTaskStatus.RUNNING),
        ]
        ps.execution_order = [["st-1", "st-2"]]
        ps.current_group_index = 0

        assert ps.current_group_complete() is False

        ps.sub_tasks[1].status = SubTaskStatus.COMPLETED
        assert ps.current_group_complete() is True

    def test_current_group_complete_with_failed(self):
        """Failed sub-tasks count as complete (not blocking)."""
        ps = PhaseState(current_phase=AgentPhase.EXECUTE)
        ps.sub_tasks = [
            SubTask(id="st-1", description="A", strategy=SubTaskStrategy.TOOL_CALL,
                    status=SubTaskStatus.FAILED),
        ]
        ps.execution_order = [["st-1"]]
        ps.current_group_index = 0

        assert ps.current_group_complete() is True


# -------------------------------------------------------------------
# State machine extensions
# -------------------------------------------------------------------


class TestWaitingUserStatus:
    """Tests for WAITING_USER status in AgentState."""

    @pytest.fixture
    def agent_state(self):
        return AgentState(
            job_id=uuid4(),
            tenant_id=uuid4(),
            user_id=uuid4(),
            provider="anthropic",
            model="claude-sonnet-4-6",
            system_prompt=None,
            tools=None,
            temperature=0.7,
            max_tokens=4096,
        )

    def test_waiting_user_status_exists(self):
        """WAITING_USER should be a valid AgentStatus."""
        assert AgentStatus.WAITING_USER.value == "waiting_user"

    def test_mark_waiting_user(self, agent_state):
        """mark_waiting_user transitions to WAITING_USER status."""
        agent_state.mark_waiting_user("What format do you prefer?")
        assert agent_state.status == AgentStatus.WAITING_USER


# -------------------------------------------------------------------
# Effort config extensions
# -------------------------------------------------------------------


class TestEffortConfigMultiPhase:
    """Tests for multi-phase fields in EffortConfig."""

    def test_low_effort_no_multi_phase(self):
        """LOW effort should not enable multi-phase."""
        config = get_effort_config("low")
        assert config.enable_multi_phase is False
        assert config.max_evaluations == 0

    def test_medium_effort_no_multi_phase(self):
        """MEDIUM effort should not enable multi-phase."""
        config = get_effort_config("medium")
        assert config.enable_multi_phase is False
        assert config.max_evaluations == 0

    def test_high_effort_enables_multi_phase(self):
        """HIGH effort should enable multi-phase."""
        config = get_effort_config("high")
        assert config.enable_multi_phase is True
        assert config.max_evaluations == 3
        assert config.evaluation_pass_score == 7

    def test_default_effort_is_medium(self):
        """Default (None) effort returns MEDIUM config."""
        config = get_effort_config(None)
        assert config.enable_multi_phase is False
        assert config.max_iterations == 10

    def test_invalid_effort_returns_medium(self):
        """Invalid effort level returns MEDIUM config."""
        config = get_effort_config("ultra")
        assert config.enable_multi_phase is False


# -------------------------------------------------------------------
# PhaseExecutor
# -------------------------------------------------------------------


class TestPhaseExecutor:
    """Tests for PhaseExecutor routing and behavior."""

    @pytest.fixture
    def mock_services(self):
        """Create mock services for PhaseExecutor."""
        llm_service = AsyncMock()
        tool_handler = AsyncMock()
        snapshot_service = AsyncMock()
        event_callback = AsyncMock()

        config = MagicMock()
        config.enable_multi_phase = True

        return llm_service, tool_handler, snapshot_service, event_callback, config

    @pytest.fixture
    def agent_state(self):
        """Create a sample AgentState for testing."""
        state = AgentState(
            job_id=uuid4(),
            tenant_id=uuid4(),
            user_id=uuid4(),
            provider="anthropic",
            model="claude-sonnet-4-6",
            system_prompt=None,
            tools=[{"name": "web_search", "description": "Search the web"}],
            temperature=0.7,
            max_tokens=4096,
            metadata={"effort_level": "high"},
        )
        state.add_user_message("Compare React vs Vue vs Svelte")
        state.mark_running()
        return state

    def _make_executor(self, mock_services):
        """Create PhaseExecutor from mock services."""
        from services.orchestrator.src.engine.phase_executor import PhaseExecutor

        llm, tool, snap, callback, cfg = mock_services
        return PhaseExecutor(
            llm_service=llm,
            tool_handler=tool,
            snapshot_service=snap,
            event_callback=callback,
            config=cfg,
        )

    async def test_triage_routes_simple(self, mock_services, agent_state):
        """Triage classifying as 'simple' should fall back to simple executor."""
        executor = self._make_executor(mock_services)
        llm_service = mock_services[0]

        # Mock triage response: simple mode
        llm_service.complete_structured.return_value = LLMResponse(
            content='{"mode": "simple", "needs_clarification": false, "reasoning": "Simple question"}',
            tool_calls=None,
            finish_reason="end_turn",
            input_tokens=100,
            output_tokens=50,
        )

        # Mock the fallback executor (imported lazily inside methods)
        with patch(
            "services.orchestrator.src.engine.agent.AgentExecutor"
        ) as MockAgentExecutor:
            mock_simple_executor = AsyncMock()
            mock_simple_executor.execute_streaming.return_value = agent_state
            MockAgentExecutor.return_value = mock_simple_executor

            result = await executor.execute(agent_state)

        # Verify triage was called
        llm_service.complete_structured.assert_called_once()

        # Verify fallback to simple
        assert result.metadata["phase_state"]["current_phase"] == "simple"

    async def test_triage_routes_multi_phase(self, mock_services, agent_state):
        """Triage classifying as 'multi_phase' should proceed to decompose."""
        executor = self._make_executor(mock_services)
        llm_service = mock_services[0]

        # First call: triage -> multi_phase
        # Second call: reflect after triage -> proceed
        # Third call: decompose
        # Fourth call: reflect after decompose -> proceed
        # ... and so on. We'll stop at decompose by making it error.

        call_count = 0

        async def mock_complete_structured(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Triage
                return LLMResponse(
                    content='{"mode": "multi_phase", "needs_clarification": false}',
                    tool_calls=None,
                    finish_reason="end_turn",
                    input_tokens=100,
                    output_tokens=50,
                )
            elif call_count == 2:
                # Reflect after triage
                return LLMResponse(
                    content='{"next_action": "proceed", "task_updates": []}',
                    tool_calls=None,
                    finish_reason="end_turn",
                    input_tokens=50,
                    output_tokens=30,
                )
            elif call_count == 3:
                # Decompose - return empty plan to avoid complex flow
                return LLMResponse(
                    content='{"sub_tasks": [], "execution_order": [], "task_plan": [], "synthesis_guidance": ""}',
                    tool_calls=None,
                    finish_reason="end_turn",
                    input_tokens=200,
                    output_tokens=100,
                )
            else:
                # Reflect after decompose / synthesize etc -> proceed
                return LLMResponse(
                    content='{"next_action": "proceed", "task_updates": []}',
                    tool_calls=None,
                    finish_reason="end_turn",
                    input_tokens=50,
                    output_tokens=30,
                )

        llm_service.complete_structured = AsyncMock(side_effect=mock_complete_structured)

        # Mock synthesize to complete (since no sub-tasks, goes straight through)
        # The flow is: triage -> decompose -> execute (no groups) -> synthesize -> evaluate -> respond
        # We need to mock AgentExecutor for the respond phase
        with patch(
            "services.orchestrator.src.engine.agent.AgentExecutor"
        ) as MockAgentExecutor:
            mock_simple_executor = AsyncMock()
            # execute_streaming should mark state as completed
            async def mock_streaming(state):
                state.mark_completed()
                return state
            mock_simple_executor.execute_streaming = AsyncMock(side_effect=mock_streaming)
            MockAgentExecutor.return_value = mock_simple_executor

            result = await executor.execute(agent_state)

        # Should have called LLM multiple times (triage + reflection + decompose + ...)
        assert call_count >= 3

    async def test_triage_asks_user_when_needs_clarification(
        self, mock_services, agent_state
    ):
        """Triage with needs_clarification should suspend to WAITING_USER."""
        executor = self._make_executor(mock_services)
        llm_service = mock_services[0]
        snapshot_service = mock_services[2]

        llm_service.complete_structured.return_value = LLMResponse(
            content='{"mode": "multi_phase", "needs_clarification": true, "clarification_question": "What aspects should I focus on?"}',
            tool_calls=None,
            finish_reason="end_turn",
            input_tokens=100,
            output_tokens=50,
        )

        result = await executor.execute(agent_state)

        assert result.status == AgentStatus.WAITING_USER
        phase_state = PhaseState.from_dict(result.metadata["phase_state"])
        assert phase_state.current_phase == AgentPhase.WAITING_USER
        assert phase_state.pending_question == "What aspects should I focus on?"
        assert phase_state.resume_phase == AgentPhase.DECOMPOSE

        # Snapshot should have been saved
        snapshot_service.save_snapshot.assert_called_once()

    async def test_evaluation_loop_caps_at_max(self, mock_services, agent_state):
        """Evaluation should stop after max_evaluations rounds."""
        executor = self._make_executor(mock_services)

        # Set up phase_state already at evaluate with max evaluations hit
        phase_state = PhaseState(
            current_phase=AgentPhase.EVALUATE,
            evaluation_count=3,
            max_evaluations=3,
            task_plan=[
                TaskItem(id="t-1", title="Test", status=TaskStatus.COMPLETED),
            ],
            draft_response="The draft response",
        )
        agent_state.metadata["phase_state"] = phase_state.to_dict()

        llm_service = mock_services[0]
        # Reflect call -> proceed
        llm_service.complete_structured.return_value = LLMResponse(
            content='{"next_action": "proceed", "task_updates": []}',
            tool_calls=None,
            finish_reason="end_turn",
            input_tokens=50,
            output_tokens=30,
        )

        # Mock AgentExecutor for respond phase
        with patch(
            "services.orchestrator.src.engine.agent.AgentExecutor"
        ) as MockAgentExecutor:
            mock_exec = AsyncMock()
            async def mock_streaming(state):
                state.mark_completed()
                return state
            mock_exec.execute_streaming = AsyncMock(side_effect=mock_streaming)
            MockAgentExecutor.return_value = mock_exec

            result = await executor.execute(agent_state)

        # Should have skipped evaluation and gone to respond
        # (no evaluate LLM call, just reflect + respond)

    async def test_resume_after_user_response(self, mock_services, agent_state):
        """resume_after_user_response should clear question and continue."""
        executor = self._make_executor(mock_services)

        # Set up state as WAITING_USER
        phase_state = PhaseState(
            current_phase=AgentPhase.WAITING_USER,
            pending_question="Which framework?",
            resume_phase=AgentPhase.DECOMPOSE,
            task_plan=[
                TaskItem(
                    id="t-1", title="Research",
                    status=TaskStatus.BLOCKED,
                    phase=AgentPhase.EXECUTE,
                ),
            ],
        )
        agent_state.metadata["phase_state"] = phase_state.to_dict()
        agent_state.status = AgentStatus.WAITING_USER

        llm_service = mock_services[0]

        call_count = 0
        async def mock_complete_structured(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Decompose
                return LLMResponse(
                    content='{"sub_tasks": [], "execution_order": [], "task_plan": [], "synthesis_guidance": ""}',
                    tool_calls=None,
                    finish_reason="end_turn",
                    input_tokens=200,
                    output_tokens=100,
                )
            else:
                return LLMResponse(
                    content='{"next_action": "proceed", "task_updates": []}',
                    tool_calls=None,
                    finish_reason="end_turn",
                    input_tokens=50,
                    output_tokens=30,
                )

        llm_service.complete_structured = AsyncMock(side_effect=mock_complete_structured)

        with patch(
            "services.orchestrator.src.engine.agent.AgentExecutor"
        ) as MockAgentExecutor:
            mock_exec = AsyncMock()
            async def mock_streaming(state):
                state.mark_completed()
                return state
            mock_exec.execute_streaming = AsyncMock(side_effect=mock_streaming)
            MockAgentExecutor.return_value = mock_exec

            result = await executor.resume_after_user_response(agent_state)

        # Should have cleared question and resumed
        assert result.status != AgentStatus.WAITING_USER

    def test_parse_json_valid(self, mock_services):
        """_parse_json should parse valid JSON."""
        executor = self._make_executor(mock_services)
        result = executor._parse_json('{"mode": "simple", "score": 8}')
        assert result == {"mode": "simple", "score": 8}

    def test_parse_json_with_code_fences(self, mock_services):
        """_parse_json should handle markdown code fences."""
        executor = self._make_executor(mock_services)
        result = executor._parse_json('```json\n{"mode": "simple"}\n```')
        assert result == {"mode": "simple"}

    def test_parse_json_invalid(self, mock_services):
        """_parse_json should return empty dict for invalid JSON."""
        executor = self._make_executor(mock_services)
        result = executor._parse_json("not json at all")
        assert result == {}

    def test_get_user_question(self, mock_services, agent_state):
        """_get_user_question should extract first user message."""
        executor = self._make_executor(mock_services)
        question = executor._get_user_question(agent_state)
        assert question == "Compare React vs Vue vs Svelte"

    def test_get_user_question_empty(self, mock_services):
        """_get_user_question returns empty string when no user message."""
        executor = self._make_executor(mock_services)
        state = AgentState(
            job_id=uuid4(),
            tenant_id=uuid4(),
            user_id=None,
            provider="anthropic",
            model="claude-sonnet-4-6",
            system_prompt=None,
            tools=None,
            temperature=0.7,
            max_tokens=4096,
        )
        assert executor._get_user_question(state) == ""

    def test_get_tool_names(self, mock_services, agent_state):
        """_get_tool_names should extract tool names from state."""
        executor = self._make_executor(mock_services)
        names = executor._get_tool_names(agent_state)
        assert names == ["web_search"]

    def test_get_tool_names_no_tools(self, mock_services):
        """_get_tool_names returns empty list when no tools."""
        executor = self._make_executor(mock_services)
        state = AgentState(
            job_id=uuid4(),
            tenant_id=uuid4(),
            user_id=None,
            provider="anthropic",
            model="claude-sonnet-4-6",
            system_prompt=None,
            tools=None,
            temperature=0.7,
            max_tokens=4096,
        )
        assert executor._get_tool_names(state) == []


# -------------------------------------------------------------------
# Deduplication
# -------------------------------------------------------------------


class TestSubTaskDeduplication:
    """Tests for sub-task deduplication logic."""

    @pytest.fixture
    def mock_services(self):
        llm_service = AsyncMock()
        tool_handler = AsyncMock()
        snapshot_service = AsyncMock()
        event_callback = AsyncMock()
        config = MagicMock()
        config.enable_multi_phase = True
        return llm_service, tool_handler, snapshot_service, event_callback, config

    def _make_executor(self, mock_services):
        from services.orchestrator.src.engine.phase_executor import PhaseExecutor
        llm, tool, snap, callback, cfg = mock_services
        return PhaseExecutor(
            llm_service=llm, tool_handler=tool, snapshot_service=snap,
            event_callback=callback, config=cfg,
        )

    def test_removes_exact_duplicates(self, mock_services):
        """Identical tool_name + tool_arguments should be deduplicated."""
        executor = self._make_executor(mock_services)

        sub_tasks = [
            SubTask(id="st-1", description="Search A", strategy=SubTaskStrategy.TOOL_CALL,
                    tool_name="web_search", tool_arguments={"query": "car checklist"}),
            SubTask(id="st-2", description="Search B", strategy=SubTaskStrategy.TOOL_CALL,
                    tool_name="web_search", tool_arguments={"query": "car checklist"}),
            SubTask(id="st-3", description="Search C", strategy=SubTaskStrategy.TOOL_CALL,
                    tool_name="web_search", tool_arguments={"query": "car checklist"}),
        ]

        unique, removed = executor._deduplicate_subtasks(sub_tasks)

        assert len(unique) == 1
        assert unique[0].id == "st-1"
        assert removed == {"st-2", "st-3"}

    def test_keeps_different_queries(self, mock_services):
        """Sub-tasks with different tool_arguments should all be kept."""
        executor = self._make_executor(mock_services)

        sub_tasks = [
            SubTask(id="st-1", description="Engine", strategy=SubTaskStrategy.TOOL_CALL,
                    tool_name="web_search", tool_arguments={"query": "engine inspection"}),
            SubTask(id="st-2", description="Brakes", strategy=SubTaskStrategy.TOOL_CALL,
                    tool_name="web_search", tool_arguments={"query": "brake system check"}),
            SubTask(id="st-3", description="Electric", strategy=SubTaskStrategy.TOOL_CALL,
                    tool_name="web_search", tool_arguments={"query": "electrical diagnostics"}),
        ]

        unique, removed = executor._deduplicate_subtasks(sub_tasks)

        assert len(unique) == 3
        assert removed == set()

    def test_llm_call_subtasks_never_deduped(self, mock_services):
        """LLM_CALL sub-tasks should never be deduplicated."""
        executor = self._make_executor(mock_services)

        sub_tasks = [
            SubTask(id="st-1", description="Analyze A", strategy=SubTaskStrategy.LLM_CALL,
                    llm_prompt="Analyze this"),
            SubTask(id="st-2", description="Analyze B", strategy=SubTaskStrategy.LLM_CALL,
                    llm_prompt="Analyze this"),
        ]

        unique, removed = executor._deduplicate_subtasks(sub_tasks)

        assert len(unique) == 2
        assert removed == set()

    def test_mixed_strategies_dedupes_only_tools(self, mock_services):
        """Only tool_call sub-tasks are deduplicated; llm_call are kept."""
        executor = self._make_executor(mock_services)

        sub_tasks = [
            SubTask(id="st-1", description="Search", strategy=SubTaskStrategy.TOOL_CALL,
                    tool_name="web_search", tool_arguments={"query": "same"}),
            SubTask(id="st-2", description="Analyze", strategy=SubTaskStrategy.LLM_CALL,
                    llm_prompt="Analyze results"),
            SubTask(id="st-3", description="Search dup", strategy=SubTaskStrategy.TOOL_CALL,
                    tool_name="web_search", tool_arguments={"query": "same"}),
        ]

        unique, removed = executor._deduplicate_subtasks(sub_tasks)

        assert len(unique) == 2
        assert removed == {"st-3"}
        assert unique[0].id == "st-1"
        assert unique[1].id == "st-2"


# -------------------------------------------------------------------
# Config extensions
# -------------------------------------------------------------------


class TestOrchestratorConfig:
    """Tests for orchestrator config multi-phase fields."""

    def test_config_has_multi_phase_fields(self):
        """Config should have multi-phase settings."""
        from services.orchestrator.src.config import OrchestratorConfig

        config = OrchestratorConfig()

        assert hasattr(config, "enable_multi_phase")
        assert hasattr(config, "user_response_topic")
        assert hasattr(config, "user_response_consumer_group")

    def test_config_defaults(self):
        """Config should have sensible defaults."""
        from services.orchestrator.src.config import OrchestratorConfig

        config = OrchestratorConfig()

        assert config.enable_multi_phase is True
        assert config.user_response_topic == "agent.user-response"
        assert config.user_response_consumer_group == "orchestrator-user-response"
