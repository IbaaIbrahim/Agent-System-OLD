"""Multi-phase agent execution orchestrator.

Routes agent execution through phases: TRIAGE -> DECOMPOSE -> EXECUTE ->
SYNTHESIZE -> EVALUATE -> RESPOND, with inter-phase reflection and
todo list management.
"""

import asyncio
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from libs.common import get_logger
from libs.common.tool_catalog import TOOL_CATALOG
from libs.llm import LLMMessage, MessageRole, ToolCall

from ..handlers.tool_handler import ToolHandler
from ..prompts.effort_levels import get_effort_config
from ..prompts.phase_prompts import (
    DECOMPOSE_PROMPT,
    EVALUATE_PROMPT,
    REFLECT_PROMPT,
    SYNTHESIZE_PROMPT,
    TRIAGE_PROMPT,
)
from ..services.llm_service import LLMService
from ..services.snapshot_service import SnapshotService
from .phases import (
    AgentPhase,
    PhaseState,
    SubTask,
    SubTaskStatus,
    SubTaskStrategy,
    TaskItem,
    TaskStatus,
)
from .state import AgentState, AgentStatus

logger = get_logger(__name__)

# Max result length stored in sub-task to avoid bloating metadata
_MAX_SUBTASK_RESULT_LEN = 3000


class PhaseExecutor:
    """Orchestrates multi-phase agent execution with todo list and reflection."""

    def __init__(
        self,
        llm_service: LLMService,
        tool_handler: ToolHandler,
        snapshot_service: SnapshotService,
        event_callback: Any = None,
        config: Any = None,
    ) -> None:
        self.llm_service = llm_service
        self.tool_handler = tool_handler
        self.snapshot_service = snapshot_service
        self.event_callback = event_callback
        self.config = config

    # ------------------------------------------------------------------
    # Public entry points
    # ------------------------------------------------------------------

    async def execute(self, state: AgentState) -> AgentState:
        """Main entry point. Routes through phases based on phase_state."""
        phase_state = self._get_or_create_phase_state(state)

        logger.info(
            "PhaseExecutor routing",
            job_id=str(state.job_id),
            phase=phase_state.current_phase.value,
        )

        handlers = {
            AgentPhase.TRIAGE: self._run_triage,
            AgentPhase.DECOMPOSE: self._run_decompose,
            AgentPhase.EXECUTE: self._run_execute,
            AgentPhase.SYNTHESIZE: self._run_synthesize,
            AgentPhase.EVALUATE: self._run_evaluate,
            AgentPhase.RESPOND: self._run_respond,
        }

        handler = handlers.get(phase_state.current_phase)
        if handler is None:
            logger.error(
                "Unknown phase, falling back to simple",
                phase=phase_state.current_phase.value,
            )
            return await self._fallback_to_simple(state)

        try:
            return await handler(state, phase_state)
        except Exception:
            logger.exception(
                "Phase execution failed",
                job_id=str(state.job_id),
                phase=phase_state.current_phase.value,
            )
            raise

    async def resume_after_tools(
        self,
        state: AgentState,
        tool_results: dict[str, str],
    ) -> AgentState:
        """Resume multi-phase execution after tool results arrive.

        Called by ResumeHandler when all pending tools complete.
        """
        phase_state = self._get_phase_state(state)
        if phase_state is None:
            logger.error("No phase_state on resume", job_id=str(state.job_id))
            return state

        logger.info(
            "Resuming multi-phase after tools",
            job_id=str(state.job_id),
            phase=phase_state.current_phase.value,
            tool_count=len(tool_results),
        )

        # Tool results already emitted by resume handler as they arrived
        already_emitted = set(
            state.metadata.get("emitted_tool_result_ids", [])
        )

        # Inject tool results into matching sub-tasks
        for tc in state.pending_tool_calls:
            result = tool_results.get(tc.id, "Error: Tool result not available")
            state.add_tool_result(tc.id, result)

            # Update matching sub-task
            for st in phase_state.sub_tasks:
                if st.tool_call_id == tc.id:
                    truncated = result[:_MAX_SUBTASK_RESULT_LEN]
                    st.result = truncated
                    st.status = SubTaskStatus.COMPLETED
                    await self._emit_event(state, "subtask_complete", {
                        "subtask_id": st.id,
                        "status": "completed",
                        "summary": st.description,
                    })
                    break

            # Emit tool_result event only if not already emitted partially
            if tc.id not in already_emitted:
                await self._emit_event(state, "tool_result", {
                    "tool_call_id": tc.id,
                    "tool_name": tc.name,
                    "result": result,
                })

        # Clean up partial emission tracking
        state.metadata.pop("emitted_tool_result_ids", None)

        # Clear pending tools
        state.pending_tool_calls = []
        state.mark_running()

        # Check if current group is complete
        if phase_state.current_group_complete():
            phase_state.current_group_index += 1

            if phase_state.all_groups_complete():
                # All groups done -> transition to SYNTHESIZE
                self._save_phase_state(state, phase_state)
                return await self._transition_phase(
                    state, phase_state, AgentPhase.SYNTHESIZE
                )
            else:
                # More groups -> continue executing
                self._save_phase_state(state, phase_state)
                return await self._run_execute(state, phase_state)

        # Some sub-tasks still pending (shouldn't happen if resume waits for all)
        self._save_phase_state(state, phase_state)
        return state

    async def resume_after_user_response(self, state: AgentState) -> AgentState:
        """Resume after receiving a user response to a question."""
        phase_state = self._get_phase_state(state)
        if phase_state is None:
            return state

        # Clear the pending question
        phase_state.pending_question = None
        phase_state.question_context = None

        # Resume to the phase we were heading to before asking the user
        if phase_state.resume_phase:
            phase_state.current_phase = phase_state.resume_phase
            phase_state.resume_phase = None
        else:
            # Default: re-run triage with the user's clarification
            phase_state.current_phase = AgentPhase.TRIAGE

        state.mark_running()
        self._save_phase_state(state, phase_state)
        return await self.execute(state)

    # ------------------------------------------------------------------
    # Phase implementations
    # ------------------------------------------------------------------

    async def _run_triage(
        self, state: AgentState, phase_state: PhaseState
    ) -> AgentState:
        """Classify request complexity with a single LLM call."""
        await self._emit_phase_start(state, "triage", "Analyzing request...", 0, 6)

        # Get available tool names
        tool_names = self._get_tool_names(state)

        # Build triage prompt
        user_question = self._get_user_question(state)
        effort_level = (
            state.metadata.get("effort_level", "high") if state.metadata else "high"
        )
        prompt = TRIAGE_PROMPT.format(
            effort_level=effort_level.upper(),
            tool_names=", ".join(tool_names),
        )

        messages = [LLMMessage(role=MessageRole.USER, content=user_question)]

        response = await self.llm_service.complete_structured(
            messages=messages,
            system_prompt=prompt,
            provider_name=state.provider,
            model=state.model,
        )

        state.increment_tokens(response.input_tokens, response.output_tokens)

        # Parse triage decision
        decision = self._parse_json(response.content or "{}")

        await self._emit_phase_complete(state, "triage")

        mode = decision.get("mode", "simple")
        needs_clarification = decision.get("needs_clarification", False)

        if needs_clarification:
            question = decision.get("clarification_question", "Could you clarify?")
            phase_state.resume_phase = (
                AgentPhase.DECOMPOSE if mode == "multi_phase" else AgentPhase.SIMPLE
            )
            return await self._ask_user(state, phase_state, question)

        if mode == "multi_phase":
            phase_state.current_phase = AgentPhase.DECOMPOSE
            self._save_phase_state(state, phase_state)
            return await self._transition_phase(
                state, phase_state, AgentPhase.DECOMPOSE
            )

        # Simple mode
        logger.info("Triage: simple mode", job_id=str(state.job_id))
        return await self._fallback_to_simple(state)

    async def _run_decompose(
        self, state: AgentState, phase_state: PhaseState
    ) -> AgentState:
        """Decompose task into sub-tasks and build todo list."""
        await self._emit_phase_start(
            state, "decompose", "Planning approach...", 1, 6
        )

        user_question = self._get_user_question(state)
        tool_names = self._get_tool_names(state)
        tool_descriptions = self._get_tool_descriptions(state)

        prompt = DECOMPOSE_PROMPT.format(
            tool_names=", ".join(tool_names),
            tool_descriptions=tool_descriptions,
        )

        messages = [LLMMessage(role=MessageRole.USER, content=user_question)]

        response = await self.llm_service.complete_structured(
            messages=messages,
            system_prompt=prompt,
            provider_name=state.provider,
            model=state.model,
            max_tokens=8192,
        )

        state.increment_tokens(response.input_tokens, response.output_tokens)

        plan = self._parse_json(response.content or "{}")

        # Build sub-tasks
        sub_tasks = []
        for st_data in plan.get("sub_tasks", []):
            st = SubTask(
                id=st_data.get("id", f"st-{uuid.uuid4().hex[:8]}"),
                description=st_data.get("description", ""),
                strategy=SubTaskStrategy(
                    st_data.get("strategy", "tool_call")
                ),
                tool_name=st_data.get("tool_name"),
                tool_arguments=st_data.get("tool_arguments"),
                llm_prompt=st_data.get("llm_prompt"),
                dependencies=st_data.get("dependencies", []),
            )
            sub_tasks.append(st)

        # Deduplicate tool_call sub-tasks with identical arguments
        sub_tasks, removed_ids = self._deduplicate_subtasks(sub_tasks)

        phase_state.sub_tasks = sub_tasks

        # Clean removed IDs from execution_order
        raw_order = plan.get("execution_order", [])
        phase_state.execution_order = [
            [sid for sid in group if sid not in removed_ids]
            for group in raw_order
        ]
        # Remove empty groups
        phase_state.execution_order = [
            g for g in phase_state.execution_order if g
        ]
        phase_state.synthesis_guidance = plan.get("synthesis_guidance", "")
        phase_state.current_group_index = 0

        # Build task plan (todo list) from decomposition
        task_plan = []
        for tp_data in plan.get("task_plan", []):
            task = TaskItem(
                id=tp_data.get("id", f"t-{uuid.uuid4().hex[:8]}"),
                title=tp_data.get("title", ""),
                phase=AgentPhase.EXECUTE,
                sub_task_ids=tp_data.get("sub_task_ids", []),
            )
            task_plan.append(task)

        # Add standard phases as tasks
        task_plan.append(TaskItem(
            id="t-synthesize",
            title="Combine and synthesize findings",
            phase=AgentPhase.SYNTHESIZE,
        ))
        task_plan.append(TaskItem(
            id="t-evaluate",
            title="Review quality and completeness",
            phase=AgentPhase.EVALUATE,
        ))
        task_plan.append(TaskItem(
            id="t-respond",
            title="Deliver final response",
            phase=AgentPhase.RESPOND,
        ))

        phase_state.task_plan = task_plan

        # Set max evaluations from effort config
        effort_config = get_effort_config(
            state.metadata.get("effort_level") if state.metadata else None
        )
        phase_state.max_evaluations = effort_config.max_evaluations

        self._save_phase_state(state, phase_state)

        # Emit initial task plan
        await self._emit_event(state, "task_plan_update", {
            "tasks": [t.to_dict() for t in phase_state.task_plan],
        })

        await self._emit_phase_complete(state, "decompose")

        return await self._transition_phase(
            state, phase_state, AgentPhase.EXECUTE
        )

    async def _run_execute(
        self, state: AgentState, phase_state: PhaseState
    ) -> AgentState:
        """Execute the current parallel group of sub-tasks."""
        if phase_state.all_groups_complete():
            return await self._transition_phase(
                state, phase_state, AgentPhase.SYNTHESIZE
            )

        group_index = phase_state.current_group_index
        group_subtasks = phase_state.get_current_group_subtasks()

        if not group_subtasks:
            phase_state.current_group_index += 1
            self._save_phase_state(state, phase_state)
            return await self._run_execute(state, phase_state)

        await self._emit_phase_start(
            state, "execute",
            f"Executing tasks (group {group_index + 1}/{len(phase_state.execution_order)})...",
            2, 6,
        )

        tool_subtasks = [
            st for st in group_subtasks
            if st.strategy == SubTaskStrategy.TOOL_CALL
        ]
        llm_subtasks = [
            st for st in group_subtasks
            if st.strategy == SubTaskStrategy.LLM_CALL
        ]

        # Mark sub-tasks as running
        for st in group_subtasks:
            st.status = SubTaskStatus.RUNNING
            await self._emit_event(state, "subtask_start", {
                "subtask_id": st.id,
                "description": st.description,
                "strategy": st.strategy.value,
                "parallel_group": group_index,
            })

        # Execute LLM sub-tasks concurrently
        if llm_subtasks:
            llm_futures = [
                self._execute_llm_subtask(state, st) for st in llm_subtasks
            ]
            llm_results = await asyncio.gather(
                *llm_futures, return_exceptions=True
            )
            for st, result in zip(llm_subtasks, llm_results, strict=True):
                if isinstance(result, Exception):
                    st.status = SubTaskStatus.FAILED
                    st.error = str(result)
                    await self._emit_event(state, "subtask_complete", {
                        "subtask_id": st.id,
                        "status": "failed",
                        "summary": str(result),
                    })
                else:
                    st.result = result[:_MAX_SUBTASK_RESULT_LEN]
                    st.status = SubTaskStatus.COMPLETED
                    await self._emit_event(state, "subtask_complete", {
                        "subtask_id": st.id,
                        "status": "completed",
                        "summary": st.description,
                    })

        # Dispatch tool sub-tasks to Kafka
        if tool_subtasks:
            tool_calls = []
            for st in tool_subtasks:
                tc_id = f"tc-{uuid.uuid4().hex[:12]}"
                st.tool_call_id = tc_id
                tc = ToolCall(
                    id=tc_id,
                    name=st.tool_name or "",
                    arguments=st.tool_arguments or {},
                )
                tool_calls.append(tc)

            # Add assistant message with tool calls to conversation
            state.add_assistant_message(
                content=None,
                tool_calls=tool_calls,
            )

            # Emit tool_call event
            await self._emit_event(state, "tool_call", {
                "tool_calls": [
                    {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                    for tc in tool_calls
                ],
            })

            # Save state and suspend for tool results
            state.mark_waiting_tool(tool_calls)
            self._save_phase_state(state, phase_state)
            await self.snapshot_service.save_snapshot(state)
            await self.snapshot_service.update_job(state)

            # Dispatch to Kafka
            await self.tool_handler.dispatch_tools_async(state, tool_calls)

            await self._emit_event(state, "suspended", {
                "pending_tools": [
                    {"id": tc.id, "name": tc.name} for tc in tool_calls
                ],
                "snapshot_sequence": state.iteration,
            })

            logger.info(
                "Phase executor suspended for tools",
                job_id=str(state.job_id),
                tool_count=len(tool_calls),
            )

            return state  # Suspend - will resume via resume_after_tools

        # No tool sub-tasks: advance to next group or synthesize
        phase_state.current_group_index += 1
        self._save_phase_state(state, phase_state)

        await self._emit_phase_complete(state, "execute")

        if phase_state.all_groups_complete():
            return await self._transition_phase(
                state, phase_state, AgentPhase.SYNTHESIZE
            )
        else:
            return await self._run_execute(state, phase_state)

    async def _run_synthesize(
        self, state: AgentState, phase_state: PhaseState
    ) -> AgentState:
        """Combine all sub-task results into a draft response."""
        await self._emit_phase_start(
            state, "synthesize", "Combining findings...", 3, 6
        )

        user_question = self._get_user_question(state)
        sub_task_results = self._format_subtask_results(phase_state)

        prompt = SYNTHESIZE_PROMPT.format(
            user_question=user_question,
            sub_task_results=sub_task_results,
            synthesis_guidance=phase_state.synthesis_guidance or "Combine logically.",
        )

        messages = [LLMMessage(role=MessageRole.USER, content=prompt)]

        response = await self.llm_service.complete_structured(
            messages=messages,
            system_prompt="You are a research synthesizer. Write a comprehensive response.",
            provider_name=state.provider,
            model=state.model,
            temperature=0.5,
            max_tokens=8192,
        )

        state.increment_tokens(response.input_tokens, response.output_tokens)
        phase_state.draft_response = response.content or ""

        self._save_phase_state(state, phase_state)
        await self._emit_phase_complete(state, "synthesize")

        return await self._transition_phase(
            state, phase_state, AgentPhase.EVALUATE
        )

    async def _run_evaluate(
        self, state: AgentState, phase_state: PhaseState
    ) -> AgentState:
        """Self-evaluate draft response quality."""
        effort_config = get_effort_config(
            state.metadata.get("effort_level") if state.metadata else None
        )

        # Skip evaluation if max_evaluations is 0
        if effort_config.max_evaluations == 0:
            return await self._transition_phase(
                state, phase_state, AgentPhase.RESPOND
            )

        if phase_state.evaluation_count >= phase_state.max_evaluations:
            logger.info(
                "Max evaluations reached, proceeding to respond",
                job_id=str(state.job_id),
                count=phase_state.evaluation_count,
            )
            return await self._transition_phase(
                state, phase_state, AgentPhase.RESPOND
            )

        await self._emit_phase_start(
            state, "evaluate", "Reviewing quality...", 4, 6
        )

        phase_state.evaluation_count += 1

        user_question = self._get_user_question(state)
        sub_task_summaries = "\n".join(
            f"- {st.description}: {st.status.value}"
            for st in phase_state.sub_tasks
        )

        prompt = EVALUATE_PROMPT.format(
            user_question=user_question,
            sub_task_summaries=sub_task_summaries,
            draft_response=phase_state.draft_response or "",
            pass_score=effort_config.evaluation_pass_score,
        )

        messages = [LLMMessage(role=MessageRole.USER, content=prompt)]

        response = await self.llm_service.complete_structured(
            messages=messages,
            system_prompt="You are a quality evaluator. Be critical but fair.",
            provider_name=state.provider,
            model=state.model,
        )

        state.increment_tokens(response.input_tokens, response.output_tokens)

        evaluation = self._parse_json(response.content or "{}")
        score = evaluation.get("score", 10)
        passes = evaluation.get("pass", True)
        gaps = evaluation.get("gaps", [])
        suggested_actions = evaluation.get("suggested_actions", [])

        await self._emit_phase_complete(state, "evaluate")

        logger.info(
            "Evaluation result",
            job_id=str(state.job_id),
            score=score,
            passes=passes,
            gaps=gaps,
            eval_count=phase_state.evaluation_count,
        )

        if passes:
            self._save_phase_state(state, phase_state)
            return await self._transition_phase(
                state, phase_state, AgentPhase.RESPOND
            )

        # Check for ask_user suggestion
        for action in suggested_actions:
            if action.get("type") == "ask_user":
                question = action.get("description", "Could you clarify?")
                phase_state.resume_phase = AgentPhase.EVALUATE
                self._save_phase_state(state, phase_state)
                return await self._ask_user(state, phase_state, question)

        # Add new sub-tasks from suggested actions
        new_subtasks = []
        new_ids = []
        for action in suggested_actions:
            if action.get("type") in ("additional_search", "deeper_analysis"):
                st_id = f"st-eval-{uuid.uuid4().hex[:8]}"
                new_ids.append(st_id)
                tool_name = action.get("tool_name")
                tool_args = action.get("tool_arguments")

                if tool_name and tool_args:
                    strategy = SubTaskStrategy.TOOL_CALL
                else:
                    strategy = SubTaskStrategy.LLM_CALL

                new_st = SubTask(
                    id=st_id,
                    description=action.get("description", "Follow-up research"),
                    strategy=strategy,
                    tool_name=tool_name,
                    tool_arguments=tool_args,
                    llm_prompt=action.get("description") if strategy == SubTaskStrategy.LLM_CALL else None,
                )
                new_subtasks.append(new_st)

        if new_subtasks:
            phase_state.sub_tasks.extend(new_subtasks)
            phase_state.execution_order.append(new_ids)
            phase_state.current_group_index = len(phase_state.execution_order) - 1

            # Add evaluation follow-up task to todo list
            phase_state.task_plan.append(TaskItem(
                id=f"t-eval-followup-{phase_state.evaluation_count}",
                title=f"Follow-up research (eval round {phase_state.evaluation_count})",
                phase=AgentPhase.EXECUTE,
                sub_task_ids=new_ids,
            ))

            self._save_phase_state(state, phase_state)

            await self._emit_event(state, "task_plan_update", {
                "tasks": [t.to_dict() for t in phase_state.task_plan],
                "adjustment_reason": f"Evaluation found gaps: {', '.join(gaps)}",
            })

            # Loop back to execute
            return await self._run_execute(state, phase_state)

        # No actionable suggestions, proceed anyway
        self._save_phase_state(state, phase_state)
        return await self._transition_phase(
            state, phase_state, AgentPhase.RESPOND
        )

    async def _run_respond(
        self, state: AgentState, phase_state: PhaseState
    ) -> AgentState:
        """Stream the final response to the user."""
        await self._emit_phase_start(
            state, "respond", "Preparing response...", 5, 6
        )

        # Mark all remaining tasks as completed
        for task in phase_state.task_plan:
            if task.status in (TaskStatus.PENDING, TaskStatus.IN_PROGRESS):
                task.status = TaskStatus.COMPLETED

        self._save_phase_state(state, phase_state)

        await self._emit_event(state, "task_plan_update", {
            "tasks": [t.to_dict() for t in phase_state.task_plan],
        })

        # Use the draft response as the final content
        draft = phase_state.draft_response or ""

        if draft:
            # Inject the draft as a system-guided assistant completion
            # by adding context to the conversation and letting the LLM
            # produce the final polished output via streaming
            context_msg = (
                f"Based on thorough multi-phase research, here is the "
                f"comprehensive response to deliver:\n\n{draft}"
            )
            state.add_user_message(context_msg)

            # Use the existing AgentExecutor for streaming the final response
            from .agent import AgentExecutor

            executor = AgentExecutor(
                llm_service=self.llm_service,
                tool_handler=self.tool_handler,
                snapshot_service=self.snapshot_service,
                event_callback=self.event_callback,
            )

            # Execute with limited iterations for the final response
            state = await executor.execute_streaming(state)
        else:
            # No draft available, emit complete directly
            state.mark_completed()
            await self._emit_event(state, "complete", {
                "total_input_tokens": state.total_input_tokens,
                "total_output_tokens": state.total_output_tokens,
            })

        await self._emit_phase_complete(state, "respond")
        return state

    # ------------------------------------------------------------------
    # Inter-phase reflection
    # ------------------------------------------------------------------

    async def _transition_phase(
        self,
        state: AgentState,
        phase_state: PhaseState,
        next_phase: AgentPhase,
    ) -> AgentState:
        """Transition between phases with reflection.

        1. Record phase completion in history
        2. Update task statuses in todo list
        3. Emit task_plan_update SSE event
        4. Run inter-phase reflection (LLM call)
        5. Based on reflection: proceed / adjust / ask user
        """
        completed_phase = phase_state.current_phase

        # Record phase completion
        phase_state.phase_history.append({
            "phase": completed_phase.value,
            "completed_at": datetime.now(UTC).isoformat(),
        })

        # Update task statuses for completed phase
        for task in phase_state.task_plan:
            if (
                task.phase == completed_phase
                and task.status == TaskStatus.IN_PROGRESS
            ):
                task.status = TaskStatus.COMPLETED

        # Emit todo list update
        await self._emit_event(state, "task_plan_update", {
            "tasks": [t.to_dict() for t in phase_state.task_plan],
            "completed_phase": completed_phase.value,
            "next_phase": next_phase.value,
        })

        # Run inter-phase reflection
        reflection = await self._reflect(state, phase_state, next_phase)

        # Apply reflection decision
        next_action = reflection.get("next_action", "proceed")

        # Apply task updates from reflection
        for update in reflection.get("task_updates", []):
            task_id = update.get("task_id")
            for task in phase_state.task_plan:
                if task.id == task_id:
                    if update.get("status"):
                        task.status = TaskStatus(update["status"])
                    if update.get("notes"):
                        task.notes = update["notes"]

        if next_action == "ask_user":
            question = reflection.get("question", "Could you clarify?")
            context = reflection.get("question_context")
            phase_state.resume_phase = next_phase
            self._save_phase_state(state, phase_state)
            return await self._ask_user(state, phase_state, question, context)

        if next_action == "adjust":
            self._apply_plan_adjustments(
                phase_state, reflection.get("adjustments", [])
            )
            await self._emit_event(state, "task_plan_update", {
                "tasks": [t.to_dict() for t in phase_state.task_plan],
                "adjustment_reason": reflection.get("reasoning", "Plan adjusted"),
            })

        # Proceed to next phase
        phase_state.current_phase = next_phase

        # Mark next tasks as in_progress
        for task in phase_state.task_plan:
            if task.phase == next_phase and task.status == TaskStatus.PENDING:
                task.status = TaskStatus.IN_PROGRESS

        self._save_phase_state(state, phase_state)
        return await self.execute(state)

    async def _reflect(
        self,
        state: AgentState,
        phase_state: PhaseState,
        proposed_next: AgentPhase,
    ) -> dict[str, Any]:
        """Inter-phase reflection. Lightweight LLM call to review progress."""
        user_question = self._get_user_question(state)
        task_plan_json = json.dumps(
            [t.to_dict() for t in phase_state.task_plan], indent=2
        )
        results_summary = self._format_subtask_results(phase_state)

        prompt = REFLECT_PROMPT.format(
            user_question=user_question,
            task_plan_json=task_plan_json,
            completed_phase=phase_state.current_phase.value,
            proposed_next_phase=proposed_next.value,
            results_summary=results_summary[:4000],  # Truncate for token budget
        )

        messages = [LLMMessage(role=MessageRole.USER, content=prompt)]

        response = await self.llm_service.complete_structured(
            messages=messages,
            system_prompt="You are reviewing progress between execution phases.",
            provider_name=state.provider,
            model=state.model,
            temperature=0.2,
            max_tokens=2048,
        )

        state.increment_tokens(response.input_tokens, response.output_tokens)
        return self._parse_json(response.content or '{"next_action": "proceed"}')

    # ------------------------------------------------------------------
    # Human-in-the-loop
    # ------------------------------------------------------------------

    async def _ask_user(
        self,
        state: AgentState,
        phase_state: PhaseState,
        question: str,
        context: str | None = None,
    ) -> AgentState:
        """Ask the user a clarifying question and suspend."""
        phase_state.pending_question = question
        phase_state.question_context = context
        phase_state.current_phase = AgentPhase.WAITING_USER

        # Mark relevant tasks as blocked
        for task in phase_state.task_plan:
            if task.status == TaskStatus.IN_PROGRESS:
                task.status = TaskStatus.BLOCKED

        self._save_phase_state(state, phase_state)

        state.mark_waiting_user(question, context)

        await self._emit_event(state, "user_question", {
            "question": question,
            "context": context,
            "input_type": "text",
        })

        await self._emit_event(state, "task_plan_update", {
            "tasks": [t.to_dict() for t in phase_state.task_plan],
        })

        # Save snapshot for resume
        await self.snapshot_service.save_snapshot(state)
        await self.snapshot_service.update_job(state)

        logger.info(
            "Agent asking user question",
            job_id=str(state.job_id),
            question=question,
        )

        return state  # Suspend

    # ------------------------------------------------------------------
    # LLM sub-task execution
    # ------------------------------------------------------------------

    async def _execute_llm_subtask(
        self, state: AgentState, sub_task: SubTask
    ) -> str:
        """Execute a single LLM sub-task."""
        user_question = self._get_user_question(state)

        prompt = (
            f"Original user question: {user_question}\n\n"
            f"Sub-task: {sub_task.description}\n\n"
        )
        if sub_task.llm_prompt:
            prompt += f"Instructions: {sub_task.llm_prompt}\n\n"
        prompt += "Provide a thorough, detailed response to this sub-task."

        messages = [LLMMessage(role=MessageRole.USER, content=prompt)]

        response = await self.llm_service.complete_structured(
            messages=messages,
            system_prompt="You are a research assistant completing a sub-task.",
            provider_name=state.provider,
            model=state.model,
            temperature=0.5,
            max_tokens=4096,
        )

        state.increment_tokens(response.input_tokens, response.output_tokens)
        return response.content or ""

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_or_create_phase_state(self, state: AgentState) -> PhaseState:
        """Get existing phase state or create new one starting at TRIAGE."""
        existing = state.metadata.get("phase_state")
        if existing:
            return PhaseState.from_dict(existing)
        ps = PhaseState(current_phase=AgentPhase.TRIAGE)
        state.metadata["phase_state"] = ps.to_dict()
        return ps

    def _get_phase_state(self, state: AgentState) -> PhaseState | None:
        """Get phase state from metadata, or None if not present."""
        data = state.metadata.get("phase_state")
        if data:
            return PhaseState.from_dict(data)
        return None

    def _save_phase_state(self, state: AgentState, phase_state: PhaseState) -> None:
        """Save phase state back to metadata."""
        state.metadata["phase_state"] = phase_state.to_dict()

    def _get_user_question(self, state: AgentState) -> str:
        """Extract the original user question from messages."""
        for msg in state.messages:
            if msg.role == MessageRole.USER:
                if isinstance(msg.content, str):
                    return msg.content
                elif isinstance(msg.content, list):
                    # Multi-part content (e.g., with file blocks)
                    text_parts = [
                        p["text"] for p in msg.content
                        if isinstance(p, dict) and p.get("type") == "text"
                    ]
                    return " ".join(text_parts)
        return ""

    def _get_tool_names(self, state: AgentState) -> list[str]:
        """Get available tool names from state."""
        if not state.tools:
            return []
        return [t["name"] for t in state.tools]

    def _get_tool_descriptions(self, state: AgentState) -> str:
        """Get formatted tool descriptions for prompts."""
        if not state.tools:
            return "No tools available."
        lines = []
        for t in state.tools:
            lines.append(f"- {t['name']}: {t.get('description', 'No description')}")
        return "\n".join(lines)

    def _format_subtask_results(self, phase_state: PhaseState) -> str:
        """Format sub-task results for use in prompts."""
        lines = []
        for st in phase_state.sub_tasks:
            status = st.status.value
            result = st.result or st.error or "No result"
            lines.append(
                f"### Sub-task: {st.description}\n"
                f"Status: {status}\n"
                f"Result:\n{result}\n"
            )
        return "\n".join(lines) if lines else "No sub-task results available."

    def _deduplicate_subtasks(
        self, sub_tasks: list[SubTask]
    ) -> tuple[list[SubTask], set[str]]:
        """Remove tool_call sub-tasks with identical (tool_name, tool_arguments).

        Returns:
            Tuple of (deduplicated list, set of removed sub-task IDs).
        """
        seen: set[str] = set()
        unique: list[SubTask] = []
        removed_ids: set[str] = set()

        for st in sub_tasks:
            if st.strategy == SubTaskStrategy.TOOL_CALL and st.tool_name:
                # Build a dedup key from tool_name + sorted arguments
                args_key = json.dumps(st.tool_arguments or {}, sort_keys=True)
                key = f"{st.tool_name}:{args_key}"
                if key in seen:
                    removed_ids.add(st.id)
                    logger.warning(
                        "Removing duplicate sub-task",
                        subtask_id=st.id,
                        tool_name=st.tool_name,
                        tool_arguments=st.tool_arguments,
                    )
                    continue
                seen.add(key)

            unique.append(st)

        if removed_ids:
            logger.info(
                "Deduplication removed sub-tasks",
                removed_count=len(removed_ids),
                remaining_count=len(unique),
            )

        return unique, removed_ids

    def _apply_plan_adjustments(
        self,
        phase_state: PhaseState,
        adjustments: list[dict[str, Any]],
    ) -> None:
        """Apply plan adjustments from reflection."""
        for adj in adjustments:
            action = adj.get("action")
            if action == "add":
                task_data = adj.get("task", {})
                task = TaskItem(
                    id=task_data.get("id", f"t-adj-{uuid.uuid4().hex[:8]}"),
                    title=task_data.get("title", "Adjusted task"),
                    phase=AgentPhase.EXECUTE,
                )
                phase_state.task_plan.append(task)
            elif action == "remove":
                task_id = adj.get("task", {}).get("id")
                if task_id:
                    phase_state.task_plan = [
                        t for t in phase_state.task_plan if t.id != task_id
                    ]

    async def _fallback_to_simple(self, state: AgentState) -> AgentState:
        """Fall back to the simple agent execution loop."""
        from .agent import AgentExecutor

        # Clear phase state to avoid re-entering multi-phase on resume
        state.metadata["phase_state"] = PhaseState(
            current_phase=AgentPhase.SIMPLE,
        ).to_dict()

        executor = AgentExecutor(
            llm_service=self.llm_service,
            tool_handler=self.tool_handler,
            snapshot_service=self.snapshot_service,
            event_callback=self.event_callback,
        )
        return await executor.execute_streaming(state)

    def _parse_json(self, text: str) -> dict[str, Any]:
        """Parse JSON from LLM response, handling common formatting issues."""
        # Strip markdown code fences if present
        cleaned = text.strip()
        if cleaned.startswith("```"):
            # Remove opening fence (possibly with language hint)
            first_newline = cleaned.find("\n")
            if first_newline != -1:
                cleaned = cleaned[first_newline + 1:]
            # Remove closing fence
            if cleaned.rstrip().endswith("```"):
                cleaned = cleaned.rstrip()[:-3]
        cleaned = cleaned.strip()

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            logger.warning("Failed to parse JSON from LLM response", text=text[:200])
            return {}

    # ------------------------------------------------------------------
    # Event emission
    # ------------------------------------------------------------------

    async def _emit_event(
        self,
        state: AgentState,
        event_type: str,
        data: dict[str, Any],
    ) -> None:
        """Emit an event through the callback."""
        if self.event_callback:
            try:
                await self.event_callback(
                    job_id=state.job_id,
                    event_type=event_type,
                    data=data,
                )
            except Exception as e:
                logger.error(
                    "Error emitting phase event",
                    event_type=event_type,
                    error=str(e),
                )

    async def _emit_phase_start(
        self,
        state: AgentState,
        phase: str,
        label: str,
        index: int,
        total: int,
    ) -> None:
        """Emit a phase_start event."""
        await self._emit_event(state, "phase_start", {
            "phase": phase,
            "phase_label": label,
            "phase_index": index,
            "total_phases": total,
        })

    async def _emit_phase_complete(
        self, state: AgentState, phase: str
    ) -> None:
        """Emit a phase_complete event."""
        await self._emit_event(state, "phase_complete", {
            "phase": phase,
        })
