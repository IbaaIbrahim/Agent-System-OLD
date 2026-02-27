"""Conversations router for managing chat sessions."""

import uuid

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from libs.common import get_logger
from libs.common.exceptions import NotFoundError

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

    messages = await service.get_conversation_messages(conversation_id, tenant_id)

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
