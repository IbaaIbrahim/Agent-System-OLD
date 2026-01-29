"""Agent execution loop."""

import asyncio
from typing import Any
from uuid import UUID

from libs.common import get_logger
from libs.llm import LLMProvider, ToolDefinition

from ..config import get_config
from .state import AgentState, AgentStatus
from ..services.llm_service import LLMService
from ..handlers.tool_handler import ToolHandler

logger = get_logger(__name__)


class AgentExecutor:
    """Executes the agent loop with LLM and tools."""

    def __init__(
        self,
        llm_service: LLMService,
        tool_handler: ToolHandler,
        event_callback: Any = None,
    ) -> None:
        self.llm_service = llm_service
        self.tool_handler = tool_handler
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

                logger.debug(
                    "Agent iteration",
                    job_id=str(state.job_id),
                    iteration=state.iteration,
                )

                # Call LLM
                response = await self.llm_service.complete(state)

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

                    # Execute tools
                    tool_results = await self.tool_handler.execute_tools(
                        state,
                        response.tool_calls,
                    )

                    # Add tool results to messages
                    for tc, result in zip(response.tool_calls, tool_results):
                        state.add_tool_result(tc.id, result)
                        await self._emit_event(state, "tool_result", {
                            "tool_call_id": tc.id,
                            "tool_name": tc.name,
                            "result": result[:500],  # Truncate for event
                        })

                else:
                    # LLM response without tools - we're done
                    if response.content:
                        state.add_assistant_message(content=response.content)
                        await self._emit_event(state, "message", {
                            "content": response.content,
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

                # Stream LLM response
                content_buffer = ""
                tool_calls = []

                async for chunk in self.llm_service.stream(state):
                    if chunk.content:
                        content_buffer += chunk.content
                        await self._emit_event(state, "delta", {
                            "content": chunk.content,
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

                            # Execute tools
                            results = await self.tool_handler.execute_tools(
                                state,
                                tool_calls,
                            )

                            for tc, result in zip(tool_calls, results):
                                state.add_tool_result(tc.id, result)
                                await self._emit_event(state, "tool_result", {
                                    "tool_call_id": tc.id,
                                    "result": result[:500],
                                })

                        elif content_buffer:
                            state.add_assistant_message(content=content_buffer)

                        if chunk.finish_reason in ("end_turn", "stop") and not tool_calls:
                            state.mark_completed()
                            await self._emit_event(state, "complete", {
                                "total_input_tokens": state.total_input_tokens,
                                "total_output_tokens": state.total_output_tokens,
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
