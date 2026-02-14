"""Tool for fetching cached file analysis descriptions from the database."""

import sys
from typing import Any

sys.path.insert(0, "services/tool-workers")

from libs.common import get_logger

from .base import BaseTool, catalog_tool

logger = get_logger(__name__)


@catalog_tool("get_file_description")
class GetFileDescriptionTool(BaseTool):
    """Fetch a previously generated file analysis description from the database.

    This tool allows the agent to retrieve cached vision analysis results
    without re-analyzing the file. It checks the file_uploads table for
    an existing analysis_description.
    """

    async def execute(self, arguments: dict[str, Any], context: dict[str, Any]) -> str:
        """Fetch file description from the database.

        Args:
            arguments: Tool arguments (file_id)
            context: Execution context (job_id, tenant_id)

        Returns:
            The cached analysis description or an appropriate error message
        """
        file_id = arguments["file_id"]

        logger.info(
            "Fetching file description",
            file_id=file_id,
            job_id=context.get("job_id"),
        )

        try:
            import uuid as uuid_mod

            from libs.db.models import FileUpload
            from libs.db.session import get_session_context

            try:
                file_uuid = uuid_mod.UUID(file_id)
            except ValueError:
                return f"Error: Invalid file ID format: '{file_id}'. Expected a UUID."

            async with get_session_context() as session:
                file_upload = await session.get(FileUpload, file_uuid)

                if not file_upload:
                    return (
                        f"Error: File '{file_id}' not found in the database. "
                        "The file may not have been uploaded or the ID is incorrect."
                    )

                # Check tenant ownership
                tenant_id = context.get("tenant_id")
                if tenant_id and str(file_upload.tenant_id) != str(tenant_id):
                    return f"Error: File '{file_id}' not found."

                if not file_upload.analysis_description:
                    return (
                        f"File '{file_upload.filename}' (type: {file_upload.content_type}, "
                        f"size: {file_upload.size_bytes} bytes) has not been analyzed yet. "
                        "Use the 'analyze_file' tool with this file_id to generate an analysis."
                    )

                # Return the cached analysis
                analyzed_at = (
                    file_upload.analyzed_at.isoformat()
                    if file_upload.analyzed_at
                    else "unknown"
                )

                return (
                    f"File: {file_upload.filename}\n"
                    f"Type: {file_upload.content_type}\n"
                    f"Size: {file_upload.size_bytes} bytes\n"
                    f"Analyzed at: {analyzed_at}\n\n"
                    f"--- Analysis ---\n{file_upload.analysis_description}"
                )

        except Exception as e:
            logger.error(
                "Failed to fetch file description",
                file_id=file_id,
                error=str(e),
                error_type=type(e).__name__,
            )
            return f"Error fetching file description: {str(e)}"
