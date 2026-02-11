"""Knowledge base service for CRUD operations."""

import csv
import io
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, select, update

from libs.common import get_logger
from libs.db.models import KnowledgeBaseEntry
from libs.db.session import get_session_context
from libs.embeddings import get_embedder
from libs.vectordb import get_milvus_client

logger = get_logger(__name__)


class KnowledgeBaseService:
    """Service for knowledge base CRUD and export operations.

    Provides dual-storage persistence:
    - PostgreSQL: Source of truth for metadata, content, CRUD operations
    - Milvus: Vector embeddings for semantic search

    Supports export to JSON/CSV formats.
    """

    async def create_entry(
        self,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID | None,
        title: str,
        content: str,
        category: str | None = None,
        tags: list[str] | None = None,
        file_ids: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> KnowledgeBaseEntry:
        """Create a new knowledge base entry with embedding.

        Args:
            tenant_id: Tenant UUID
            user_id: User UUID who created the entry
            title: Entry title
            content: Entry content
            category: Optional category
            tags: Optional tags
            file_ids: Optional file UUIDs to attach
            metadata: Optional additional metadata

        Returns:
            Created KnowledgeBaseEntry
        """
        # Generate embedding
        embedder = get_embedder()
        embedding = await embedder.embed_text(content)

        async with get_session_context() as session:
            entry = KnowledgeBaseEntry(
                tenant_id=tenant_id,
                user_id=user_id,
                title=title,
                content=content,
                category=category,
                tags=tags or [],
                file_ids=file_ids or [],
                metadata_=metadata or {},
                has_embedding=True,
                embedding_model="text-embedding-3-small",
                embedding_generated_at=datetime.now(timezone.utc),
            )
            session.add(entry)
            await session.flush()
            await session.refresh(entry)

            # Store in Milvus
            milvus = get_milvus_client()
            await milvus.insert(
                tenant_id=tenant_id,
                entry_id=entry.id,
                embedding=embedding,
                category=category,
                tags=tags or [],
            )

            logger.info(
                "Knowledge base entry created",
                entry_id=str(entry.id),
                tenant_id=str(tenant_id),
            )
            return entry

    async def get_entry(
        self,
        entry_id: uuid.UUID,
        tenant_id: uuid.UUID,
    ) -> KnowledgeBaseEntry | None:
        """Get a single entry by ID.

        Args:
            entry_id: Entry UUID
            tenant_id: Tenant UUID (for access control)

        Returns:
            KnowledgeBaseEntry or None
        """
        async with get_session_context() as session:
            stmt = select(KnowledgeBaseEntry).where(
                KnowledgeBaseEntry.id == entry_id,
                KnowledgeBaseEntry.tenant_id == tenant_id,
            )
            result = await session.execute(stmt)
            return result.scalar_one_or_none()

    async def list_entries(
        self,
        tenant_id: uuid.UUID,
        category: str | None = None,
        tags: list[str] | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> tuple[list[KnowledgeBaseEntry], int]:
        """List entries with optional filtering.

        Args:
            tenant_id: Tenant UUID
            category: Optional category filter
            tags: Optional tag filter (matches if any overlap)
            offset: Pagination offset
            limit: Max results to return

        Returns:
            Tuple of (entries list, total count)
        """
        async with get_session_context() as session:
            filters = [KnowledgeBaseEntry.tenant_id == tenant_id]

            if category:
                filters.append(KnowledgeBaseEntry.category == category)

            if tags:
                filters.append(KnowledgeBaseEntry.tags.op("&&")(tags))

            # Count total
            count_stmt = select(KnowledgeBaseEntry.id).where(*filters)
            count_result = await session.execute(count_stmt)
            total = len(count_result.all())

            # Fetch paginated results
            stmt = (
                select(KnowledgeBaseEntry)
                .where(*filters)
                .order_by(KnowledgeBaseEntry.created_at.desc())
                .offset(offset)
                .limit(limit)
            )
            result = await session.execute(stmt)
            entries = list(result.scalars().all())

            return entries, total

    async def update_entry(
        self,
        entry_id: uuid.UUID,
        tenant_id: uuid.UUID,
        title: str | None = None,
        content: str | None = None,
        category: str | None = None,
        tags: list[str] | None = None,
        file_ids: list[str] | None = None,
    ) -> KnowledgeBaseEntry | None:
        """Update an existing entry and regenerate embedding if content changed.

        Args:
            entry_id: Entry UUID to update
            tenant_id: Tenant UUID
            title: Optional new title
            content: Optional new content (triggers re-embedding)
            category: Optional new category
            tags: Optional new tags
            file_ids: Optional new file IDs

        Returns:
            Updated KnowledgeBaseEntry or None
        """
        async with get_session_context() as session:
            # Fetch existing entry
            stmt = select(KnowledgeBaseEntry).where(
                KnowledgeBaseEntry.id == entry_id,
                KnowledgeBaseEntry.tenant_id == tenant_id,
            )
            result = await session.execute(stmt)
            entry = result.scalar_one_or_none()
            if not entry:
                return None

            update_values = {}
            if title is not None:
                update_values["title"] = title
            if content is not None:
                update_values["content"] = content
            if category is not None:
                update_values["category"] = category
            if tags is not None:
                update_values["tags"] = tags
            if file_ids is not None:
                update_values["file_ids"] = file_ids

            # If content changed, regenerate embedding
            if content is not None and content != entry.content:
                embedder = get_embedder()
                embedding = await embedder.embed_text(content)
                update_values["embedding_generated_at"] = datetime.now(timezone.utc)

                # Update Milvus
                milvus = get_milvus_client()
                await milvus.delete(tenant_id=tenant_id, entry_id=entry_id)
                await milvus.insert(
                    tenant_id=tenant_id,
                    entry_id=entry_id,
                    embedding=embedding,
                    category=category if category is not None else entry.category,
                    tags=tags if tags is not None else entry.tags,
                )

            # Update PostgreSQL
            update_stmt = (
                update(KnowledgeBaseEntry)
                .where(
                    KnowledgeBaseEntry.id == entry_id,
                    KnowledgeBaseEntry.tenant_id == tenant_id,
                )
                .values(**update_values)
                .returning(KnowledgeBaseEntry)
            )
            result = await session.execute(update_stmt)
            updated = result.scalar_one_or_none()

            logger.info(
                "Knowledge base entry updated",
                entry_id=str(entry_id),
                tenant_id=str(tenant_id),
            )
            return updated

    async def delete_entry(
        self,
        entry_id: uuid.UUID,
        tenant_id: uuid.UUID,
    ) -> bool:
        """Delete an entry from both PostgreSQL and Milvus.

        Args:
            entry_id: Entry UUID to delete
            tenant_id: Tenant UUID

        Returns:
            True if deleted, False if not found
        """
        async with get_session_context() as session:
            stmt = delete(KnowledgeBaseEntry).where(
                KnowledgeBaseEntry.id == entry_id,
                KnowledgeBaseEntry.tenant_id == tenant_id,
            )
            result = await session.execute(stmt)
            deleted = result.rowcount > 0

            if deleted:
                # Delete from Milvus
                milvus = get_milvus_client()
                await milvus.delete(tenant_id=tenant_id, entry_id=entry_id)

                logger.info(
                    "Knowledge base entry deleted",
                    entry_id=str(entry_id),
                    tenant_id=str(tenant_id),
                )

            return deleted

    async def export_entries(
        self,
        tenant_id: uuid.UUID,
        format: str = "json",
    ) -> str:
        """Export all entries for a tenant as JSON or CSV.

        Args:
            tenant_id: Tenant UUID
            format: Export format ("json" or "csv")

        Returns:
            Serialized export data
        """
        async with get_session_context() as session:
            stmt = (
                select(KnowledgeBaseEntry)
                .where(KnowledgeBaseEntry.tenant_id == tenant_id)
                .order_by(KnowledgeBaseEntry.created_at.asc())
            )
            result = await session.execute(stmt)
            entries = list(result.scalars().all())

            if format == "csv":
                return self._export_csv(entries)
            else:
                return self._export_json(entries)

    def _export_json(self, entries: list[KnowledgeBaseEntry]) -> str:
        """Export entries as JSON.

        Args:
            entries: List of KnowledgeBaseEntry objects

        Returns:
            JSON string
        """
        data = []
        for entry in entries:
            data.append({
                "id": str(entry.id),
                "title": entry.title,
                "content": entry.content,
                "category": entry.category,
                "tags": entry.tags,
                "file_ids": entry.file_ids,
                "metadata": entry.metadata_,
                "created_at": entry.created_at.isoformat(),
                "updated_at": entry.updated_at.isoformat(),
            })
        return json.dumps(data, indent=2)

    def _export_csv(self, entries: list[KnowledgeBaseEntry]) -> str:
        """Export entries as CSV.

        Args:
            entries: List of KnowledgeBaseEntry objects

        Returns:
            CSV string
        """
        output = io.StringIO()
        writer = csv.DictWriter(
            output,
            fieldnames=["id", "title", "content", "category", "tags", "file_ids", "created_at"],
        )
        writer.writeheader()

        for entry in entries:
            writer.writerow({
                "id": str(entry.id),
                "title": entry.title,
                "content": entry.content,
                "category": entry.category or "",
                "tags": ",".join(entry.tags),
                "file_ids": ",".join(entry.file_ids),
                "created_at": entry.created_at.isoformat(),
            })

        return output.getvalue()


def get_knowledge_base_service() -> KnowledgeBaseService:
    """Factory for KnowledgeBaseService.

    Returns:
        New KnowledgeBaseService instance
    """
    return KnowledgeBaseService()
