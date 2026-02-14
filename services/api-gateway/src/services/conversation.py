"""Conversation service for managing chat sessions."""

import uuid

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import selectinload

from libs.common import get_logger
from libs.db.models import ChatMessage, Conversation, FileUpload, Job
from libs.db.session import get_session_context

logger = get_logger(__name__)


def _generate_title(content: str | None) -> str:
    """Generate a conversation title from the first user message."""
    if not content:
        return "New chat"
    # Truncate at word boundary around 80 chars
    if len(content) <= 80:
        return content.strip()
    truncated = content[:80].rsplit(" ", 1)[0]
    return f"{truncated.strip()}..."


class ConversationService:
    """Service for conversation CRUD operations."""

    async def create_conversation(
        self,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID | None,
        title: str,
    ) -> Conversation:
        """Create a new conversation."""
        async with get_session_context() as session:
            conv = Conversation(
                tenant_id=tenant_id,
                user_id=user_id,
                title=title,
            )
            session.add(conv)
            await session.flush()
            await session.refresh(conv)
            logger.info(
                "Conversation created",
                conversation_id=str(conv.id),
                tenant_id=str(tenant_id),
            )
            return conv

    async def get_conversation(
        self,
        conversation_id: uuid.UUID,
        tenant_id: uuid.UUID,
    ) -> Conversation | None:
        """Get a single conversation by ID, scoped to tenant."""
        async with get_session_context() as session:
            stmt = select(Conversation).where(
                Conversation.id == conversation_id,
                Conversation.tenant_id == tenant_id,
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def list_conversations(
        self,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[Conversation], int]:
        """List conversations with pagination, ordered by updated_at DESC."""
        async with get_session_context() as session:
            # Base filter
            filters = [
                Conversation.tenant_id == tenant_id,
                Conversation.is_archived == False,  # noqa: E712
            ]
            if user_id:
                filters.append(Conversation.user_id == user_id)

            # Count
            count_stmt = select(func.count(Conversation.id)).where(*filters)
            total = (await session.execute(count_stmt)).scalar() or 0

            # Fetch
            stmt = (
                select(Conversation)
                .where(*filters)
                .order_by(Conversation.updated_at.desc())
                .offset(offset)
                .limit(limit)
            )
            result = await session.execute(stmt)
            conversations = list(result.scalars().all())

            return conversations, total

    async def update_conversation(
        self,
        conversation_id: uuid.UUID,
        tenant_id: uuid.UUID,
        title: str,
    ) -> Conversation | None:
        """Update conversation title."""
        async with get_session_context() as session:
            stmt = (
                update(Conversation)
                .where(
                    Conversation.id == conversation_id,
                    Conversation.tenant_id == tenant_id,
                )
                .values(title=title)
                .returning(Conversation)
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def delete_conversation(
        self,
        conversation_id: uuid.UUID,
        tenant_id: uuid.UUID,
    ) -> bool:
        """Delete a conversation and its associated jobs (cascade)."""
        async with get_session_context() as session:
            stmt = delete(Conversation).where(
                Conversation.id == conversation_id,
                Conversation.tenant_id == tenant_id,
            )
            result = await session.execute(stmt)
            deleted = result.rowcount > 0
            if deleted:
                logger.info(
                    "Conversation deleted",
                    conversation_id=str(conversation_id),
                    tenant_id=str(tenant_id),
                )
            return deleted

    async def get_conversation_messages(
        self,
        conversation_id: uuid.UUID,
        tenant_id: uuid.UUID,
    ) -> list[dict]:
        """Get all messages across jobs in a conversation, ordered chronologically."""
        async with get_session_context() as session:
            # Verify conversation belongs to tenant
            conv = await self.get_conversation(conversation_id, tenant_id)
            if not conv:
                return []

            stmt = (
                select(ChatMessage, Job.created_at.label("job_created_at"), Job.metadata_)
                .join(Job, ChatMessage.job_id == Job.id)
                .where(Job.conversation_id == conversation_id)
                .order_by(Job.created_at.asc(), ChatMessage.sequence_num.asc())
            )
            result = await session.execute(stmt)
            rows = result.all()

            # Collect all file_ids to fetch in a single query
            all_file_ids: set[str] = set()
            job_file_ids: dict[str, list[str]] = {}  # job_id -> file_ids

            for row in rows:
                msg = row[0]  # ChatMessage
                job_metadata = row[2]  # Job.metadata_
                role_value = msg.role.value if hasattr(msg.role, "value") else msg.role
                if role_value == "user" and job_metadata:
                    file_ids = job_metadata.get("file_ids", [])
                    if file_ids:
                        job_file_ids[str(msg.job_id)] = file_ids
                        all_file_ids.update(file_ids)

            # Fetch all file metadata in one query
            file_map: dict[str, dict] = {}
            if all_file_ids:
                file_uuids = [uuid.UUID(fid) for fid in all_file_ids]
                file_stmt = select(FileUpload).where(FileUpload.id.in_(file_uuids))
                file_result = await session.execute(file_stmt)
                for file_upload in file_result.scalars():
                    file_map[str(file_upload.id)] = {
                        "id": str(file_upload.id),
                        "type": "image" if file_upload.content_type.startswith("image/") else "file",
                        "url": f"/api/v1/files/{file_upload.id}/download",
                        "name": file_upload.filename,
                        "size": file_upload.size_bytes,
                        "content_type": file_upload.content_type,
                    }

            messages = []
            for row in rows:
                msg = row[0]  # ChatMessage
                role = msg.role.value if hasattr(msg.role, "value") else msg.role

                message_dict = {
                    "id": str(msg.id),
                    "role": role,
                    "content": msg.content,
                    "job_id": str(msg.job_id),
                    "created_at": msg.created_at.isoformat() if msg.created_at else None,
                }

                # Include tool_calls if present
                if msg.tool_calls:
                    message_dict["tool_calls"] = msg.tool_calls

                # Add attachments for user messages
                if role == "user" and str(msg.job_id) in job_file_ids:
                    attachments = []
                    for fid in job_file_ids[str(msg.job_id)]:
                        if fid in file_map:
                            attachments.append(file_map[fid])
                    if attachments:
                        message_dict["attachments"] = attachments

                messages.append(message_dict)

            # Merge consecutive assistant messages from the same job
            # This handles cases where tool_call + message events create separate records
            merged_messages = self._merge_assistant_messages(messages)

            return merged_messages

    def _merge_assistant_messages(self, messages: list[dict]) -> list[dict]:
        """Merge consecutive assistant messages from the same job.

        When tools are used, multiple ChatMessage records are created per job:
        - tool_call event: role=assistant, content=NULL, tool_calls=[...]
        - tool_result event: role=tool, content="result"
        - message event: role=assistant, content="final response"

        This method merges these into coherent messages for the frontend,
        combining tool_calls and content from the same job.
        """
        if not messages:
            return []

        merged: list[dict] = []
        current_assistant: dict | None = None
        current_job_id: str | None = None

        for msg in messages:
            role = msg["role"]
            job_id = msg["job_id"]

            # Skip tool messages - they're intermediate results
            if role == "tool":
                continue

            if role == "assistant":
                # Check if we should merge with current assistant message
                if current_assistant is not None and current_job_id == job_id:
                    # Merge into existing assistant message
                    # Combine content (prefer non-null)
                    if msg.get("content"):
                        if current_assistant.get("content"):
                            # Both have content - append
                            current_assistant["content"] += "\n\n" + msg["content"]
                        else:
                            current_assistant["content"] = msg["content"]

                    # Merge tool_calls arrays
                    if msg.get("tool_calls"):
                        if current_assistant.get("tool_calls"):
                            current_assistant["tool_calls"].extend(msg["tool_calls"])
                        else:
                            current_assistant["tool_calls"] = msg["tool_calls"]

                    # Use earliest created_at
                    if msg.get("created_at") and current_assistant.get("created_at"):
                        if msg["created_at"] < current_assistant["created_at"]:
                            current_assistant["created_at"] = msg["created_at"]
                else:
                    # New assistant message (different job or first one)
                    # Flush previous assistant message if any
                    if current_assistant is not None:
                        merged.append(current_assistant)

                    # Start tracking new assistant message
                    current_assistant = msg.copy()
                    current_job_id = job_id
            else:
                # Non-assistant message (user, system)
                # Flush current assistant message if any
                if current_assistant is not None:
                    merged.append(current_assistant)
                    current_assistant = None
                    current_job_id = None

                merged.append(msg)

        # Don't forget the last assistant message
        if current_assistant is not None:
            merged.append(current_assistant)

        return merged

    async def search_conversations(
        self,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID | None,
        query: str,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[Conversation], int]:
        """Full-text search across conversation messages."""
        async with get_session_context() as session:
            # Search in chat_messages content, join through Job to Conversation
            search_filter = func.to_tsvector(
                "english", func.coalesce(ChatMessage.content, "")
            ).match(query)

            base_filters = [
                Conversation.tenant_id == tenant_id,
                Conversation.is_archived == False,  # noqa: E712
            ]
            if user_id:
                base_filters.append(Conversation.user_id == user_id)

            # Get distinct conversation IDs matching the search
            subq = (
                select(Job.conversation_id)
                .join(ChatMessage, ChatMessage.job_id == Job.id)
                .where(
                    Job.conversation_id.isnot(None),
                    search_filter,
                )
                .distinct()
                .subquery()
            )

            # Count
            count_stmt = (
                select(func.count(Conversation.id))
                .where(
                    *base_filters,
                    Conversation.id.in_(select(subq.c.conversation_id)),
                )
            )
            total = (await session.execute(count_stmt)).scalar() or 0

            # Fetch conversations
            stmt = (
                select(Conversation)
                .where(
                    *base_filters,
                    Conversation.id.in_(select(subq.c.conversation_id)),
                )
                .order_by(Conversation.updated_at.desc())
                .offset(offset)
                .limit(limit)
            )
            result = await session.execute(stmt)
            conversations = list(result.scalars().all())

            return conversations, total

    async def touch_conversation(
        self,
        conversation_id: uuid.UUID,
    ) -> None:
        """Update the updated_at timestamp of a conversation."""
        async with get_session_context() as session:
            stmt = (
                update(Conversation)
                .where(Conversation.id == conversation_id)
                .values(updated_at=func.now())
            )
            await session.execute(stmt)


def get_conversation_service() -> ConversationService:
    """Factory for ConversationService."""
    return ConversationService()
