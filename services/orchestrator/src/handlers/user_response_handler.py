"""Handler for user responses to agent questions (human-in-the-loop)."""

from typing import Any
from uuid import UUID

from libs.common import get_logger

from ..config import get_config
from ..engine.state import AgentStatus
from ..services.event_publisher import EventPublisher
from ..services.llm_service import LLMService
from ..services.snapshot_service import SnapshotService
from ..services.state_lock import DistributedStateLock
from .tool_handler import ToolHandler

logger = get_logger(__name__)


class UserResponseHandler:
    """Handles user text responses to agent questions.

    Listens on agent.user-response topic. When a user responds:
    1. Acquires distributed lock for the job
    2. Loads snapshot from PostgreSQL
    3. Verifies job is in WAITING_USER state
    4. Injects user's text response into message history
    5. Clears pending question from phase state
    6. Resumes execution via PhaseExecutor
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

    async def handle_user_response(
        self,
        message: dict[str, Any],
        headers: dict[str, str],
    ) -> None:
        """Handle a user's text response to an agent question.

        Args:
            message: User response payload
            headers: Kafka message headers
        """
        job_id = UUID(message["job_id"])
        user_response = message.get("response", "")

        logger.info(
            "User response received",
            job_id=str(job_id),
            response_len=len(user_response),
        )

        # Acquire lock
        if not await self.lock.acquire(job_id, owner="user_response_handler"):
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

            # Verify state is waiting for user
            if state.status != AgentStatus.WAITING_USER:
                logger.debug(
                    "Job not in WAITING_USER state, skipping",
                    job_id=str(job_id),
                    status=state.status.value,
                )
                return

            # Inject user response into message history
            state.add_user_message(user_response)

            # Emit user response event
            await self._publish_event(
                job_id=job_id,
                event_type="message",
                data={
                    "content": user_response,
                    "role": "user",
                },
            )

            # Resume via PhaseExecutor
            from ..engine.phase_executor import PhaseExecutor

            phase_executor = PhaseExecutor(
                llm_service=self.llm_service,
                tool_handler=self.tool_handler,
                snapshot_service=self.snapshot_service,
                event_callback=self._publish_event,
                config=self.config,
            )

            state = await phase_executor.resume_after_user_response(state)

            # Save final state
            await self.snapshot_service.save_snapshot(state)
            await self.snapshot_service.update_job(state)

            logger.info(
                "Job resumed after user response",
                job_id=str(job_id),
                final_status=state.status.value,
            )

        except Exception as e:
            logger.exception(
                "User response handling failed",
                job_id=str(job_id),
                error=str(e),
            )
            await self._publish_event(
                job_id=job_id,
                event_type="error",
                data={"error": f"User response handling failed: {str(e)}"},
            )
            raise
        finally:
            await self.lock.release(job_id)

    async def _publish_event(
        self,
        job_id: UUID,
        event_type: str,
        data: dict[str, Any],
    ) -> None:
        """Publish an event to Redis for SSE streaming."""
        await self.event_publisher.publish_event(
            job_id=job_id,
            event_type=event_type,
            data=data,
        )
