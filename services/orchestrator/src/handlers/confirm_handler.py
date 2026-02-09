"""Handler for user confirmation responses for CONFIRM_REQUIRED tools."""

import json
from typing import Any
from uuid import UUID

from libs.common import get_logger
from libs.common.tool_catalog import get_tool_metadata
from libs.messaging.kafka import get_producer
from libs.messaging.redis import get_redis_client

from ..config import get_config
from ..engine.agent import AgentExecutor
from ..engine.state import AgentStatus
from ..services.event_publisher import EventPublisher
from ..services.llm_service import LLMService
from ..services.snapshot_service import SnapshotService
from ..services.state_lock import DistributedStateLock
from .tool_handler import ToolHandler

logger = get_logger(__name__)


class ConfirmHandler:
    """Handles user confirm/reject responses for CONFIRM_REQUIRED tools.

    Listens on agent.confirm topic. When a user responds:
    1. Acquires distributed lock for the job
    2. Loads snapshot from PostgreSQL
    3. If confirmed: dispatches tool to workers
    4. If rejected: injects rejection result and resumes agent
    """

    def __init__(
        self,
        snapshot_service: SnapshotService,
        llm_service: LLMService,
        tool_handler: ToolHandler,
    ) -> None:
        self.snapshot_service = snapshot_service
        self.llm_service = llm_service
        self.tool_handler = tool_handler
        self.config = get_config()
        self.lock = DistributedStateLock(ttl=self.config.job_lock_ttl_seconds)
        self.event_publisher = EventPublisher()

    async def handle_confirmation(
        self,
        message: dict[str, Any],
        headers: dict[str, str],
    ) -> None:
        """Handle a user's confirm/reject response.

        Args:
            message: Confirmation payload
            headers: Kafka message headers
        """
        job_id = UUID(message["job_id"])
        tool_call_id = message["tool_call_id"]
        confirmed = message["confirmed"]

        logger.info(
            "Confirmation received",
            job_id=str(job_id),
            tool_call_id=tool_call_id,
            confirmed=confirmed,
        )

        # Acquire lock - prevents duplicate processing
        if not await self.lock.acquire(job_id, owner="confirm_handler"):
            logger.debug(
                "Job already being processed, skipping",
                job_id=str(job_id),
            )
            return

        try:
            # Load latest snapshot
            state = await self.snapshot_service.load_latest_snapshot(job_id)

            if state is None:
                logger.warning(
                    "No snapshot found for job",
                    job_id=str(job_id),
                )
                return

            # Verify state is waiting for tools
            if state.status != AgentStatus.WAITING_TOOL:
                logger.debug(
                    "Job not in WAITING_TOOL state, skipping",
                    job_id=str(job_id),
                    status=state.status.value,
                )
                return

            # Find the pending tool call
            tool_call = self._find_pending_tool_call(state, tool_call_id)

            if tool_call is None:
                logger.warning(
                    "Tool call not found in pending tools",
                    job_id=str(job_id),
                    tool_call_id=tool_call_id,
                )
                return

            # Emit confirm_response event to client
            await self._emit_confirm_response(job_id, tool_call_id, confirmed)

            if confirmed:
                # User approved - check if tool needs client-side execution
                tool_metadata = get_tool_metadata(tool_call.name)

                if tool_metadata and tool_metadata.client_side_execution:
                    # Emit client_tool_call event for frontend execution
                    await self._emit_client_tool_call(state, tool_call)
                    logger.info(
                        "Client-side tool call emitted after user confirmation",
                        job_id=str(job_id),
                        tool_call_id=tool_call_id,
                    )
                else:
                    # Dispatch to backend workers
                    await self._dispatch_to_workers(state, tool_call)
                    logger.info(
                        "Tool dispatched after user confirmation",
                        job_id=str(job_id),
                        tool_call_id=tool_call_id,
                    )
            else:
                # User rejected - inject rejection result and resume
                await self._handle_rejection(state, tool_call_id)
                logger.info(
                    "Tool rejected by user, resuming agent",
                    job_id=str(job_id),
                    tool_call_id=tool_call_id,
                )

        except Exception as e:
            logger.exception(
                "Confirmation handling failed",
                job_id=str(job_id),
                error=str(e),
            )
            # Publish error event
            await self._publish_event(
                job_id=job_id,
                event_type="error",
                data={"error": f"Confirmation handling failed: {str(e)}"},
            )
            raise
        finally:
            await self.lock.release(job_id)

    def _find_pending_tool_call(self, state, tool_call_id: str):
        """Find a tool call in the pending tools list.

        Args:
            state: Current agent state
            tool_call_id: ID of the tool call to find

        Returns:
            ToolCall if found, None otherwise
        """
        for tc in state.pending_tool_calls:
            if tc.id == tool_call_id:
                return tc
        return None

    async def _emit_confirm_response(
        self,
        job_id: UUID,
        tool_call_id: str,
        confirmed: bool,
    ) -> None:
        """Emit confirm_response event to client.

        Args:
            job_id: Job ID
            tool_call_id: Tool call ID
            confirmed: Whether the user confirmed or rejected
        """
        redis = await get_redis_client()

        event_data = {
            "type": "confirm_response",
            "job_id": str(job_id),
            "tool_call_id": tool_call_id,
            "confirmed": confirmed,
        }

        channel = f"job:{job_id}"
        await redis.client.publish(channel, json.dumps(event_data))

        logger.debug(
            "Confirm response event emitted",
            job_id=str(job_id),
            tool_call_id=tool_call_id,
            confirmed=confirmed,
        )

    async def _emit_client_tool_call(self, state, tool_call) -> None:
        """Emit client_tool_call event for client-side execution.

        Args:
            state: Current agent state
            tool_call: Tool call to execute on client
        """
        redis = await get_redis_client()

        event_data = {
            "type": "client_tool_call",
            "tool_call_id": tool_call.id,
            "tool_name": tool_call.name,
            "arguments": tool_call.arguments,
        }

        # Publish to job's event channel (pub/sub uses job: prefix)
        channel = f"job:{state.job_id}"
        await redis.client.publish(channel, json.dumps(event_data))

        logger.debug(
            "Client tool call event emitted",
            job_id=str(state.job_id),
            tool_call_id=tool_call.id,
            tool_name=tool_call.name,
        )

    async def _dispatch_to_workers(self, state, tool_call) -> None:
        """Dispatch the confirmed tool to workers.

        Args:
            state: Current agent state
            tool_call: Tool call to dispatch
        """
        producer = await get_producer()

        message = {
            "tool_call_id": tool_call.id,
            "job_id": str(state.job_id),
            "tenant_id": str(state.tenant_id),
            "tool_name": tool_call.name,
            "arguments": tool_call.arguments,
            "snapshot_sequence": state.iteration,
            "plan_features": getattr(state, "plan_features", []),
            "enabled_tools": getattr(state, "enabled_tools", []),
        }

        await producer.send(
            topic=self.config.tools_topic,
            message=message,
            key=str(state.tenant_id),
            headers={
                "job_id": str(state.job_id),
                "tool_call_id": tool_call.id,
            },
        )

    async def _handle_rejection(self, state, tool_call_id: str) -> None:
        """Handle tool rejection - inject result and resume agent.

        Args:
            state: Current agent state
            tool_call_id: ID of the rejected tool call
        """
        redis = await get_redis_client()

        # Create rejection result
        result = json.dumps({
            "error": "user_rejected",
            "message": "User cancelled this action",
            "success": False,
        })

        # Store result in Redis for resume handler
        result_key = f"tool_result:{tool_call_id}"
        await redis.set(result_key, result, ex=300)  # 5 minute expiry

        # Publish resume signal to trigger job continuation
        producer = await get_producer()

        await producer.send(
            topic=self.config.resume_topic,
            message={
                "job_id": str(state.job_id),
                "tool_call_id": tool_call_id,
                "snapshot_sequence": state.iteration,
                "status": "rejected",
                "tool_name": "user_rejected",
            },
            key=str(state.job_id),
            headers={
                "tool_call_id": tool_call_id,
                "job_id": str(state.job_id),
            },
        )

    async def _publish_event(
        self,
        job_id: UUID,
        event_type: str,
        data: dict[str, Any],
    ) -> None:
        """Publish an event to Redis for SSE streaming.

        Args:
            job_id: Job ID
            event_type: Event type
            data: Event payload
        """
        await self.event_publisher.publish_event(
            job_id=job_id,
            event_type=event_type,
            data=data,
        )
