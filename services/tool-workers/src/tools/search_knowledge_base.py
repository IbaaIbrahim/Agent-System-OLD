"""Search knowledge base tool using vector similarity."""

import uuid
from typing import Any

from libs.common import get_logger
from libs.common.tool_catalog import ToolBehavior
from libs.db.models import KnowledgeBaseEntry
from libs.db.session import get_session_context
from libs.embeddings import get_embedder
from libs.vectordb import get_milvus_client
from sqlalchemy import select

from .base import BaseTool

logger = get_logger(__name__)


class SearchKnowledgeBaseTool(BaseTool):
    """Semantic search across knowledge base entries.

    Uses Milvus vector search for semantic similarity matching.
    Results are retrieved from PostgreSQL for full content display.
    """

    name = "search_knowledge_base"
    description = (
        "Search the knowledge base using semantic similarity. Use this when the user "
        "asks about previously saved information, documented procedures, or references "
        "specific topics that may have been stored. Returns relevant entries with content, "
        "categories, and associated files."
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Natural language search query",
            },
            "category": {
                "type": "string",
                "description": "Optional category filter to narrow results",
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional tags to filter results (matches if any tag overlaps)",
            },
            "top_k": {
                "type": "integer",
                "description": "Number of results to return (default: 5, max: 20)",
                "default": 5,
            },
        },
        "required": ["query"],
    }
    behavior = ToolBehavior.AUTO_EXECUTE
    required_plan_feature = None

    async def execute(
        self,
        arguments: dict[str, Any],
        context: dict[str, Any],
    ) -> str:
        """Execute semantic search.

        Args:
            arguments: Search parameters (query, category, tags, top_k)
            context: Job context (tenant_id, user_id, job_id)

        Returns:
            Formatted search results
        """
        query = arguments.get("query", "")
        category = arguments.get("category")
        tags = arguments.get("tags")
        top_k = min(arguments.get("top_k", 5), 20)
        tenant_id = uuid.UUID(context["tenant_id"])

        logger.info(
            "Searching knowledge base",
            query=query,
            category=category,
            tags=tags,
            top_k=top_k,
            tenant_id=str(tenant_id),
        )

        try:
            # Generate query embedding
            embedder = get_embedder()
            query_embedding = await embedder.embed_text(query)

            # Search Milvus
            milvus = get_milvus_client()
            vector_results = await milvus.search(
                tenant_id=tenant_id,
                query_embedding=query_embedding,
                top_k=top_k,
                category=category,
                tags=tags,
            )

            if not vector_results:
                return f"No knowledge base entries found for query: '{query}'"

            # Fetch full entry details from PostgreSQL
            entry_ids = [uuid.UUID(r["entry_id"]) for r in vector_results]
            async with get_session_context() as session:
                stmt = select(KnowledgeBaseEntry).where(
                    KnowledgeBaseEntry.id.in_(entry_ids),
                    KnowledgeBaseEntry.tenant_id == tenant_id,
                )
                result = await session.execute(stmt)
                entries_dict = {str(e.id): e for e in result.scalars().all()}

            # Format results
            lines = [f"# Knowledge Base Search Results for: {query}\n"]
            lines.append(f"Found {len(vector_results)} relevant entries:\n")

            for i, vr in enumerate(vector_results, 1):
                entry = entries_dict.get(vr["entry_id"])
                if not entry:
                    continue

                similarity_pct = int(vr["distance"] * 100)
                lines.append(f"## {i}. {entry.title} ({similarity_pct}% match)")
                if entry.category:
                    lines.append(f"**Category:** {entry.category}")
                if entry.tags:
                    lines.append(f"**Tags:** {', '.join(entry.tags)}")
                lines.append(f"\n{entry.content[:500]}...")
                if entry.file_ids:
                    lines.append(f"**Attached files:** {len(entry.file_ids)} file(s)")
                lines.append("")

            return "\n".join(lines)

        except Exception as e:
            logger.error(
                "Knowledge base search failed",
                query=query,
                error=str(e),
                tenant_id=str(tenant_id),
            )
            return f"Search failed: {str(e)}"
