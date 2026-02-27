"""Knowledge base REST API endpoints."""

import uuid

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field

from libs.common import get_logger
from libs.common.exceptions import NotFoundError

from ..middleware.tenant import get_tenant_id, get_user_id
from ..services.knowledge_base import get_knowledge_base_service

logger = get_logger(__name__)

router = APIRouter()


# --- Request/Response Models ---


class CreateEntryRequest(BaseModel):
    """Request to create a knowledge base entry."""

    title: str = Field(..., min_length=1, max_length=500)
    content: str = Field(..., min_length=1, max_length=50000)
    category: str | None = Field(None, max_length=100)
    tags: list[str] = Field(default_factory=list)
    file_ids: list[str] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)


class UpdateEntryRequest(BaseModel):
    """Request to update a knowledge base entry."""

    title: str | None = Field(None, min_length=1, max_length=500)
    content: str | None = Field(None, min_length=1, max_length=50000)
    category: str | None = Field(None, max_length=100)
    tags: list[str] | None = None
    file_ids: list[str] | None = None


class EntryResponse(BaseModel):
    """Knowledge base entry response."""

    id: str
    title: str
    content: str
    category: str | None
    tags: list[str]
    file_ids: list[str]
    metadata: dict
    has_embedding: bool
    created_at: str
    updated_at: str


class EntryListResponse(BaseModel):
    """Paginated list of entries."""

    entries: list[EntryResponse]
    total: int
    offset: int
    limit: int


# --- Endpoints ---


@router.post("/knowledge-base/entries", response_model=EntryResponse, status_code=201)
async def create_entry(
    body: CreateEntryRequest,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    user_id: uuid.UUID | None = Depends(get_user_id),
) -> EntryResponse:
    """Create a new knowledge base entry with semantic embedding."""
    service = get_knowledge_base_service()

    entry = await service.create_entry(
        tenant_id=tenant_id,
        user_id=user_id,
        title=body.title,
        content=body.content,
        category=body.category,
        tags=body.tags,
        file_ids=body.file_ids,
        metadata=body.metadata,
    )

    return EntryResponse(
        id=str(entry.id),
        title=entry.title,
        content=entry.content,
        category=entry.category,
        tags=entry.tags,
        file_ids=entry.file_ids,
        metadata=entry.metadata_,
        has_embedding=entry.has_embedding,
        created_at=entry.created_at.isoformat(),
        updated_at=entry.updated_at.isoformat(),
    )


@router.get("/knowledge-base/entries", response_model=EntryListResponse)
async def list_entries(
    category: str | None = Query(None),
    tags: list[str] = Query(default_factory=list),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
) -> EntryListResponse:
    """List knowledge base entries with optional filtering."""
    service = get_knowledge_base_service()

    entries, total = await service.list_entries(
        tenant_id=tenant_id,
        category=category,
        tags=tags if tags else None,
        offset=offset,
        limit=limit,
    )

    return EntryListResponse(
        entries=[
            EntryResponse(
                id=str(e.id),
                title=e.title,
                content=e.content,
                category=e.category,
                tags=e.tags,
                file_ids=e.file_ids,
                metadata=e.metadata_,
                has_embedding=e.has_embedding,
                created_at=e.created_at.isoformat(),
                updated_at=e.updated_at.isoformat(),
            )
            for e in entries
        ],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.get("/knowledge-base/entries/{entry_id}", response_model=EntryResponse)
async def get_entry(
    entry_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
) -> EntryResponse:
    """Get a single knowledge base entry by ID."""
    service = get_knowledge_base_service()

    entry = await service.get_entry(entry_id, tenant_id)
    if not entry:
        raise NotFoundError("Knowledge base entry not found")

    return EntryResponse(
        id=str(entry.id),
        title=entry.title,
        content=entry.content,
        category=entry.category,
        tags=entry.tags,
        file_ids=entry.file_ids,
        metadata=entry.metadata_,
        has_embedding=entry.has_embedding,
        created_at=entry.created_at.isoformat(),
        updated_at=entry.updated_at.isoformat(),
    )


@router.patch("/knowledge-base/entries/{entry_id}", response_model=EntryResponse)
async def update_entry(
    entry_id: uuid.UUID,
    body: UpdateEntryRequest,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
) -> EntryResponse:
    """Update a knowledge base entry. Regenerates embedding if content changes."""
    service = get_knowledge_base_service()

    entry = await service.update_entry(
        entry_id=entry_id,
        tenant_id=tenant_id,
        title=body.title,
        content=body.content,
        category=body.category,
        tags=body.tags,
        file_ids=body.file_ids,
    )
    if not entry:
        raise NotFoundError("Knowledge base entry not found")

    return EntryResponse(
        id=str(entry.id),
        title=entry.title,
        content=entry.content,
        category=entry.category,
        tags=entry.tags,
        file_ids=entry.file_ids,
        metadata=entry.metadata_,
        has_embedding=entry.has_embedding,
        created_at=entry.created_at.isoformat(),
        updated_at=entry.updated_at.isoformat(),
    )


@router.delete("/knowledge-base/entries/{entry_id}")
async def delete_entry(
    entry_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
) -> dict:
    """Delete a knowledge base entry from both PostgreSQL and Milvus."""
    service = get_knowledge_base_service()

    deleted = await service.delete_entry(entry_id, tenant_id)
    if not deleted:
        raise NotFoundError("Knowledge base entry not found")

    return {"status": "deleted"}


@router.get("/knowledge-base/export")
async def export_entries(
    format: str = Query("json", pattern="^(json|csv)$"),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
) -> Response:
    """Export all knowledge base entries as JSON or CSV."""
    service = get_knowledge_base_service()

    data = await service.export_entries(tenant_id, format=format)

    if format == "csv":
        return Response(
            content=data,
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename=knowledge_base_{tenant_id}.csv"},
        )
    else:
        return Response(
            content=data,
            media_type="application/json",
            headers={"Content-Disposition": f"attachment; filename=knowledge_base_{tenant_id}.json"},
        )
