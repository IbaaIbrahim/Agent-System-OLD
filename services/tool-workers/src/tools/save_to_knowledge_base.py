"""Save content to knowledge base with embedding generation."""

import uuid
from datetime import datetime, timezone
from typing import Any

from libs.common import get_logger
from libs.common.config import get_settings
from libs.db.models import KnowledgeBaseEntry
from libs.db.session import get_session_context
from libs.embeddings import get_embedder
from libs.vectordb import get_milvus_client

from .base import BaseTool, catalog_tool

logger = get_logger(__name__)


@catalog_tool("save_to_knowledge_base")
class SaveToKnowledgeBaseTool(BaseTool):
    """Save or update knowledge base entries with vector embedding.

    This tool enables the agent to persist information for future retrieval.
    Content is automatically embedded using OpenAI's text-embedding-3-small model.
    """

    async def execute(
        self,
        arguments: dict[str, Any],
        context: dict[str, Any],
    ) -> str:
        """Save entry to knowledge base.

        Args:
            arguments: Entry data (title, content, category, tags, file_ids)
            context: Job context (tenant_id, user_id, job_id)

        Returns:
            Confirmation message with entry ID
        """
        title = arguments.get("title", "")
        content = arguments.get("content", "")
        category = arguments.get("category")
        tags = arguments.get("tags", [])
        file_ids_str = arguments.get("file_ids", [])

        tenant_id = uuid.UUID(context["tenant_id"])
        user_id = uuid.UUID(context["user_id"]) if context.get("user_id") else None

        settings = get_settings()
        if len(content) > settings.kb_max_content_length:
            return f"Content too long ({len(content)} chars). Max: {settings.kb_max_content_length}"

        logger.info(
            "Saving to knowledge base",
            title=title,
            category=category,
            tenant_id=str(tenant_id),
        )

        try:
            # Generate embedding
            embedder = get_embedder()
            embedding = await embedder.embed_text(content)

            # Convert file_ids strings to UUIDs
            file_ids = [uuid.UUID(fid) for fid in file_ids_str] if file_ids_str else []

            # Create PostgreSQL entry
            async with get_session_context() as session:
                entry = KnowledgeBaseEntry(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    title=title,
                    content=content,
                    category=category,
                    tags=tags,
                    file_ids=[str(fid) for fid in file_ids],
                    has_embedding=True,
                    embedding_model=settings.embedding_model,
                    embedding_generated_at=datetime.now(timezone.utc),
                )
                session.add(entry)
                await session.flush()
                await session.refresh(entry)
                entry_id = entry.id

            # Store in Milvus
            milvus = get_milvus_client()
            await milvus.insert(
                tenant_id=tenant_id,
                entry_id=entry_id,
                embedding=embedding,
                category=category,
                tags=tags,
            )

            logger.info(
                "Knowledge base entry saved",
                entry_id=str(entry_id),
                tenant_id=str(tenant_id),
            )

            result = [
                f"Saved to knowledge base: '{title}'",
                f"Entry ID: {entry_id}",
            ]
            if category:
                result.append(f"Category: {category}")
            if tags:
                result.append(f"Tags: {', '.join(tags)}")
            if file_ids:
                result.append(f"Attached files: {len(file_ids)}")

            return "\n".join(result)

        except Exception as e:
            logger.error(
                "Failed to save to knowledge base",
                title=title,
                error=str(e),
                tenant_id=str(tenant_id),
            )
            return f"Failed to save: {str(e)}"
