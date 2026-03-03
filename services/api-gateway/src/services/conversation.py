"""Conversation service for managing chat sessions."""

import json
import uuid

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import selectinload

from libs.common import get_logger
from libs.db.models import ChatMessage, Conversation, FileUpload, Job
from libs.db.session import get_session_context
from libs.messaging.redis import RedisStreams

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
                        "url": f"/v1/files/{file_upload.id}/download",
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

                # Include tool_call_id and tool_name for role=tool messages
                if role == "tool":
                    if msg.tool_call_id:
                        message_dict["tool_call_id"] = msg.tool_call_id
                    if msg.metadata_ and msg.metadata_.get("tool_name"):
                        message_dict["tool_name"] = msg.metadata_["tool_name"]

                # Include reply_to_message_id if present
                if msg.metadata_ and msg.metadata_.get("reply_to_message_id"):
                    message_dict["reply_to_message_id"] = msg.metadata_[
                        "reply_to_message_id"
                    ]

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

    async def _fetch_hot_events(
        self,
        job_ids: set[str],
        known_event_ids: set[str],
        rows: list,
        jobs_with_assistant: set[str] | None = None,
    ) -> list[dict]:
        """Fetch recent events from Redis streams that aren't yet in PostgreSQL.

        Returns message dicts in the same format as the tree-walk adjacency
        entries so they can be merged directly.
        """
        # Event types that correspond to ChatMessage records
        CONVERSATIONAL_TYPES = {"message", "tool_call", "tool_result"}

        streams = RedisStreams()
        hot_messages: list[dict] = []
        if jobs_with_assistant is None:
            jobs_with_assistant = set()

        # Build a lookup: job_id -> last message id (for parent linking)
        last_msg_by_job: dict[str, str] = {}
        for row in rows:
            msg = row[0]
            last_msg_by_job[str(msg.job_id)] = str(msg.id)

        for job_id in job_ids:
            # Skip jobs that already have their assistant response in PG —
            # the archiver has fully processed them, so Redis events would
            # be duplicates that inflate branch counts.
            if job_id in jobs_with_assistant:
                continue

            stream_key = f"events:{job_id}"
            try:
                entries = await streams.read_range(
                    stream=stream_key, count=500,
                )
            except Exception:
                logger.debug(
                    "No Redis stream for job (may have expired)",
                    job_id=job_id,
                )
                continue

            for entry in entries:
                event_type = entry.data.get("type", "")
                if event_type not in CONVERSATIONAL_TYPES:
                    continue

                # Deduplicate: skip events already archived in PostgreSQL
                event_id = entry.id
                if event_id in known_event_ids:
                    continue

                event_data = entry.data.get("data", {})
                if isinstance(event_data, str):
                    try:
                        event_data = json.loads(event_data)
                    except (json.JSONDecodeError, TypeError):
                        continue

                # Determine parent: chain to last known message in this job
                parent_id = last_msg_by_job.get(job_id)

                # Build a synthetic message ID from the Redis entry ID
                # so it's unique but distinguishable from UUIDs
                synthetic_id = f"hot-{job_id[:8]}-{entry.id}"

                if event_type == "message":
                    role = event_data.get("role", "assistant")
                    msg_dict: dict = {
                        "id": synthetic_id,
                        "role": role,
                        "content": event_data.get("content"),
                        "job_id": job_id,
                        "created_at": None,
                        "parent_message_id": parent_id,
                    }
                    if event_data.get("tool_calls"):
                        msg_dict["tool_calls"] = event_data["tool_calls"]

                elif event_type == "tool_call":
                    msg_dict = {
                        "id": synthetic_id,
                        "role": "assistant",
                        "content": event_data.get("content"),
                        "job_id": job_id,
                        "created_at": None,
                        "parent_message_id": parent_id,
                        "tool_calls": event_data.get("tool_calls"),
                    }

                elif event_type == "tool_result":
                    msg_dict = {
                        "id": synthetic_id,
                        "role": "tool",
                        "content": event_data.get("result"),
                        "job_id": job_id,
                        "created_at": None,
                        "parent_message_id": parent_id,
                        "tool_call_id": event_data.get("tool_call_id"),
                        "tool_name": event_data.get("tool_name"),
                    }
                else:
                    continue

                hot_messages.append(msg_dict)
                # Update chain: next event in this job parents to this one
                last_msg_by_job[job_id] = synthetic_id

        if hot_messages:
            logger.info(
                "Merged hot events from Redis",
                count=len(hot_messages),
                job_ids=list(job_ids),
            )

        return hot_messages

    def _merge_assistant_messages(self, messages: list[dict]) -> list[dict]:
        """Merge consecutive assistant messages from the same job.

        When tools are used, multiple ChatMessage records are created per job:
        - tool_call event: role=assistant, content=NULL, tool_calls=[...]
        - tool_result event: role=tool, content="result", tool_call_id="..."
        - message event: role=assistant, content="final response"

        This method merges these into coherent messages for the frontend,
        combining tool_calls, tool_results, and content from the same job.
        """
        if not messages:
            return []

        # First pass: build a lookup of tool results keyed by job_id
        # Each job may have multiple tool results (one per tool call)
        tool_results_by_job: dict[str, list[dict]] = {}
        for msg in messages:
            if msg["role"] == "tool":
                job_id = msg["job_id"]
                if job_id not in tool_results_by_job:
                    tool_results_by_job[job_id] = []
                tool_results_by_job[job_id].append({
                    "tool_call_id": msg.get("tool_call_id", ""),
                    "tool_name": msg.get("tool_name"),
                    "result": msg.get("content"),
                })

        merged: list[dict] = []
        current_assistant: dict | None = None
        current_job_id: str | None = None

        for msg in messages:
            role = msg["role"]
            job_id = msg["job_id"]

            # Skip tool messages - captured in tool_results_by_job above
            if role == "tool":
                continue

            if role == "assistant":
                # Check if we should merge with current assistant message
                if current_assistant is not None and current_job_id == job_id:
                    # Merge into existing assistant message
                    # Combine content (prefer non-null)
                    if msg.get("content"):
                        if current_assistant.get("content"):
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

                    # Attach tool_results for this job if any
                    if job_id in tool_results_by_job:
                        current_assistant["tool_results"] = tool_results_by_job[job_id]
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

    async def get_conversation_messages_tree(
        self,
        conversation_id: uuid.UUID,
        tenant_id: uuid.UUID,
        active_branch_override: dict[str, str] | None = None,
    ) -> list[dict]:
        """Get messages for the active branch of a conversation tree.

        Walks the message tree from root to leaves, following active_branch
        selections at each branch point. Falls back to latest child when no
        explicit selection exists.
        """
        async with get_session_context() as session:
            conv = await self.get_conversation(conversation_id, tenant_id)
            if not conv:
                return []

            active_branch = (
                active_branch_override
                if active_branch_override is not None
                else (conv.active_branch or {})
            )

            # Fetch all messages
            stmt = (
                select(
                    ChatMessage,
                    Job.created_at.label("job_created_at"),
                    Job.metadata_,
                )
                .join(Job, ChatMessage.job_id == Job.id)
                .where(Job.conversation_id == conversation_id)
                .order_by(Job.created_at.asc(), ChatMessage.sequence_num.asc())
            )
            result = await session.execute(stmt)
            rows = result.all()

            if not rows:
                return []

            # Check if any message has parent_message_id set
            has_tree = any(row[0].parent_message_id is not None for row in rows)
            if not has_tree:
                return await self.get_conversation_messages(
                    conversation_id, tenant_id
                )

            # Collect file info
            all_file_ids: set[str] = set()
            job_file_ids: dict[str, list[str]] = {}
            for row in rows:
                msg = row[0]
                job_metadata = row[2]
                role_value = (
                    msg.role.value if hasattr(msg.role, "value") else msg.role
                )
                if role_value == "user" and job_metadata:
                    fids = job_metadata.get("file_ids", [])
                    if fids:
                        job_file_ids[str(msg.job_id)] = fids
                        all_file_ids.update(fids)

            file_map: dict[str, dict] = {}
            if all_file_ids:
                file_uuids = [uuid.UUID(fid) for fid in all_file_ids]
                file_stmt = select(FileUpload).where(
                    FileUpload.id.in_(file_uuids)
                )
                file_result = await session.execute(file_stmt)
                for fu in file_result.scalars():
                    file_map[str(fu.id)] = {
                        "id": str(fu.id),
                        "type": (
                            "image"
                            if fu.content_type.startswith("image/")
                            else "file"
                        ),
                        "url": f"/v1/files/{fu.id}/download",
                        "name": fu.filename,
                        "size": fu.size_bytes,
                        "content_type": fu.content_type,
                    }

            # ---- Hot event merge: fetch recent events from Redis ----
            # The archiver buffers events for up to 5s before flushing to
            # PostgreSQL.  To avoid a "reload gap" we also read from the
            # Redis event streams for each job and merge any events that
            # haven't been archived yet.
            known_event_ids: set[str] = set()
            jobs_with_assistant: set[str] = set()
            for row in rows:
                msg = row[0]
                if msg.metadata_ and msg.metadata_.get("event_id"):
                    known_event_ids.add(msg.metadata_["event_id"])
                role_val = (
                    msg.role.value if hasattr(msg.role, "value") else msg.role
                )
                if role_val == "assistant":
                    jobs_with_assistant.add(str(msg.job_id))

            job_ids = {str(row[0].job_id) for row in rows}
            hot_messages = await self._fetch_hot_events(
                job_ids, known_event_ids, rows,
                jobs_with_assistant,
            )

            # Build adjacency list: parent_id -> list of child messages
            children: dict[str | None, list[dict]] = {}

            for row in rows:
                msg = row[0]
                role = (
                    msg.role.value if hasattr(msg.role, "value") else msg.role
                )
                parent_id = (
                    str(msg.parent_message_id)
                    if msg.parent_message_id
                    else None
                )

                msg_dict: dict = {
                    "id": str(msg.id),
                    "role": role,
                    "content": msg.content,
                    "job_id": str(msg.job_id),
                    "created_at": (
                        msg.created_at.isoformat()
                        if msg.created_at
                        else None
                    ),
                    "parent_message_id": parent_id,
                }

                if msg.tool_calls:
                    msg_dict["tool_calls"] = msg.tool_calls
                if role == "tool":
                    if msg.tool_call_id:
                        msg_dict["tool_call_id"] = msg.tool_call_id
                    if msg.metadata_ and msg.metadata_.get("tool_name"):
                        msg_dict["tool_name"] = msg.metadata_["tool_name"]
                if msg.metadata_ and msg.metadata_.get("reply_to_message_id"):
                    msg_dict["reply_to_message_id"] = msg.metadata_[
                        "reply_to_message_id"
                    ]
                if role == "user" and str(msg.job_id) in job_file_ids:
                    attachments = []
                    for fid in job_file_ids[str(msg.job_id)]:
                        if fid in file_map:
                            attachments.append(file_map[fid])
                    if attachments:
                        msg_dict["attachments"] = attachments

                children.setdefault(parent_id, []).append(msg_dict)

            # Merge hot (Redis) messages into the adjacency list
            for hot_msg in hot_messages:
                parent_id = hot_msg.get("parent_message_id")
                children.setdefault(parent_id, []).append(hot_msg)

            # Walk tree from roots following active_branch
            result_messages: list[dict] = []
            current_nodes = children.get(None, [])

            while current_nodes:
                # Separate assistant/tool messages from user messages.
                # Due to a race condition, the archiver may write an
                # assistant response *after* the next user message is
                # created, causing both to share the same parent.  In
                # that case we chain them: assistant first, then treat
                # user messages as children of the last assistant.
                non_user = [
                    n for n in current_nodes
                    if n["role"] in ("assistant", "tool")
                ]
                user_nodes = [
                    n for n in current_nodes
                    if n["role"] == "user"
                ]

                if non_user and user_nodes:
                    # Mixed siblings — chain assistant/tool msgs first
                    non_user.sort(
                        key=lambda x: (x.get("created_at") or "", x["id"]),
                    )
                    for msg in non_user:
                        result_messages.append(msg)
                    # Gather actual children of the assistant msgs
                    next_nodes = list(user_nodes)
                    for msg in non_user:
                        next_nodes.extend(children.get(msg["id"], []))
                    current_nodes = next_nodes
                    continue

                # All nodes are the same kind — apply branch logic
                if len(current_nodes) == 1:
                    chosen = current_nodes[0]
                else:
                    # Multiple children = branch point on the parent
                    parent_id = current_nodes[0].get("parent_message_id")
                    active_child_id = (
                        active_branch.get(parent_id) if parent_id else None
                    )

                    chosen = None
                    chosen_index = 0
                    if active_child_id:
                        for i, c in enumerate(current_nodes):
                            if c["id"] == active_child_id:
                                chosen = c
                                chosen_index = i
                                break

                    if chosen is None:
                        chosen = current_nodes[-1]
                        chosen_index = len(current_nodes) - 1

                    # Annotate with branch metadata
                    chosen["branch_point"] = True
                    chosen["branch_count"] = len(current_nodes)
                    chosen["active_branch_index"] = chosen_index
                    chosen["branch_ids"] = [c["id"] for c in current_nodes]

                result_messages.append(chosen)
                current_nodes = children.get(chosen["id"], [])

            merged = self._merge_assistant_messages(result_messages)
            return merged

    async def switch_branch(
        self,
        conversation_id: uuid.UUID,
        tenant_id: uuid.UUID,
        branch_point_message_id: str,
        target_child_message_id: str,
    ) -> None:
        """Switch the active branch at a given branch point."""
        async with get_session_context() as session:
            conv_stmt = select(Conversation).where(
                Conversation.id == conversation_id,
                Conversation.tenant_id == tenant_id,
            )
            result = await session.execute(conv_stmt)
            conv = result.scalar_one_or_none()
            if not conv:
                return

            branch = dict(conv.active_branch or {})
            branch[branch_point_message_id] = target_child_message_id

            stmt = (
                update(Conversation)
                .where(Conversation.id == conversation_id)
                .values(active_branch=branch)
            )
            await session.execute(stmt)

    async def get_branch_context_messages(
        self,
        conversation_id: uuid.UUID,
        tenant_id: uuid.UUID,
        branch_point_message_id: str,
    ) -> list[dict]:
        """Get all messages from root to a specific message (inclusive).

        Used to build conversation context when creating a new branch.
        """
        async with get_session_context() as session:
            stmt = (
                select(ChatMessage)
                .join(Job, ChatMessage.job_id == Job.id)
                .where(Job.conversation_id == conversation_id)
            )
            result = await session.execute(stmt)
            all_msgs = {str(m.id): m for m in result.scalars().all()}

            # Walk from target back to root
            path: list[str] = []
            current_id: str | None = branch_point_message_id
            while current_id and current_id in all_msgs:
                path.append(current_id)
                msg = all_msgs[current_id]
                current_id = (
                    str(msg.parent_message_id)
                    if msg.parent_message_id
                    else None
                )

            path.reverse()

            context: list[dict] = []
            for msg_id in path:
                msg = all_msgs[msg_id]
                role = (
                    msg.role.value
                    if hasattr(msg.role, "value")
                    else msg.role
                )
                context.append({
                    "role": role,
                    "content": msg.content,
                    "tool_calls": msg.tool_calls,
                    "tool_call_id": msg.tool_call_id,
                })

            return context

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
