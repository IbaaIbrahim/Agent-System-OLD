"""Job handler for processing incoming jobs from Kafka."""

from typing import Any
from uuid import UUID

from libs.common import get_logger
from libs.messaging.redis import RedisPubSub

from ..config import get_config
from ..engine.state import StateManager
from ..services.llm_service import LLMService
from ..services.snapshot_service import SnapshotService
from .tool_handler import ToolHandler

logger = get_logger(__name__)


class JobHandler:
    """Handles incoming job messages from Kafka."""

    def __init__(self) -> None:
        self.config = get_config()
        self.state_manager = StateManager()
        self.llm_service = LLMService()
        self.tool_handler = ToolHandler()
        self.snapshot_service = SnapshotService()

    async def handle_job(
        self,
        message: dict[str, Any],
        headers: dict[str, str],
    ) -> None:
        """Handle an incoming job message.

        Args:
            message: Job payload from Kafka
            headers: Message headers
        """
        job_id = UUID(message["job_id"])
        tenant_id = UUID(message["tenant_id"])

        logger.info(
            "Processing job",
            job_id=str(job_id),
            tenant_id=str(tenant_id),
        )

        try:
            # Create agent state
            state = self.state_manager.create_state(
                job_id=job_id,
                tenant_id=tenant_id,
                user_id=UUID(message["user_id"]) if message.get("user_id") else None,
                provider=message["provider"],
                model=message["model"],
                messages=message["messages"],
                system_prompt=message.get("system"),
                tools=message.get("tools"),
                temperature=message.get("temperature", 0.7),
                max_tokens=message.get("max_tokens", 4096),
                metadata=message.get("metadata", {}),
            )

            # Save initial state
            await self.snapshot_service.save_job(state)

            # Lazy import to avoid circular dependency
            from ..engine.agent import AgentExecutor

            # Create executor with event callback
            executor = AgentExecutor(
                llm_service=self.llm_service,
                tool_handler=self.tool_handler,
                snapshot_service=self.snapshot_service,
                event_callback=self._publish_event,
            )

            # Execute based on stream setting
            if message.get("stream", True):
                state = await executor.execute_streaming(state)
            else:
                state = await executor.execute(state)

            # Save final state
            await self.snapshot_service.save_snapshot(state)
            await self.snapshot_service.update_job(state)

            # Cleanup
            self.state_manager.remove_state(job_id)

            logger.info(
                "Job completed",
                job_id=str(job_id),
                status=state.status.value,
            )

        except Exception as e:
            logger.exception(
                "Job processing failed",
                job_id=str(job_id),
            )
            # Publish error event
            await self._publish_event(
                job_id=job_id,
                event_type="error",
                data={"error": str(e)},
            )
            raise

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
        from libs.messaging.redis import RedisStreams

        # Publish to pub/sub for real-time delivery
        pubsub = RedisPubSub()
        await pubsub.connect()

        channel = f"job:{job_id}"
        message = {
            "type": event_type,
            "data": data,
        }

        await pubsub.publish(channel, message)
        await pubsub.disconnect()

        # Also store in streams for catch-up
        streams = RedisStreams()
        stream_key = f"events:{job_id}"

        event_id = await streams.add(
            stream=stream_key,
            data={
                "type": event_type,
                "data": data,
            },
        )

        # Update message with event ID
        message["id"] = event_id
