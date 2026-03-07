"""Job handler for processing incoming jobs from Kafka."""

from typing import Any
from uuid import UUID

from libs.common import get_logger
from libs.common.tool_catalog import get_tool_metadata
from libs.messaging.redis import RedisPubSub

from ..config import get_config
from ..engine.state import StateManager
from ..services.llm_service import LLMService
from ..services.snapshot_service import SnapshotService
from .tool_handler import ToolHandler
from ..services.event_publisher import EventPublisher

logger = get_logger(__name__)


class JobHandler:
    """Handles incoming job messages from Kafka."""

    def __init__(self) -> None:
        self.config = get_config()
        self.state_manager = StateManager()
        self.llm_service = LLMService()
        self.tool_handler = ToolHandler()
        self.snapshot_service = SnapshotService()
        self.event_publisher = EventPublisher()
        
        # Load tool definition from catalog
        # We convert to dict effectively by reconstructing relevant parts or using model_dump
        meta = get_tool_metadata("analyze_file")
        if meta:
            self.analyze_file_tool = {
                "name": meta.name,
                "description": meta.description,
                "parameters": meta.parameters,
            }
        else:
            logger.error("analyze_file tool definition not found in catalog")
            self.analyze_file_tool = None

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
            tools = message.get("tools") or []
            
            # Auto-enable file analysis tool if files are present in metadata
            file_ids = message.get("metadata", {}).get("file_ids", [])
            if file_ids:
                logger.info("Injecting file context for job", job_id=str(job_id), count=len(file_ids))
                
                # Check if already present to avoid duplicates
                if not any(t.get("name") == "analyze_file" for t in tools):
                    if self.analyze_file_tool:
                        logger.info("Injecting analyze_file tool definition", job_id=str(job_id))
                        tools.append(self.analyze_file_tool)
                
                # Content injection: saved descriptions (extended access) + raw content when no saved
                from ..services.file_service import FileService
                all_blocks = []
                saved_descriptions_blocks = []
                for fid in file_ids:
                    saved_text, filename = await FileService.get_saved_file_description(fid)
                    if saved_text and filename:
                        saved_descriptions_blocks.append({
                            "type": "text",
                            "text": f"\n\n[Saved analysis for file: {filename}]\n{saved_text}\n--- End of saved analysis ---"
                        })
                    # If no saved description, inject raw content (from Redis/disk) so agent can use analyze_file
                    if not saved_text:
                        blocks = await FileService.get_content_blocks(fid)
                        all_blocks.extend(blocks)
                
                # Inject blocks into the last user message
                if (saved_descriptions_blocks or all_blocks) and message["messages"] and message["messages"][-1]["role"] == "user":
                    last_msg = message["messages"][-1]
                    original_content = last_msg.get("content", "")
                    
                    # Convert to list of blocks if it's currently a string
                    if isinstance(original_content, str):
                        last_msg["content"] = [{"type": "text", "text": original_content}]
                    elif original_content is None:
                        last_msg["content"] = []
                    
                    # Prepend saved descriptions so agent has extended access to previously analyzed files
                    if saved_descriptions_blocks:
                        last_msg["content"].append({
                            "type": "text",
                            "text": "\n\n[Attached Files – Saved Descriptions]\n"
                        })
                        last_msg["content"].extend(saved_descriptions_blocks)
                    # Raw content for files not yet analyzed (or no cache)
                    if all_blocks:
                        last_msg["content"].append({
                            "type": "text",
                            "text": "\n\n[Attached Files Content]\n"
                        })
                        last_msg["content"].extend(all_blocks)
                    
                    # System note: use saved descriptions when present; analyze_file for details or specific queries
                    files_list = ", ".join(file_ids)
                    last_msg["content"].append({
                        "type": "text",
                        "text": (
                            f"\n\n[System Note: Files IDs: {files_list}. "
                            "When a saved analysis is shown above, you can use it for extended access. "
                            "Use the 'analyze_file' tool for detailed analysis of files without a saved description, "
                            "or to query a specific section (e.g. 'what are the skills?') with the query parameter.]"
                        )
                    })
            
            # Reply context injection — tell the LLM what message is being replied to
            job_metadata = message.get("metadata", {})
            reply_to_message_id = job_metadata.get("reply_to_message_id")
            if reply_to_message_id:
                reply_to_content = job_metadata.get("reply_to_content", "")
                reply_to_role = job_metadata.get("reply_to_role", "assistant")
                reply_to_selected = job_metadata.get("reply_to_selected_text")

                if reply_to_content:
                    truncated = reply_to_content[:2000]
                    if len(reply_to_content) > 2000:
                        truncated += "..."

                    if reply_to_selected:
                        reply_context = (
                            f'[This message is a reply to a specific section of a previous '
                            f'{reply_to_role} message.\n'
                            f'Selected section: "{reply_to_selected}"\n'
                            f'Full message: "{truncated}"]'
                        )
                    else:
                        reply_context = (
                            f'[This message is a reply to a previous '
                            f'{reply_to_role} message: "{truncated}"]'
                        )

                    # Prepend to last user message
                    if message["messages"] and message["messages"][-1]["role"] == "user":
                        last_msg = message["messages"][-1]
                        original = last_msg.get("content") or ""
                        if isinstance(original, str):
                            last_msg["content"] = reply_context + "\n\n" + original
                        elif isinstance(original, list):
                            last_msg["content"].insert(0, {"type": "text", "text": reply_context})

                    logger.info(
                        "Injected reply context",
                        job_id=str(job_id),
                        reply_to_message_id=reply_to_message_id,
                        has_selected_text=bool(reply_to_selected),
                    )

            # Create agent state
            state = self.state_manager.create_state(
                job_id=job_id,
                tenant_id=tenant_id,
                user_id=UUID(message["user_id"]) if message.get("user_id") else None,
                provider=message["provider"],
                model=message["model"],
                messages=message["messages"],
                system_prompt=message.get("system"),
                tools=tools,
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

            # Check if multi-phase execution should be used (HIGH effort only)
            from ..prompts.effort_levels import get_effort_config

            effort_level = message.get("metadata", {}).get("effort_level")
            effort_config = get_effort_config(effort_level)

            if self.config.enable_multi_phase and effort_config.enable_multi_phase:
                from ..engine.phase_executor import PhaseExecutor

                logger.info(
                    "Using multi-phase execution",
                    job_id=str(job_id),
                    effort_level=effort_level,
                )
                phase_executor = PhaseExecutor(
                    llm_service=self.llm_service,
                    tool_handler=self.tool_handler,
                    snapshot_service=self.snapshot_service,
                    event_callback=self._publish_event,
                    config=self.config,
                )
                state = await phase_executor.execute(state)
            elif message.get("stream", True):
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
        await self.event_publisher.publish_event(
            job_id=job_id,
            event_type=event_type,
            data=data,
        )
