"""Delete knowledge base entries."""

import uuid
from typing import Any

from libs.common import get_logger
from libs.common.tool_catalog import ToolBehavior
from libs.db.session import get_session_context
from libs.vectordb import get_milvus_client
from sqlalchemy import delete as sql_delete

from .base import BaseTool

logger = get_logger(__name__)


class DeleteFromKnowledgeBaseTool(BaseTool):
    """Delete knowledge base entries by ID.

    Permanently removes entries from both PostgreSQL and Milvus.
    Requires user confirmation due to destructive nature.
    """

    name = "delete_from_knowledge_base"
    description = (
        "Delete an entry from the knowledge base. Use this when the user asks to "
        "remove or forget saved information. Requires the entry ID."
    )
    parameters = {
        "type": "object",
        "properties": {
            "entry_id": {
                "type": "string",
                "description": "UUID of the knowledge base entry to delete",
            },
        },
        "required": ["entry_id"],
    }
    behavior = ToolBehavior.CONFIRM_REQUIRED
    required_plan_feature = None

    async def execute(
        self,
        arguments: dict[str, Any],
        context: dict[str, Any],
    ) -> str:
        """Delete entry from knowledge base.

        Args:
            arguments: Entry ID to delete
            context: Job context (tenant_id, user_id, job_id)

        Returns:
            Confirmation message
        """
        entry_id_str = arguments.get("entry_id", "")
        tenant_id = uuid.UUID(context["tenant_id"])

        try:
            entry_id = uuid.UUID(entry_id_str)
        except ValueError:
            return f"Invalid entry ID format: {entry_id_str}"

        logger.info(
            "Deleting from knowledge base",
            entry_id=str(entry_id),
            tenant_id=str(tenant_id),
        )

        try:
            # Delete from PostgreSQL
            async with get_session_context() as session:
                from libs.db.models import KnowledgeBaseEntry
                stmt = sql_delete(KnowledgeBaseEntry).where(
                    KnowledgeBaseEntry.id == entry_id,
                    KnowledgeBaseEntry.tenant_id == tenant_id,
                )
                result = await session.execute(stmt)
                deleted = result.rowcount > 0

            if not deleted:
                return f"Entry not found: {entry_id}"

            # Delete from Milvus
            milvus = get_milvus_client()
            await milvus.delete(tenant_id=tenant_id, entry_id=entry_id)

            logger.info(
                "Knowledge base entry deleted",
                entry_id=str(entry_id),
                tenant_id=str(tenant_id),
            )

            return f"Deleted knowledge base entry: {entry_id}"

        except Exception as e:
            logger.error(
                "Failed to delete from knowledge base",
                entry_id=str(entry_id),
                error=str(e),
                tenant_id=str(tenant_id),
            )
            return f"Failed to delete: {str(e)}"
