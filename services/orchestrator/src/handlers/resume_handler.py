"""Resume handler for job continuation after tool completion."""

from typing import Any
from uuid import UUID

from libs.common import get_logger
from libs.llm import ToolCall
from libs.messaging.redis import RedisPubSub, RedisStreams, get_redis_client

from ..config import get_config
from ..engine.agent import AgentExecutor
from ..engine.state import AgentState, AgentStatus
from ..services.llm_service import LLMService
from ..services.snapshot_service import SnapshotService
from ..services.state_lock import DistributedStateLock
from ..services.event_publisher import EventPublisher
from .tool_handler import ToolHandler

logger = get_logger(__name__)


class ResumeHandler:
    """Handles job resumption after tool completion.

    Listens on agent.job-resume topic. When a tool completes:
    1. Acquires distributed lock for the job
    2. Loads snapshot from PostgreSQL
    3. Checks if ALL pending tools are complete
    4. Fetches tool results from Redis
    5. Resumes execution via AgentExecutor
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

    async def handle_resume(
        self,
        message: dict[str, Any],
        headers: dict[str, str],
    ) -> None:
        """Handle a resume signal from a tool worker.

        Args:
            message: Resume signal payload
            headers: Kafka message headers
        """
        job_id = UUID(message["job_id"])
        tool_call_id = message["tool_call_id"]
        snapshot_seq = message.get("snapshot_sequence", 0)

        logger.info(
            "Resume signal received",
            job_id=str(job_id),
            tool_call_id=tool_call_id,
            snapshot_sequence=snapshot_seq,
        )

        # Acquire lock - prevents duplicate processing
        if not await self.lock.acquire(job_id, owner="resume_handler"):
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

            # Check if ALL pending tools are complete
            tool_results = await self._fetch_tool_results(state.pending_tool_calls)

            missing_tools = [
                tc.id for tc in state.pending_tool_calls
                if tc.id not in tool_results or tool_results[tc.id] is None
            ]

            if missing_tools:
                logger.debug(
                    "Not all tools complete yet",
                    job_id=str(job_id),
                    pending_count=len(missing_tools),
                    missing_tool_ids=missing_tools,
                )
                # Release lock and wait for other tools to complete
                return

            logger.info(
                "All tools complete, resuming execution",
                job_id=str(job_id),
                tool_count=len(state.pending_tool_calls),
            )

            # Create executor with event callback
            executor = AgentExecutor(
                llm_service=self.llm_service,
                tool_handler=self.tool_handler,
                snapshot_service=self.snapshot_service,
                event_callback=self._publish_event,
            )

            # Resume execution from snapshot
            state = await executor.resume_from_snapshot(state, tool_results)

            # Save final state
            await self.snapshot_service.save_snapshot(state)
            await self.snapshot_service.update_job(state)

            logger.info(
                "Job resumed successfully",
                job_id=str(job_id),
                final_status=state.status.value,
            )

        except Exception as e:
            logger.exception(
                "Resume handling failed",
                job_id=str(job_id),
                error=str(e),
            )
            # Publish error event
            await self._publish_event(
                job_id=job_id,
                event_type="error",
                data={"error": f"Resume failed: {str(e)}"},
            )
            raise
        finally:
            await self.lock.release(job_id)

    async def _fetch_tool_results(
        self,
        tool_calls: list[ToolCall],
    ) -> dict[str, str | None]:
        """Fetch results for all pending tool calls from Redis.

        Args:
            tool_calls: List of pending tool calls

        Returns:
            Map of tool_call_id to result (None if not yet available)
        """
        redis = await get_redis_client()
        results = {}

        for tc in tool_calls:
            result_key = f"tool_result:{tc.id}"
            result = await redis.get(result_key)
            results[tc.id] = result

        return results

    async def _publish_event(
        self,
        job_id: UUID,
        event_type: str,
        data: dict[str, Any],
    ) -> None:
        """Publish an event to Redis for SSE streaming.

        Matches the signature used in JobHandler.

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
