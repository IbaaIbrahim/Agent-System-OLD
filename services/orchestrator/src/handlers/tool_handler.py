"""Tool invocation handler."""

import asyncio
import json
from datetime import UTC
from typing import Any

from libs.common import get_logger
from libs.llm import ToolCall
from libs.messaging.kafka import get_producer

from ..config import get_config
from ..engine.state import AgentState

logger = get_logger(__name__)


class ToolHandler:
    """Handles tool invocation for agents."""

    def __init__(self) -> None:
        self.config = get_config()
        self._pending_results: dict[str, asyncio.Future] = {}

    def _get_tool_category(
        self,
        tool_name: str,
        tools: list[dict[str, Any]] | None,
    ) -> str:
        """Get the category of a tool from the tools list.

        Args:
            tool_name: Name of the tool
            tools: List of tool definitions

        Returns:
            Tool category (builtin, configurable, or client_side)
        """
        if not tools:
            return "builtin"

        for tool in tools:
            if tool.get("name") == tool_name:
                return tool.get("category", "builtin")

        return "builtin"

    async def _emit_client_tool_event(
        self,
        state: AgentState,
        tool_call: ToolCall,
    ) -> None:
        """Emit SSE event for client-side tool execution.

        The frontend will receive this event, execute the tool locally,
        and POST the result back to the API.

        Args:
            state: Current agent state
            tool_call: Tool call to emit
        """
        from libs.messaging.redis import get_redis_client

        redis = await get_redis_client()

        event_data = {
            "type": "client_tool_call",
            "job_id": str(state.job_id),
            "tool_call_id": tool_call.id,
            "tool_name": tool_call.name,
            "arguments": tool_call.arguments,
        }

        # Publish to job's event channel
        channel = f"events:{state.job_id}"
        await redis.publish(channel, json.dumps(event_data))

        logger.info(
            "Client-side tool event emitted",
            job_id=str(state.job_id),
            tool_name=tool_call.name,
            tool_id=tool_call.id,
        )

    async def execute_tools(
        self,
        state: AgentState,
        tool_calls: list[ToolCall],
    ) -> list[str]:
        """Execute multiple tool calls.

        Args:
            state: Current agent state
            tool_calls: List of tool calls to execute

        Returns:
            List of tool results in same order as calls
        """
        results = []

        for tc in tool_calls:
            try:
                result = await self._execute_single_tool(state, tc)
                results.append(result)
            except TimeoutError:
                results.append(f"Error: Tool '{tc.name}' timed out")
            except Exception as e:
                logger.error(
                    "Tool execution failed",
                    tool_name=tc.name,
                    error=str(e),
                )
                results.append(f"Error: {str(e)}")

        return results

    async def dispatch_tools_async(
        self,
        state: AgentState,
        tool_calls: list[ToolCall],
    ) -> None:
        """Dispatch tools to Kafka without waiting for results.

        Used in suspend/resume mode. Tools are dispatched and the
        orchestrator exits. Resume signals will trigger continuation.

        Args:
            state: Current agent state
            tool_calls: List of tool calls to dispatch
        """
        from libs.messaging.redis import get_redis_client

        producer = await get_producer()
        redis = await get_redis_client()

        for tc in tool_calls:
            logger.info(
                "Dispatching tool (async)",
                job_id=str(state.job_id),
                tool_name=tc.name,
                tool_id=tc.id,
            )

            # Check for built-in simple tools
            if tc.name == "get_current_time":
                # Store result immediately in Redis for resume handler
                from datetime import datetime
                result = datetime.now(UTC).isoformat()

                result_key = f"tool_result:{tc.id}"
                await redis.set(result_key, result, ex=300)

                # Publish resume signal immediately
                await producer.send(
                    topic=self.config.resume_topic,
                    message={
                        "job_id": str(state.job_id),
                        "tool_call_id": tc.id,
                        "snapshot_sequence": state.iteration,
                        "status": "completed",
                        "tool_name": tc.name,
                    },
                    key=str(state.job_id),
                )

                logger.info(
                    "Built-in tool executed and resume signal sent",
                    job_id=str(state.job_id),
                    tool_name=tc.name,
                )
            else:
                # Check if this is a client-side tool
                category = self._get_tool_category(tc.name, state.tools)

                if category == "client_side":
                    # Emit SSE event for frontend to execute
                    await self._emit_client_tool_event(state, tc)
                    logger.info(
                        "Client-side tool event emitted, waiting for frontend",
                        job_id=str(state.job_id),
                        tool_name=tc.name,
                    )
                else:
                    # Dispatch to tool workers (builtin or configurable)
                    message = {
                        "tool_call_id": tc.id,
                        "job_id": str(state.job_id),
                        "tenant_id": str(state.tenant_id),
                        "tool_name": tc.name,
                        "arguments": tc.arguments,
                        "snapshot_sequence": state.iteration,
                    }

                    await producer.send(
                        topic=self.config.tools_topic,
                        message=message,
                        key=str(state.tenant_id),
                        headers={
                            "job_id": str(state.job_id),
                            "tool_call_id": tc.id,
                        },
                    )

                    logger.debug(
                        "Tool dispatched to worker queue",
                        job_id=str(state.job_id),
                        tool_name=tc.name,
                    )

    async def _execute_single_tool(
        self,
        state: AgentState,
        tool_call: ToolCall,
    ) -> str:
        """Execute a single tool call.

        For simple built-in tools, execute directly.
        For complex tools, dispatch to tool workers via Kafka.

        Args:
            state: Current agent state
            tool_call: Tool to execute

        Returns:
            Tool execution result
        """
        logger.info(
            "Executing tool",
            job_id=str(state.job_id),
            tool_name=tool_call.name,
            tool_id=tool_call.id,
        )

        # Check for built-in simple tools
        if tool_call.name == "get_current_time":
            from datetime import datetime
            return datetime.now(UTC).isoformat()

        # For other tools, dispatch to tool workers
        return await self._dispatch_to_worker(state, tool_call)

    async def _dispatch_to_worker(
        self,
        state: AgentState,
        tool_call: ToolCall,
    ) -> str:
        """Dispatch tool execution to worker via Kafka.

        Args:
            state: Agent state
            tool_call: Tool to execute

        Returns:
            Tool result from worker
        """
        producer = await get_producer()

        # Create message for tool worker
        message = {
            "tool_call_id": tool_call.id,
            "job_id": str(state.job_id),
            "tenant_id": str(state.tenant_id),
            "tool_name": tool_call.name,
            "arguments": tool_call.arguments,
            "snapshot_sequence": state.iteration,  # For resume handler
        }

        # Send to tools topic
        await producer.send(
            topic=self.config.tools_topic,
            message=message,
            key=str(state.tenant_id),
            headers={
                "job_id": str(state.job_id),
                "tool_call_id": tool_call.id,
            },
        )

        # Wait for result (with timeout)
        result = await self._wait_for_result(
            tool_call.id,
            timeout=self.config.tool_timeout_seconds,
        )

        return result

    async def _wait_for_result(
        self,
        tool_call_id: str,
        timeout: int,
    ) -> str:
        """Wait for tool result from worker.

        In a production system, this would use a more sophisticated
        mechanism like Redis pub/sub or a callback endpoint.

        Args:
            tool_call_id: Tool call ID to wait for
            timeout: Timeout in seconds

        Returns:
            Tool result
        """
        from libs.messaging.redis import get_redis_client

        redis = await get_redis_client()
        result_key = f"tool_result:{tool_call_id}"

        # Poll for result with timeout
        start_time = asyncio.get_event_loop().time()

        while (asyncio.get_event_loop().time() - start_time) < timeout:
            result = await redis.get(result_key)
            if result:
                # Clean up
                await redis.delete(result_key)
                return result

            await asyncio.sleep(0.1)

        raise TimeoutError(f"Tool {tool_call_id} timed out after {timeout}s")

    async def receive_result(
        self,
        tool_call_id: str,
        result: str,
    ) -> None:
        """Receive a tool result from a worker.

        Called by the tool result consumer.

        Args:
            tool_call_id: Tool call this result is for
            result: Tool execution result
        """
        from libs.messaging.redis import get_redis_client

        redis = await get_redis_client()
        result_key = f"tool_result:{tool_call_id}"

        # Store result with expiration
        await redis.set(result_key, result, ex=300)  # 5 minute expiry

        logger.debug(
            "Tool result received",
            tool_call_id=tool_call_id,
            result_length=len(result),
        )
