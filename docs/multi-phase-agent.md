# Multi-Phase Agent Architecture

## Context

The current agent is a simple reactive loop: LLM thinks, calls tools, gets results, repeats. It has no ability to decompose complex tasks into sub-tasks, run parallel research, evaluate its own output, or ask the user questions mid-execution. The effort-level system (LOW/MEDIUM/HIGH) only controls iteration count and prompt wording -- there's no structural difference in how the agent operates.

This plan introduces a **phase-aware execution framework** that transforms the agent into a multi-phase reasoning engine with task decomposition, parallel execution, self-evaluation, and human-in-the-loop capabilities.

---

## Architecture Overview

```
User Request
    |
 [TRIAGE] ---------> simple? ----> Existing AgentExecutor loop (no overhead)
    |
 complex (HIGH effort)
    |
 [DECOMPOSE] ------> LLM generates sub-tasks + builds todo list
    |
    ↓ reflect → update tasks → proceed / adjust / ask user
    |
 [EXECUTE] ---------> Parallel tool calls via Kafka + concurrent LLM calls
    |
    ↓ reflect → update tasks → proceed / adjust / ask user
    |
 [SYNTHESIZE] ------> LLM combines all sub-task results into draft
    |
    ↓ reflect → update tasks → proceed / adjust / ask user
    |
 [EVALUATE] ---------> LLM self-evaluates quality
    |  |
    |  ↓ reflect → gaps found? → adjust plan → loop back to EXECUTE
    |
    ↓ reflect → pass → proceed
    |
 [RESPOND] ----------> Stream final answer to user

Between every phase: reflect() updates todo list, decides next action.
Any phase can suspend to WAITING_USER (human-in-the-loop) and resume.
```

**Key design principle:** Phase state lives in `AgentState.metadata["phase_state"]` -- this already serializes through snapshots with zero changes to `StateSerializer`. The existing suspend/resume, tool dispatch, and event publishing infrastructure is reused entirely.

**Scope decision:** Multi-phase execution is triggered **only for HIGH effort**. LOW and MEDIUM effort continue using the existing simple agent loop with zero overhead. This keeps the rollout focused and avoids adding latency to common requests.

**Task tracking:** The agent maintains a **todo list** (task plan) that is created during decomposition and updated after each phase. Task status changes are emitted as SSE events so the frontend can show real-time progress. After each phase, the agent runs an **inter-phase reflection** step -- a lightweight LLM call that reviews what was accomplished, updates task statuses, and decides whether to proceed to the next phase or adjust the plan first. This ensures the agent operates coherently across phases rather than blindly following the initial decomposition.

---

## Implementation Steps

### Step 1: Phase Data Structures

**New file: `services/orchestrator/src/engine/phases.py`**

Define all phase-related types:

```python
class AgentPhase(str, Enum):
    TRIAGE = "triage"
    DECOMPOSE = "decompose"
    EXECUTE = "execute"
    SYNTHESIZE = "synthesize"
    EVALUATE = "evaluate"
    RESPOND = "respond"
    WAITING_USER = "waiting_user"
    SIMPLE = "simple"  # bypass multi-phase

class SubTaskStrategy(str, Enum):
    TOOL_CALL = "tool_call"      # Dispatch via existing Kafka tool workers
    LLM_CALL = "llm_call"       # Separate LLM completion (not in main conversation)

class SubTaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"

@dataclass
class SubTask:
    id: str
    description: str
    strategy: SubTaskStrategy
    status: SubTaskStatus = SubTaskStatus.PENDING
    dependencies: list[str] = field(default_factory=list)
    # For TOOL_CALL
    tool_name: str | None = None
    tool_arguments: dict | None = None
    tool_call_id: str | None = None
    # For LLM_CALL
    llm_prompt: str | None = None
    # Result
    result: str | None = None
    error: str | None = None

class TaskStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    ADJUSTED = "adjusted"  # Plan changed after reflection
    BLOCKED = "blocked"    # Waiting for user input

@dataclass
class TaskItem:
    """A high-level task in the agent's todo list. Visible to user via SSE."""
    id: str
    title: str               # Short label (e.g. "Research climate data")
    status: TaskStatus = TaskStatus.PENDING
    phase: AgentPhase | None = None  # Which phase this task maps to
    sub_task_ids: list[str] = field(default_factory=list)  # Linked sub-tasks
    notes: str | None = None  # Reflection notes after completion

@dataclass
class PhaseState:
    current_phase: AgentPhase
    phase_history: list[dict] = field(default_factory=list)

    # Todo list - the agent's high-level task plan
    task_plan: list[TaskItem] = field(default_factory=list)

    # Sub-tasks (detailed execution units)
    sub_tasks: list[SubTask] | None = None
    execution_order: list[list[str]] | None = None  # [[parallel group 1 ids], [group 2 ids]]
    current_group_index: int = 0
    synthesis_guidance: str | None = None
    draft_response: str | None = None
    evaluation_count: int = 0
    max_evaluations: int = 2
    pending_question: str | None = None
    question_context: str | None = None
```

Includes `to_dict()` / `from_dict()` for JSON roundtrip through `metadata`.

---

### Step 2: Phase Prompts

**New file: `services/orchestrator/src/prompts/phase_prompts.py`**

Five structured-output prompts:

1. **TRIAGE_PROMPT** - Classify request as `simple` or `multi_phase`. Include `needs_clarification` flag and optional `clarification_question`. Uses effort level as context (HIGH biases toward multi_phase).

2. **DECOMPOSE_PROMPT** - Generate sub-tasks with:
   - `sub_tasks[]` - each with id, description, strategy, tool_name/arguments or llm_prompt
   - `execution_order` - list of parallel groups (sub-task IDs that can run concurrently)
   - `synthesis_guidance` - instructions for combining results

3. **SYNTHESIZE_PROMPT** - Combine sub-task results into a coherent draft response, given the original question and synthesis_guidance.

4. **EVALUATE_PROMPT** - Assess draft response on completeness, accuracy, coherence, depth. Returns JSON with `score`, `pass` boolean, `gaps[]`, and `suggested_actions[]` (type: additional_search | deeper_analysis | restructure | ask_user).

5. **REFLECT_PROMPT** - Inter-phase reflection prompt. Given the current task plan, what was just completed, and results so far, decide:
   - Update task statuses (mark completed, note findings)
   - Whether to proceed to the next planned phase
   - Whether to adjust the plan (add/remove/reorder tasks)
   - Whether to ask the user a clarifying question before continuing
   Returns JSON with `updated_tasks[]`, `next_action` (proceed | adjust | ask_user), and `reasoning`.

---

### Step 3: Extend State Machine

**Modify: `services/orchestrator/src/engine/state.py`**

Add `WAITING_USER` status to `AgentStatus` enum:

```python
class AgentStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_TOOL = "waiting_tool"
    WAITING_USER = "waiting_user"  # NEW
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
```

Add `mark_waiting_user()` method to `AgentState`:

```python
def mark_waiting_user(self, question: str, context: str | None = None) -> None:
    self.status = AgentStatus.WAITING_USER
```

---

### Step 4: Extend Effort Config

**Modify: `services/orchestrator/src/prompts/effort_levels.py`**

Extend `EffortConfig` with multi-phase settings:

```python
class EffortConfig(NamedTuple):
    max_iterations: int
    prompt_section: str
    enable_multi_phase: bool       # NEW
    max_evaluations: int           # NEW
    evaluation_pass_score: int     # NEW (1-10)

# LOW:  enable_multi_phase=False, max_evaluations=0, evaluation_pass_score=5
# MEDIUM: enable_multi_phase=False, max_evaluations=0, evaluation_pass_score=6
# HIGH: enable_multi_phase=True, max_evaluations=3, evaluation_pass_score=7
```

Only HIGH effort enables multi-phase. LOW and MEDIUM use the existing loop unchanged.

---

### Step 5: Add Structured LLM Completion

**Modify: `services/orchestrator/src/services/llm_service.py`**

Add `complete_structured()` method for triage/decompose/evaluate calls that need JSON output:

```python
async def complete_structured(
    self,
    messages: list[LLMMessage],
    system_prompt: str,
    provider: str,
    model: str,
    temperature: float = 0.3,  # Lower for structured output
    max_tokens: int = 4096,
) -> LLMResponse:
    """Generate a structured JSON completion without tools."""
```

This wraps the existing `provider.complete()` with no tools, a JSON-focused system prompt suffix, and lower temperature. The caller parses the JSON from `response.content`. No provider changes needed -- both Anthropic and OpenAI handle this.

---

### Step 6: Phase Executor (Core)

**New file: `services/orchestrator/src/engine/phase_executor.py`**

The central orchestration class with **todo list management** and **inter-phase reflection**.

**Core loop pattern:** After each phase completes, `PhaseExecutor` runs `_reflect()` before transitioning to the next phase. Reflection reviews results, updates the task plan, and decides whether to proceed, adjust, or ask the user. This keeps the agent coherent across phases.

```python
class PhaseExecutor:
    def __init__(self, llm_service, tool_handler, snapshot_service, event_callback, config)

    async def execute(self, state: AgentState) -> AgentState:
        """Main entry point. Routes through phases based on phase_state."""
        phase_state = self._get_or_create_phase_state(state)

        if phase_state.current_phase == AgentPhase.TRIAGE:
            return await self._run_triage(state, phase_state)
        elif phase_state.current_phase == AgentPhase.DECOMPOSE:
            return await self._run_decompose(state, phase_state)
        # ... etc for each phase

    async def _transition_phase(self, state, phase_state, next_phase) -> AgentState:
        """Transition between phases with reflection.

        Called after every phase completion. This is the coherence mechanism:
        1. Mark current phase complete in history
        2. Update task statuses in todo list
        3. Emit task_plan_update SSE event (frontend shows progress)
        4. Run inter-phase reflection (LLM call)
        5. Based on reflection: proceed / adjust plan / ask user
        """
        # 1. Record phase completion
        phase_state.phase_history.append({
            "phase": phase_state.current_phase,
            "completed_at": datetime.now(UTC).isoformat(),
        })

        # 2. Update task statuses for completed phase
        for task in phase_state.task_plan:
            if task.phase == phase_state.current_phase and task.status == TaskStatus.IN_PROGRESS:
                task.status = TaskStatus.COMPLETED

        # 3. Emit todo list update event
        await self._emit_event(state, "task_plan_update", {
            "tasks": [t.to_dict() for t in phase_state.task_plan],
            "completed_phase": phase_state.current_phase.value,
            "next_phase": next_phase.value,
        })

        # 4. Run inter-phase reflection
        reflection = await self._reflect(state, phase_state, next_phase)

        # 5. Apply reflection decision
        if reflection["next_action"] == "ask_user":
            # Agent needs user input before continuing
            phase_state.pending_question = reflection["question"]
            phase_state.current_phase = AgentPhase.WAITING_USER
            state.mark_waiting_user(reflection["question"])
            await self._emit_event(state, "user_question", {...})
            await self.snapshot_service.save_snapshot(state)
            return state  # Suspend

        if reflection["next_action"] == "adjust":
            # Agent wants to modify the plan based on what it learned
            self._apply_plan_adjustments(phase_state, reflection["adjustments"])
            await self._emit_event(state, "task_plan_update", {
                "tasks": [t.to_dict() for t in phase_state.task_plan],
                "adjustment_reason": reflection["reasoning"],
            })

        # Proceed to next phase
        phase_state.current_phase = next_phase
        # Mark next tasks as in_progress
        for task in phase_state.task_plan:
            if task.phase == next_phase and task.status == TaskStatus.PENDING:
                task.status = TaskStatus.IN_PROGRESS

        return await self.execute(state)  # Continue to next phase

    async def _reflect(self, state, phase_state, proposed_next) -> dict:
        """Inter-phase reflection. Lightweight LLM call to review progress.

        The agent reviews:
        - What the current phase accomplished
        - Whether the results change the plan
        - Whether it needs user input before continuing
        - Whether tasks should be added, removed, or reordered

        Returns: {next_action: proceed|adjust|ask_user, reasoning, adjustments?, question?}
        """
        # Uses complete_structured() with REFLECT_PROMPT
        # Includes: original question, task_plan with statuses,
        #           sub-task results so far, proposed next phase

    async def _run_triage(self, state, phase_state) -> AgentState:
        """Single LLM call to classify complexity."""
        # If needs_clarification -> emit user_question, suspend to WAITING_USER
        # If simple -> set phase to SIMPLE, delegate to AgentExecutor
        # If multi_phase -> set phase to DECOMPOSE, call _transition_phase

    async def _run_decompose(self, state, phase_state) -> AgentState:
        """LLM generates sub-tasks AND builds the todo list."""
        # Parse sub_tasks and execution_order from LLM JSON response
        # Build task_plan (todo list) from sub-tasks:
        #   - Group sub-tasks into high-level tasks
        #   - Map tasks to phases (EXECUTE, SYNTHESIZE, EVALUATE, RESPOND)
        #   - Emit initial task_plan_update event
        # Transition to EXECUTE via _transition_phase

    async def _run_execute(self, state, phase_state) -> AgentState:
        """Execute current parallel group of sub-tasks."""
        group_ids = phase_state.execution_order[phase_state.current_group_index]
        group_tasks = [st for st in phase_state.sub_tasks if st.id in group_ids]

        tool_subtasks = [st for st in group_tasks if st.strategy == SubTaskStrategy.TOOL_CALL]
        llm_subtasks = [st for st in group_tasks if st.strategy == SubTaskStrategy.LLM_CALL]

        # Run LLM sub-tasks concurrently via asyncio.gather()
        # Dispatch tool sub-tasks to Kafka (reuse existing tool_handler.dispatch_tools_async)
        # If tool sub-tasks exist: suspend (WAITING_TOOL), resume via existing resume path
        # If only LLM sub-tasks: proceed directly to next group or SYNTHESIZE

    async def _run_synthesize(self, state, phase_state) -> AgentState:
        """Combine all sub-task results into draft response."""
        # After synthesis, transition to EVALUATE via _transition_phase

    async def _run_evaluate(self, state, phase_state) -> AgentState:
        """Self-evaluate draft response quality."""
        # If pass: transition to RESPOND via _transition_phase
        # If gaps + under max_evaluations: add new sub-tasks, loop to EXECUTE
        # If ask_user suggested: emit user_question, suspend to WAITING_USER

    async def _run_respond(self, state, phase_state) -> AgentState:
        """Stream final response using existing AgentExecutor."""
        # Inject draft_response + original question into messages
        # Use existing execute_streaming() for the final response with streaming

    async def resume_after_tools(self, state, tool_results) -> AgentState:
        """Called by ResumeHandler when tools complete during multi-phase."""
        # Inject tool results into matching sub-tasks
        # Update sub-task statuses
        # Emit subtask_complete events
        # Check if all sub-tasks in current group are complete
        # If yes: advance to next group or transition to SYNTHESIZE via _transition_phase
```

---

### Step 7: Integration Points

**Modify: `services/orchestrator/src/handlers/job_handler.py`**

In `handle_job()`, after creating `AgentExecutor`, check if multi-phase should be used. Only HIGH effort triggers multi-phase:

```python
# After creating executor (line ~132)
effort_level = message.get("metadata", {}).get("effort_level")
effort_config = get_effort_config(effort_level)

if config.enable_multi_phase and effort_config.enable_multi_phase:
    # Only HIGH effort reaches here (LOW/MEDIUM have enable_multi_phase=False)
    phase_executor = PhaseExecutor(
        llm_service=self.llm_service,
        tool_handler=self.tool_handler,
        snapshot_service=self.snapshot_service,
        event_callback=self._publish_event,
        config=config,
    )
    state = await phase_executor.execute(state)
else:
    # Existing path (LOW, MEDIUM, or multi-phase disabled)
    if message.get("stream", True):
        state = await executor.execute_streaming(state)
    else:
        state = await executor.execute(state)
```

**Modify: `services/orchestrator/src/handlers/resume_handler.py`**

In `handle_resume()`, after loading snapshot, check for multi-phase context:

```python
# After fetching tool_results (around line ~115)
phase_state_data = state.metadata.get("phase_state")
if phase_state_data and phase_state_data.get("current_phase") != "simple":
    # Multi-phase resume: route to PhaseExecutor
    phase_executor = PhaseExecutor(...)
    state = await phase_executor.resume_after_tools(state, tool_results)
else:
    # Existing path
    state = await executor.resume_from_snapshot(state, tool_results)
```

**Modify: `services/orchestrator/src/config.py`**

Add config fields:

```python
# Multi-phase settings
enable_multi_phase: bool = True
user_response_topic: str = "agent.user-response"
user_response_consumer_group: str = "orchestrator-user-response"
```

---

### Step 8: Human-in-the-Loop

**New file: `services/orchestrator/src/handlers/user_response_handler.py`**

Modeled exactly on `ConfirmHandler` (same lock/snapshot/resume pattern):

1. Consume from `agent.user-response` Kafka topic
2. Acquire distributed lock for the job
3. Load snapshot, verify `status == WAITING_USER`
4. Inject user's text response as a user message in `state.messages`
5. Clear `pending_question` from phase_state
6. Resume via `PhaseExecutor.execute(state)` (continues from current phase)

**Modify: `services/orchestrator/src/main.py`**

Register `UserResponseHandler` consumer alongside resume and confirm consumers (~15 lines).

**New Kafka topic: `agent.user-response`**

**Modify: `infrastructure/docker/kafka/create-topics.sh`**

Add: `agent.user-response` (3 partitions)

**Modify: `services/api-gateway/src/routers/chat.py`**

Add endpoint:

```python
@router.post("/user-response")
async def user_response(body: UserResponseRequest, ...):
    # Validate job belongs to tenant
    # Publish to agent.user-response Kafka topic
```

---

### Step 9: New SSE Events

Emitted by `PhaseExecutor` via existing `event_callback` (same `EventPublisher`):

| Event | When | Payload |
|-------|------|---------|
| `phase_start` | Phase begins | `{phase, phase_label, phase_index, total_phases}` |
| `phase_complete` | Phase ends | `{phase, duration_ms}` |
| `task_plan_update` | Todo list changes | `{tasks: [{id, title, status, notes}], adjustment_reason?}` |
| `subtask_start` | Sub-task begins | `{subtask_id, description, strategy, parallel_group}` |
| `subtask_complete` | Sub-task ends | `{subtask_id, status, summary}` |
| `user_question` | Agent asks user | `{question, context, input_type}` |
| `progress` | Status update | `{message, percent}` |

The `task_plan_update` event is the primary mechanism for the frontend to show a todo-list style progress tracker. It fires:
- After decomposition (initial task plan created)
- After each phase completion (task statuses updated)
- After reflection adjusts the plan (tasks added/removed/reordered)

Example `task_plan_update` payload:
```json
{
  "tasks": [
    {"id": "t1", "title": "Research climate data sources", "status": "completed", "notes": "Found 5 relevant datasets"},
    {"id": "t2", "title": "Analyze economic impacts", "status": "in_progress"},
    {"id": "t3", "title": "Compare regional differences", "status": "pending"},
    {"id": "t4", "title": "Synthesize findings", "status": "pending"},
    {"id": "t5", "title": "Quality review", "status": "pending"}
  ],
  "completed_phase": "execute",
  "next_phase": "synthesize"
}
```

These are additive -- existing events (delta, tool_call, tool_result, message, complete) continue unchanged. Old frontends ignore unknown SSE event types.

---

### Step 10: Update Archiver

**Modify: `services/archiver/src/services/postgres_writer.py`**

Add handling for new event types in the event routing logic. These get stored as `ChatMessage` records with appropriate metadata.

---

## Files Summary

### New Files (5)
| File | Purpose |
|------|---------|
| `services/orchestrator/src/engine/phases.py` | Phase enum, SubTask, PhaseState dataclasses |
| `services/orchestrator/src/engine/phase_executor.py` | Core multi-phase orchestration logic |
| `services/orchestrator/src/prompts/phase_prompts.py` | Triage, decompose, synthesize, evaluate prompts |
| `services/orchestrator/src/handlers/user_response_handler.py` | Human-in-the-loop Kafka handler |
| `tests/unit/orchestrator/test_phase_executor.py` | Unit tests |

### Modified Files (8)
| File | Change |
|------|--------|
| `services/orchestrator/src/engine/state.py` | Add `WAITING_USER` status + `mark_waiting_user()` |
| `services/orchestrator/src/prompts/effort_levels.py` | Extend `EffortConfig` with `enable_multi_phase`, `max_evaluations`, `evaluation_pass_score` |
| `services/orchestrator/src/services/llm_service.py` | Add `complete_structured()` method |
| `services/orchestrator/src/handlers/job_handler.py` | Route to `PhaseExecutor` when multi-phase enabled |
| `services/orchestrator/src/handlers/resume_handler.py` | Route to `PhaseExecutor.resume_after_tools()` when in multi-phase |
| `services/orchestrator/src/config.py` | Add `enable_multi_phase`, `user_response_topic`, `user_response_consumer_group` |
| `services/orchestrator/src/main.py` | Register `UserResponseHandler` consumer |
| `services/api-gateway/src/routers/chat.py` | Add `POST /user-response` endpoint |
| `infrastructure/docker/kafka/create-topics.sh` | Add `agent.user-response` topic |
| `services/archiver/src/services/postgres_writer.py` | Handle new event types |

### Unchanged Files
- `services/orchestrator/src/engine/serializer.py` - metadata dict already serializes
- `libs/llm/base.py` - no LLM abstraction changes
- `libs/common/tool_catalog.py` - no tool system changes
- `services/orchestrator/src/handlers/tool_handler.py` - tool dispatch unchanged
- `services/stream-edge/` - SSE delivery is event-type agnostic
- Frontend - Phase 1 works without frontend changes

---

## Backward Compatibility

- **Feature flag**: `enable_multi_phase: bool = True` in config. Set to `False` for instant rollback
- **Simple questions**: LOW and MEDIUM effort always use existing loop (0 overhead). HIGH effort runs triage first; simple HIGH-effort questions get classified as SIMPLE and fall through to existing `execute_streaming()` with ~1 extra LLM call
- **Existing tools/events**: All unchanged. New SSE events are additive
- **State serialization**: `PhaseState` lives in `metadata` dict, which is already JSONB. No migration needed
- **Token cost**: Triage/evaluate use low temperature + shorter max_tokens. Can use cheaper models (configurable per phase)

---

## Verification Plan

1. **Unit tests**: Test `PhaseExecutor` triage routing, decomposition parsing, evaluation loop capping, phase state serialization roundtrip
2. **Integration test**: Send a complex multi-part question (e.g. "Compare React vs Vue vs Svelte for enterprise apps") with HIGH effort. Verify:
   - Triage classifies as multi_phase
   - Decomposition creates multiple web_search sub-tasks
   - Sub-tasks execute in parallel via Kafka
   - Synthesis combines results
   - Evaluation runs and either passes or triggers one more round
   - Final response streams to SSE
3. **Simple query test**: Send "What time is it?" with MEDIUM effort. Verify triage classifies as simple, existing loop runs with no phase overhead
4. **Human-in-the-loop test**: Send ambiguous question, verify `user_question` SSE event emitted, POST `/user-response`, verify agent resumes
5. **Backward compat test**: Set `enable_multi_phase=False`, verify all existing tests pass unchanged
