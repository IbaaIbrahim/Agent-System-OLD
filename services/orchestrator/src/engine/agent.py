"""Agent execution loop."""

import asyncio
from typing import Any

from libs.common import get_logger

from ..config import get_config
from ..handlers.tool_handler import ToolHandler
from ..services.llm_service import LLMService
from ..services.snapshot_service import SnapshotService
from .state import AgentState

logger = get_logger(__name__)


class AgentExecutor:
    """Executes the agent loop with LLM and tools."""

    def __init__(
        self,
        llm_service: LLMService,
        tool_handler: ToolHandler,
        snapshot_service: SnapshotService,
        event_callback: Any = None,
    ) -> None:
        self.llm_service = llm_service
        self.tool_handler = tool_handler
        self.snapshot_service = snapshot_service
        self.event_callback = event_callback
        self.config = get_config()

    async def execute(self, state: AgentState) -> AgentState:
        """Execute the agent loop.

        Args:
            state: Initial agent state

        Returns:
            Updated agent state
        """
        logger.info(
            "Starting agent execution",
            job_id=str(state.job_id),
            provider=state.provider,
            model=state.model,
        )

        state.mark_running()
        await self._emit_event(state, "start", {"status": "running"})

        try:
            while state.iteration < self.config.max_iterations:
                state.iteration += 1

                # Periodic snapshot for crash recovery
                if state.iteration % self.config.snapshot_interval == 0:
                    await self.snapshot_service.save_snapshot(state)
                    logger.debug(
                        "Periodic snapshot saved",
                        job_id=str(state.job_id),
                        iteration=state.iteration,
                    )

                logger.debug(
                    "Agent iteration",
                    job_id=str(state.job_id),
                    iteration=state.iteration,
                )

                # Call LLM
                logger.debug(
                    "Calling llm_service.complete",
                    job_id=str(state.job_id),
                    model=state.model,
                    message_count=len(state.messages),
                    metadata=state.metadata,
                )
                response = await self.llm_service.complete(state)
                logger.debug(
                    "llm_service.complete returned",
                    job_id=str(state.job_id),
                    input_tokens=response.input_tokens,
                    output_tokens=response.output_tokens,
                    has_content=bool(response.content),
                    has_reasoning=bool(response.reasoning_content),
                    has_tool_calls=bool(response.tool_calls),
                )

                # Update token counts
                state.increment_tokens(
                    response.input_tokens,
                    response.output_tokens,
                )

                # Handle response
                if response.tool_calls:
                    # LLM wants to use tools
                    state.add_assistant_message(
                        content=response.content,
                        tool_calls=response.tool_calls,
                    )

                    await self._emit_event(state, "tool_call", {
                        "tool_calls": [
                            {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                            for tc in response.tool_calls
                        ],
                    })

                    # Check feature flag for suspend/resume
                    if self.config.enable_suspend_resume:
                        # SUSPEND: Save state and exit
                        state.mark_waiting_tool(response.tool_calls)

                        # Save snapshot before dispatching tools
                        await self.snapshot_service.save_snapshot(state)
                        await self.snapshot_service.update_job(state)

                        # Dispatch tools asynchronously (no waiting)
                        await self.tool_handler.dispatch_tools_async(state, response.tool_calls)

                        # Emit suspended event
                        await self._emit_event(state, "suspended", {
                            "pending_tools": [
                                {"id": tc.id, "name": tc.name}
                                for tc in response.tool_calls
                            ],
                            "snapshot_sequence": state.iteration,
                        })

                        logger.info(
                            "Orchestrator suspended",
                            job_id=str(state.job_id),
                            tool_count=len(response.tool_calls),
                            snapshot_sequence=state.iteration,
                        )

                        # EXIT - free CPU, wait for resume signal
                        return state
                    else:
                        # FALLBACK: Old blocking behavior for rollback safety
                        tool_results = await self.tool_handler.execute_tools(
                            state,
                            response.tool_calls,
                        )

                        # Add tool results to messages
                        for tc, result in zip(response.tool_calls, tool_results, strict=True):
                            state.add_tool_result(tc.id, result)
                            await self._emit_event(state, "tool_result", {
                                "tool_call_id": tc.id,
                                "tool_name": tc.name,
                                "result": result[:500],  # Truncate for event
                            })

                else:
                    # LLM response without tools - we're done
                    if response.reasoning_content:
                        state.reasoning_content = (state.reasoning_content or "") + response.reasoning_content

                    if response.content or response.reasoning_content:
                        state.add_assistant_message(content=response.content)
                        await self._emit_event(state, "message", {
                            "content": response.content,
                            "reasoning_content": response.reasoning_content if response.reasoning_content else None,
                        })

                    # Check if complete
                    if response.finish_reason in ("end_turn", "stop"):
                        state.mark_completed()
                        await self._emit_event(state, "complete", {
                            "total_input_tokens": state.total_input_tokens,
                            "total_output_tokens": state.total_output_tokens,
                        })
                        break

            else:
                # Max iterations reached
                state.mark_failed(
                    "Max iterations reached",
                    {"max_iterations": self.config.max_iterations},
                )
                await self._emit_event(state, "error", {
                    "error": "Max iterations reached",
                })

        except asyncio.CancelledError:
            state.mark_cancelled()
            await self._emit_event(state, "cancelled", {})
            raise

        except Exception as e:
            logger.exception(
                "Agent execution failed",
                job_id=str(state.job_id),
            )
            state.mark_failed(str(e))
            await self._emit_event(state, "error", {"error": str(e)})

        logger.info(
            "Agent execution complete",
            job_id=str(state.job_id),
            status=state.status.value,
            iterations=state.iteration,
            input_tokens=state.total_input_tokens,
            output_tokens=state.total_output_tokens,
        )

        return state

    async def execute_streaming(self, state: AgentState) -> AgentState:
        """Execute the agent loop with streaming responses.

        Args:
            state: Initial agent state

        Returns:
            Updated agent state
        """
        logger.info(
            "Starting streaming agent execution",
            job_id=str(state.job_id),
        )

        state.mark_running()
        await self._emit_event(state, "start", {"status": "running"})

        try:
            while state.iteration < self.config.max_iterations:
                state.iteration += 1

                # Periodic snapshot for crash recovery
                if state.iteration % self.config.snapshot_interval == 0:
                    await self.snapshot_service.save_snapshot(state)
                    logger.debug(
                        "Periodic snapshot saved (streaming)",
                        job_id=str(state.job_id),
                        iteration=state.iteration,
                    )

                # Stream LLM response
                content_buffer = ""
                reasoning_buffer = ""
                tool_calls = []

                logger.info(
                    "Calling llm_service.stream now",
                    job_id=str(state.job_id),
                    provider=state.provider,
                    model=state.model,
                    message_count=len(state.messages),
                )
                async for chunk in self.llm_service.stream(state):
                    if chunk:
                        # Log summary of chunk content for debugging without dumping large payloads
                        try:
                            content_len = len(chunk.content) if chunk.content else 0
                        except Exception:
                            content_len = 0
                        try:
                            reasoning_len = len(chunk.reasoning_content) if chunk.reasoning_content else 0
                        except Exception:
                            reasoning_len = 0
                        tool_calls_count = len(chunk.tool_calls) if chunk.tool_calls else 0

                        logger.debug(
                            "Stream chunk received",
                            job_id=str(state.job_id),
                            content_len=content_len,
                            reasoning_len=reasoning_len,
                            tool_calls_count=tool_calls_count,
                            is_final=bool(chunk.is_final),
                            finish_reason=chunk.finish_reason,
                        )
                        
                        if chunk.content:
                            content_buffer += chunk.content
                            await self._emit_event(state, "delta", {
                                "content": chunk.content,
                            })

                        if chunk.reasoning_content:
                            reasoning_buffer += chunk.reasoning_content
                            await self._emit_event(state, "reasoning_delta", {
                                "content": chunk.reasoning_content,
                            })

                        if chunk.tool_calls:
                            tool_calls.extend(chunk.tool_calls)

                        if chunk.is_final:
                            state.increment_tokens(
                                chunk.input_tokens or 0,
                                chunk.output_tokens or 0,
                            )

                            if tool_calls:
                                # Handle tool calls
                                state.add_assistant_message(
                                    content=content_buffer if content_buffer else None,
                                    tool_calls=tool_calls,
                                )

                                for tc in tool_calls:
                                    await self._emit_event(state, "tool_call", {
                                        "id": tc.id,
                                        "name": tc.name,
                                        "arguments": tc.arguments,
                                    })

                                # Check feature flag for suspend/resume
                                if self.config.enable_suspend_resume:
                                    # SUSPEND: Save state and exit
                                    state.mark_waiting_tool(tool_calls)

                                    # Save snapshot before dispatching tools
                                    await self.snapshot_service.save_snapshot(state)
                                    await self.snapshot_service.update_job(state)

                                    # Dispatch tools asynchronously (no waiting)
                                    await self.tool_handler.dispatch_tools_async(state, tool_calls)

                                    # Emit suspended event
                                    await self._emit_event(state, "suspended", {
                                        "pending_tools": [
                                            {"id": tc.id, "name": tc.name}
                                            for tc in tool_calls
                                        ],
                                        "snapshot_sequence": state.iteration,
                                    })

                                    logger.info(
                                        "Orchestrator suspended (streaming)",
                                        job_id=str(state.job_id),
                                        tool_count=len(tool_calls),
                                        snapshot_sequence=state.iteration,
                                    )

                                    # EXIT - free CPU, wait for resume signal
                                    return state
                                else:
                                    # FALLBACK: Old blocking behavior
                                    results = await self.tool_handler.execute_tools(
                                        state,
                                        tool_calls,
                                    )

                                    for tc, result in zip(tool_calls, results, strict=True):
                                        state.add_tool_result(tc.id, result)
                                        await self._emit_event(state, "tool_result", {
                                            "tool_call_id": tc.id,
                                            "result": result[:500],
                                        })

                            elif content_buffer or reasoning_buffer:
                                if reasoning_buffer:
                                    state.reasoning_content = (state.reasoning_content or "") + reasoning_buffer
                                
                                state.add_assistant_message(content=content_buffer if content_buffer else None)
                                
                                if not content_buffer and reasoning_buffer:
                                    # If only reasoning, emit one final message event with it
                                    await self._emit_event(state, "message", {
                                        "content": None,
                                        "reasoning_content": reasoning_buffer,
                                    })

                            if chunk.finish_reason in ("end_turn", "stop") and not tool_calls:
                                state.mark_completed()
                                await self._emit_event(state, "complete", {
                                    "total_input_tokens": state.total_input_tokens,
                                    "total_output_tokens": state.total_output_tokens,
                                    "reasoning_content": state.reasoning_content,
                                })
                                return state

                            break

            # Max iterations
            state.mark_failed("Max iterations reached")
            await self._emit_event(state, "error", {"error": "Max iterations reached"})

        except asyncio.CancelledError:
            state.mark_cancelled()
            await self._emit_event(state, "cancelled", {})
            raise

        except Exception as e:
            logger.exception("Streaming agent execution failed", job_id=str(state.job_id))
            state.mark_failed(str(e))
            await self._emit_event(state, "error", {"error": str(e)})

        return state

    async def resume_from_snapshot(
        self,
        state: AgentState,
        tool_results: dict[str, str],
    ) -> AgentState:
        """Resume execution after tool completion.

        Called by ResumeHandler when all pending tools are complete.

        Args:
            state: Agent state loaded from snapshot
            tool_results: Map of tool_call_id -> result string

        Returns:
            Updated agent state after resuming execution
        """
        logger.info(
            "Resuming agent execution",
            job_id=str(state.job_id),
            iteration=state.iteration,
            pending_tool_count=len(state.pending_tool_calls),
        )

        # Inject tool results into message history
        for tc in state.pending_tool_calls:
            result = tool_results.get(tc.id)

            if result is None:
                # This shouldn't happen if resume handler checks properly
                logger.error(
                    "Tool result missing during resume",
                    job_id=str(state.job_id),
                    tool_call_id=tc.id,
                )
                result = "Error: Tool result not available"

            # Add to message history
            state.add_tool_result(tc.id, result)

            # Emit event for client
            await self._emit_event(state, "tool_result", {
                "tool_call_id": tc.id,
                "tool_name": tc.name,
                "result": result[:500],  # Truncate for event
            })

        # Clear pending tools and mark as running
        state.pending_tool_calls = []
        state.mark_running()

        # Continue the main execution loop
        # Determine which method to use based on state metadata
        if state.metadata.get("streaming", True):
            return await self.execute_streaming(state)
        else:
            return await self.execute(state)

    async def _emit_event(
        self,
        state: AgentState,
        event_type: str,
        data: dict[str, Any],
    ) -> None:
        """Emit an event through the callback if configured."""
        if self.event_callback:
            try:
                await self.event_callback(
                    job_id=state.job_id,
                    event_type=event_type,
                    data=data,
                )
            except Exception as e:
                logger.error(
                    "Error emitting event",
                    event_type=event_type,
                    error=str(e),
                )
