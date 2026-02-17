"""Agent execution loop."""

import asyncio
import re
from typing import Any

from libs.common import get_logger
from libs.common.tool_catalog import get_tool_metadata

from ..config import get_config
from ..handlers.tool_handler import ToolHandler
from ..prompts.effort_levels import get_effort_config
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

    def _resolve_max_iterations(self, state: AgentState) -> int:
        """Resolve max iterations based on effort level in metadata.

        Falls back to config default if no effort level is specified.
        """
        effort_level = state.metadata.get("effort_level") if state.metadata else None
        if effort_level:
            return get_effort_config(effort_level).max_iterations
        return self.config.max_iterations

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

        max_iterations = self._resolve_max_iterations(state)

        state.mark_running()
        await self._emit_event(state, "start", {"status": "running"})

        try:
            while state.iteration < max_iterations:
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

                    # Only emit tool_call events for tools with emit_events=True
                    visible_tool_calls = [
                        {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                        for tc in response.tool_calls
                        if self._should_emit_tool_event(tc.name)
                    ]
                    if visible_tool_calls:
                        # Include content so "I'll search for that" type messages are preserved
                        await self._emit_event(state, "tool_call", {
                            "tool_calls": visible_tool_calls,
                            "content": response.content,
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
                            if self._should_emit_tool_event(tc.name):
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

                    # If no tools, this turn is done regardless of finish_reason
                    state.mark_completed()
                    await self._emit_event(state, "complete", {
                        "total_input_tokens": state.total_input_tokens,
                        "total_output_tokens": state.total_output_tokens,
                        "finish_reason": response.finish_reason,
                    })
                    break

            else:
                # Max iterations reached
                state.mark_failed(
                    "Max iterations reached",
                    {"max_iterations": max_iterations},
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
            "✅ Starting streaming agent execution",
            job_id=str(state.job_id),
        )

        max_iterations = self._resolve_max_iterations(state)

        state.mark_running()
        await self._emit_event(state, "start", {"status": "running"})

        try:
            while state.iteration < max_iterations:
                state.iteration += 1

                # Periodic snapshot for crash recovery
                if state.iteration % self.config.snapshot_interval == 0:
                    await self.snapshot_service.save_snapshot(state)
                    logger.debug(
                        "💾 Periodic snapshot saved (streaming)",
                        job_id=str(state.job_id),
                        iteration=state.iteration,
                    )

                # Stream LLM response
                content_buffer = ""
                reasoning_buffer = ""
                tool_calls = []
                
                # Stateful parser for <thinking></thinking> tags across chunks
                thinking_buffer = ""  # Accumulates content inside thinking tags
                in_thinking_tag = False  # Whether we're currently inside a thinking tag
                pending_buffer = ""  # Buffer for content that might contain partial tags

                logger.info(
                    "🏃‍♂️ Calling llm_service.stream now",
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
                            "🔄 Stream chunk received",
                            job_id=str(state.job_id),
                            content_len=content_len,
                            reasoning_len=reasoning_len,
                            tool_calls_count=tool_calls_count,
                            is_final=bool(chunk.is_final),
                            finish_reason=chunk.finish_reason,
                        )
                        
                        if chunk.content:
                            # Process content chunk by chunk, handling partial <thinking> tags
                            content = chunk.content
                            
                            # Combine pending buffer with new content
                            combined = pending_buffer + content
                            pending_buffer = ""
                            
                            remaining = combined
                            
                            while remaining:
                                if not in_thinking_tag:
                                    # Look for opening <thinking> tag
                                    open_tag_pos = remaining.find("<thinking>")
                                    if open_tag_pos != -1:
                                        # Found complete opening tag - emit content before it
                                        before_tag = remaining[:open_tag_pos]
                                        if before_tag:
                                            content_buffer += before_tag
                                            await self._emit_event(state, "delta", {
                                                "content": before_tag,
                                            })
                                        
                                        # Start thinking mode
                                        in_thinking_tag = True
                                        remaining = remaining[open_tag_pos + len("<thinking>"):]
                                    else:
                                        # Check for partial opening tag at end of content
                                        # Look for "<thinking" or any prefix that could become "<thinking>"
                                        # We need to check if content ends with a prefix of "<thinking>"
                                        OPEN_TAG = "<thinking>"
                                        OPEN_TAG_LEN = len(OPEN_TAG)
                                        
                                        # Check if content ends with a prefix of the opening tag
                                        # Check from longest to shortest to catch longest match first
                                        # (e.g., "<thinking", "<thinkin", "<thinki", "<think", "<thin", etc.)
                                        partial_found = False
                                        for i in range(OPEN_TAG_LEN, 0, -1):
                                            tag_prefix = OPEN_TAG[:i]
                                            if remaining.endswith(tag_prefix):
                                                # Found a partial tag at the end - buffer it
                                                before_partial = remaining[:-i]
                                                if before_partial:
                                                    content_buffer += before_partial
                                                    await self._emit_event(state, "delta", {
                                                        "content": before_partial,
                                                    })
                                                pending_buffer = remaining[-i:]
                                                remaining = ""
                                                partial_found = True
                                                break
                                        
                                        if not partial_found:
                                            # No partial tag found - emit all content
                                            content_buffer += remaining
                                            await self._emit_event(state, "delta", {
                                                "content": remaining,
                                            })
                                            remaining = ""
                                else:
                                    # We're inside a thinking tag - look for closing </thinking>
                                    close_tag_pos = remaining.find("</thinking>")
                                    if close_tag_pos != -1:
                                        # Found closing tag - emit final thinking content
                                        thinking_content = remaining[:close_tag_pos]
                                        if thinking_content:
                                            thinking_buffer += thinking_content
                                            reasoning_buffer += thinking_content
                                            await self._emit_event(state, "reasoning_delta", {
                                                "content": thinking_content,
                                            })
                                            thinking_buffer = ""
                                        
                                        # Exit thinking mode
                                        in_thinking_tag = False
                                        remaining = remaining[close_tag_pos + len("</thinking>"):]
                                    else:
                                        # Check for partial closing tag at end
                                        # Look for "</thinking" or any prefix that could become "</thinking>"
                                        CLOSE_TAG = "</thinking>"
                                        CLOSE_TAG_LEN = len(CLOSE_TAG)
                                        
                                        # Check if content ends with a prefix of the closing tag
                                        # Check from longest to shortest to catch longest match first
                                        partial_found = False
                                        for i in range(CLOSE_TAG_LEN, 0, -1):
                                            tag_prefix = CLOSE_TAG[:i]
                                            if remaining.endswith(tag_prefix):
                                                # Found a partial closing tag at the end - emit thinking before it, buffer the rest
                                                thinking_content = remaining[:-i]
                                                if thinking_content:
                                                    thinking_buffer += thinking_content
                                                    reasoning_buffer += thinking_content
                                                    await self._emit_event(state, "reasoning_delta", {
                                                        "content": thinking_content,
                                                    })
                                                pending_buffer = remaining[-i:]
                                                remaining = ""
                                                partial_found = True
                                                break
                                        
                                        if not partial_found:
                                            # No partial tag found - stream thinking content incrementally
                                            if remaining:
                                                thinking_buffer += remaining
                                                reasoning_buffer += remaining
                                                await self._emit_event(state, "reasoning_delta", {
                                                    "content": remaining,
                                                })
                                            remaining = ""

                        if chunk.reasoning_content:
                            reasoning_buffer += chunk.reasoning_content
                            await self._emit_event(state, "reasoning_delta", {
                                "content": chunk.reasoning_content,
                            })

                        if chunk.tool_calls:
                            tool_calls.extend(chunk.tool_calls)

                        if chunk.is_final:
                            # Handle any remaining buffered content
                            if pending_buffer:
                                if in_thinking_tag:
                                    # Stream remaining thinking content
                                    reasoning_buffer += pending_buffer
                                    await self._emit_event(state, "reasoning_delta", {
                                        "content": pending_buffer,
                                    })
                                else:
                                    # Stream remaining regular content
                                    content_buffer += pending_buffer
                                    await self._emit_event(state, "delta", {
                                        "content": pending_buffer,
                                    })
                                pending_buffer = ""
                            
                            # Handle any remaining thinking content (in case stream ends without closing tag)
                            if in_thinking_tag and thinking_buffer:
                                reasoning_buffer += thinking_buffer
                                await self._emit_event(state, "reasoning_delta", {
                                    "content": thinking_buffer,
                                })
                                thinking_buffer = ""
                            
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

                                # Emit single tool_call event with all visible tools
                                visible_tool_calls = [
                                    {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                                    for tc in tool_calls
                                    if self._should_emit_tool_event(tc.name)
                                ]
                                if visible_tool_calls:
                                    # Include content so "I'll search for that" type messages are preserved
                                    await self._emit_event(state, "tool_call", {
                                        "tool_calls": visible_tool_calls,
                                        "content": content_buffer if content_buffer else None,
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
                                        if self._should_emit_tool_event(tc.name):
                                            await self._emit_event(state, "tool_result", {
                                                "tool_call_id": tc.id,
                                                "tool_name": tc.name,
                                                "result": result[:500],
                                            })

                            elif content_buffer or reasoning_buffer:
                                if reasoning_buffer:
                                    state.reasoning_content = (state.reasoning_content or "") + reasoning_buffer

                                state.add_assistant_message(content=content_buffer if content_buffer else None)

                                # Emit final message event with full content for archival
                                await self._emit_event(state, "message", {
                                    "content": content_buffer if content_buffer else None,
                                    "reasoning_content": reasoning_buffer if reasoning_buffer else None,
                                })

                            # If no tools were found in the stream, this turn is done regardless of finish_reason
                            if not tool_calls:
                                state.mark_completed()
                                await self._emit_event(state, "complete", {
                                    "total_input_tokens": state.total_input_tokens,
                                    "total_output_tokens": state.total_output_tokens,
                                    "reasoning_content": state.reasoning_content,
                                    "finish_reason": chunk.finish_reason,
                                })
                                return state

                            break

            # Max iterations
            state.mark_failed("Max iterations reached", {"max_iterations": max_iterations})
            await self._emit_event(state, "error", {"error": "Max iterations reached"})

        except asyncio.CancelledError:
            state.mark_cancelled()
            await self._emit_event(state, "cancelled", {})
            raise

        except Exception as e:
            logger.exception("💀 Streaming agent execution failed", job_id=str(state.job_id))
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

            # Emit event for client (unless tool has emit_events=False)
            if self._should_emit_tool_event(tc.name):
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

    def _should_emit_tool_event(self, tool_name: str) -> bool:
        """Check if a tool's events should be emitted to the client.

        Tools with emit_events=False in the catalog will have their
        tool_call and tool_result SSE events suppressed.
        """
        metadata = get_tool_metadata(tool_name)
        if metadata is None:
            return True
        return metadata.emit_events

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
