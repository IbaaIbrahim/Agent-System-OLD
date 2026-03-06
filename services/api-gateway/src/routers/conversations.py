"""Conversations router for managing chat sessions."""

import uuid

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from libs.common import get_logger
from libs.common.auth import create_internal_transaction_token, create_stream_ott
from libs.common.exceptions import NotFoundError, ValidationError
from libs.common.tool_catalog import TOOL_CATALOG, ToolBehavior
from libs.db.models import ChatMessage as ChatMessageModel
from libs.db.models import Conversation, Job, JobStatus, MessageRole
from libs.db.session import get_session_context
from libs.messaging.kafka import get_producer
from sqlalchemy import select

from ..config import get_config
from ..middleware.tenant import get_tenant_id, get_user_id
from ..services.conversation import get_conversation_service

logger = get_logger(__name__)

router = APIRouter()


# --- Response Models ---


class ConversationSummary(BaseModel):
    """Summary of a conversation for list views."""

    id: str
    title: str
    created_at: str
    updated_at: str


class ConversationListResponse(BaseModel):
    """Paginated list of conversations."""

    conversations: list[ConversationSummary]
    total: int
    offset: int
    limit: int


class AttachmentInfo(BaseModel):
    """Attachment file information."""

    id: str
    type: str  # 'image' or 'file'
    url: str
    name: str
    size: int
    content_type: str


class ToolResultInfo(BaseModel):
    """A tool result linked to a tool call."""

    tool_call_id: str
    tool_name: str | None = None
    result: str | None = None


class ConversationMessage(BaseModel):
    """A message within a conversation."""

    id: str
    role: str
    content: str | None
    job_id: str
    created_at: str | None
    attachments: list[AttachmentInfo] | None = None
    tool_calls: list[dict] | None = None
    tool_results: list[ToolResultInfo] | None = None
    reply_to_message_id: str | None = None
    # Branching
    parent_message_id: str | None = None
    branch_point: bool | None = None
    branch_count: int | None = None
    active_branch_index: int | None = None
    branch_ids: list[str] | None = None


class ConversationDetailResponse(BaseModel):
    """Conversation with full message history."""

    id: str
    title: str
    created_at: str
    updated_at: str
    messages: list[ConversationMessage]


class UpdateConversationRequest(BaseModel):
    """Request body for updating a conversation."""

    title: str = Field(..., min_length=1, max_length=500)


# --- Endpoints ---


@router.get("/conversations/search", response_model=ConversationListResponse)
async def search_conversations(
    q: str = Query(..., min_length=1, max_length=500),
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    user_id: uuid.UUID | None = Depends(get_user_id),
) -> ConversationListResponse:
    """Search conversations by message content using full-text search."""
    service = get_conversation_service()
    conversations, total = await service.search_conversations(
        tenant_id=tenant_id,
        user_id=user_id,
        query=q,
        offset=offset,
        limit=limit,
    )

    return ConversationListResponse(
        conversations=[
            ConversationSummary(
                id=str(c.id),
                title=c.title,
                created_at=c.created_at.isoformat(),
                updated_at=c.updated_at.isoformat(),
            )
            for c in conversations
        ],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.get("/conversations", response_model=ConversationListResponse)
async def list_conversations(
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    user_id: uuid.UUID | None = Depends(get_user_id),
) -> ConversationListResponse:
    """List conversations for the current user, ordered by most recent."""
    service = get_conversation_service()
    conversations, total = await service.list_conversations(
        tenant_id=tenant_id,
        user_id=user_id,
        offset=offset,
        limit=limit,
    )

    return ConversationListResponse(
        conversations=[
            ConversationSummary(
                id=str(c.id),
                title=c.title,
                created_at=c.created_at.isoformat(),
                updated_at=c.updated_at.isoformat(),
            )
            for c in conversations
        ],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.get(
    "/conversations/{conversation_id}",
    response_model=ConversationDetailResponse,
)
async def get_conversation(
    conversation_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
) -> ConversationDetailResponse:
    """Get a conversation with its full message history."""
    service = get_conversation_service()

    conv = await service.get_conversation(conversation_id, tenant_id)
    if not conv:
        raise NotFoundError("Conversation not found")

    # Use tree-based loading which falls back to flat for old conversations
    messages = await service.get_conversation_messages_tree(
        conversation_id, tenant_id
    )

    return ConversationDetailResponse(
        id=str(conv.id),
        title=conv.title,
        created_at=conv.created_at.isoformat(),
        updated_at=conv.updated_at.isoformat(),
        messages=[
            ConversationMessage(**msg)
            for msg in messages
        ],
    )


@router.patch(
    "/conversations/{conversation_id}",
    response_model=ConversationSummary,
)
async def update_conversation(
    conversation_id: uuid.UUID,
    body: UpdateConversationRequest,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
) -> ConversationSummary:
    """Update a conversation's title."""
    service = get_conversation_service()

    conv = await service.update_conversation(
        conversation_id=conversation_id,
        tenant_id=tenant_id,
        title=body.title,
    )
    if not conv:
        raise NotFoundError("Conversation not found")

    return ConversationSummary(
        id=str(conv.id),
        title=conv.title,
        created_at=conv.created_at.isoformat(),
        updated_at=conv.updated_at.isoformat(),
    )


@router.delete("/conversations/{conversation_id}")
async def delete_conversation(
    conversation_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
) -> dict:
    """Delete a conversation and all its associated data."""
    service = get_conversation_service()

    deleted = await service.delete_conversation(conversation_id, tenant_id)
    if not deleted:
        raise NotFoundError("Conversation not found")

    return {"status": "deleted"}


# --- Edit / Branching ---


class EditMessageRequest(BaseModel):
    """Request to edit a user message, creating a new branch."""

    message_id: str
    content: str = Field(..., min_length=1)


class EditMessageResponse(BaseModel):
    """Response after creating an edit branch."""

    job_id: str
    stream_url: str
    stream_token: str
    branch_message_id: str
    conversation_id: str
    parent_message_id: str | None = None
    branch_count: int = 1
    active_branch_index: int = 0
    branch_ids: list[str] = []


class SwitchBranchRequest(BaseModel):
    """Request to switch active branch at a branch point."""

    branch_point_message_id: str
    target_child_message_id: str


@router.post(
    "/conversations/{conversation_id}/edit-message",
    response_model=EditMessageResponse,
)
async def edit_message(
    conversation_id: uuid.UUID,
    body: EditMessageRequest,
    request: Request,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
) -> EditMessageResponse:
    """Edit a user message, creating a new conversation branch.

    1. Validate message belongs to conversation and is a user message
    2. Create new Job + ChatMessage with edited content, sharing the parent
    3. Build conversation context up to the branch point
    4. Update active_branch to point to new message
    5. Publish to Kafka and return stream URL
    """
    config = get_config()
    service = get_conversation_service()
    user_id = getattr(request.state, "user_id", None)
    partner_id = getattr(request.state, "partner_id", None)

    # 1. Validate conversation
    conv = await service.get_conversation(conversation_id, tenant_id)
    if not conv:
        raise NotFoundError("Conversation not found")

    # 2. Validate message exists in this conversation and is a user message
    # Parse message_id safely so bad values return a 4xx error instead of 500
    try:
        message_uuid = uuid.UUID(body.message_id)
    except ValueError as exc:
        raise ValidationError(
            message="Invalid message_id",
            errors=[
                {
                    "field": "message_id",
                    "message": "Must be a valid UUID string",
                }
            ],
        ) from exc

    original_msg = None
    async with get_session_context() as session:
        stmt = (
            select(ChatMessageModel)
            .join(Job, ChatMessageModel.job_id == Job.id)
            .where(
                ChatMessageModel.id == message_uuid,
                Job.conversation_id == conversation_id,
            )
        )
        result = await session.execute(stmt)
        original_msg = result.scalar_one_or_none()

    if not original_msg:
        raise NotFoundError("Message not found in this conversation")

    role_value = (
        original_msg.role.value
        if hasattr(original_msg.role, "value")
        else original_msg.role
    )
    if role_value != "user":
        raise ValidationError(
            message="Only user messages can be edited",
            errors=[{
                "field": "message_id",
                "message": "Message is not a user message",
            }],
        )

    # 3. Determine branch point: the original message's parent
    parent_message_id = original_msg.parent_message_id

    # 4. Build context messages up to branch point
    if parent_message_id:
        context_messages = await service.get_branch_context_messages(
            conversation_id, tenant_id, str(parent_message_id)
        )
    else:
        context_messages = []

    # Add the edited user message to context
    context_messages.append({
        "role": "user",
        "content": body.content,
    })

    # 5. Determine provider/model for the edit job and create Job + ChatMessage
    job_id = uuid.uuid4()
    new_msg_id: uuid.UUID | None = None

    provider = config.default_llm_provider
    if provider == "anthropic":
        model = config.anthropic_default_model
    elif provider == "openai":
        model = config.openai_default_model
    else:
        # Fallback to Anthropic defaults if an unexpected provider is configured
        model = config.anthropic_default_model

    async with get_session_context() as session:
        job = Job(
            id=job_id,
            tenant_id=tenant_id,
            user_id=user_id,
            conversation_id=conversation_id,
            status=JobStatus.PENDING,
            provider=provider,
            model_id=model,
            metadata_={},
        )
        session.add(job)

        new_msg = ChatMessageModel(
            job_id=job_id,
            sequence_num=0,
            role=MessageRole.USER,
            content=body.content,
            parent_message_id=parent_message_id,
        )
        session.add(new_msg)
        await session.flush()
        new_msg_id = new_msg.id

    # 6. Update active_branch: parent points to new edited message
    if parent_message_id:
        await service.switch_branch(
            conversation_id,
            tenant_id,
            str(parent_message_id),
            str(new_msg_id),
        )

    # 6b. Query sibling branch metadata for the response
    branch_count = 1
    active_branch_index = 0
    branch_ids: list[str] = [str(new_msg_id)]

    if parent_message_id:
        async with get_session_context() as session:
            sibling_stmt = (
                select(ChatMessageModel.id)
                .join(Job, ChatMessageModel.job_id == Job.id)
                .where(
                    Job.conversation_id == conversation_id,
                    ChatMessageModel.parent_message_id == parent_message_id,
                    ChatMessageModel.role == MessageRole.USER,
                )
                .order_by(ChatMessageModel.created_at.asc())
            )
            sibling_result = await session.execute(sibling_stmt)
            sibling_ids = [str(row[0]) for row in sibling_result.all()]
            branch_count = len(sibling_ids)
            branch_ids = sibling_ids
            # The new message is the active branch (last one created)
            try:
                active_branch_index = sibling_ids.index(str(new_msg_id))
            except ValueError:
                active_branch_index = branch_count - 1

    # 7. Build tools (same logic as chat endpoint — include AUTO_EXECUTE tools)
    final_tools = []
    for tool_name, tool_metadata in TOOL_CATALOG.items():
        if tool_metadata.behavior == ToolBehavior.AUTO_EXECUTE:
            final_tools.append({
                "name": tool_metadata.name,
                "description": tool_metadata.description,
                "parameters": tool_metadata.parameters,
                "category": "builtin",
            })

    # 8. Generate internal token and publish to Kafka
    internal_token = create_internal_transaction_token(
        job_id=job_id,
        tenant_id=tenant_id,
        credit_check_passed=True,
        max_tokens=4096,
        partner_id=partner_id,
    )

    api_messages = [
        {"role": m["role"], "content": m.get("content")}
        for m in context_messages
        if m.get("content")
    ]

    job_payload = {
        "job_id": str(job_id),
        "tenant_id": str(tenant_id),
        "partner_id": str(partner_id) if partner_id else None,
        "user_id": str(user_id) if user_id else None,
        "provider": provider,
        "model": model,
        "messages": api_messages,
        "system": None,
        "tools": final_tools if final_tools else None,
        "temperature": 0.7,
        "max_tokens": 4096,
        "stream": True,
        "metadata": {},
    }

    producer = await get_producer()
    await producer.send(
        topic=config.jobs_topic,
        message=job_payload,
        key=str(tenant_id),
        headers={
            "job_id": str(job_id),
            "tenant_id": str(tenant_id),
            "partner_id": str(partner_id) if partner_id else "",
            "internal_token": "present" if internal_token else "",
        },
    )

    logger.info(
        "Edit branch job published",
        job_id=str(job_id),
        conversation_id=str(conversation_id),
        original_message_id=body.message_id,
    )

    stream_ott = create_stream_ott(
        job_id=job_id,
        tenant_id=tenant_id,
        user_id=user_id,
        partner_id=partner_id,
    )
    stream_url = f"{config.stream_edge_url}/api/v1/stream?token={stream_ott}"

    return EditMessageResponse(
        job_id=str(job_id),
        stream_url=stream_url,
        stream_token=stream_ott,
        branch_message_id=str(new_msg_id),
        conversation_id=str(conversation_id),
        parent_message_id=str(parent_message_id) if parent_message_id else None,
        branch_count=branch_count,
        active_branch_index=active_branch_index,
        branch_ids=branch_ids,
    )


@router.post(
    "/conversations/{conversation_id}/switch-branch",
    response_model=ConversationDetailResponse,
)
async def switch_branch(
    conversation_id: uuid.UUID,
    body: SwitchBranchRequest,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
) -> ConversationDetailResponse:
    """Switch the active branch at a branch point and return updated conversation."""
    service = get_conversation_service()

    conv = await service.get_conversation(conversation_id, tenant_id)
    if not conv:
        raise NotFoundError("Conversation not found")

    await service.switch_branch(
        conversation_id,
        tenant_id,
        body.branch_point_message_id,
        body.target_child_message_id,
    )

    messages = await service.get_conversation_messages_tree(
        conversation_id, tenant_id
    )

    return ConversationDetailResponse(
        id=str(conv.id),
        title=conv.title,
        created_at=conv.created_at.isoformat(),
        updated_at=conv.updated_at.isoformat(),
        messages=[
            ConversationMessage(**msg)
            for msg in messages
        ],
    )
