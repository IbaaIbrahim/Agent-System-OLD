"""File analysis tool using vision models for image analysis."""

import sys
from typing import Any

# Add parent directory to path for imports
sys.path.insert(0, "services/tool-workers")

from libs.common import get_logger
from libs.common.tool_catalog import ToolBehavior
from libs.llm import LLMMessage, MessageRole, get_provider
from libs.messaging.redis import get_redis_client

from .base import BaseTool

logger = get_logger(__name__)


class AnalyzeFileTool(BaseTool):
    """Analyze uploaded files using vision models.

    Supports:
    - Images: JPEG, PNG, GIF, WebP (uses vision models)
    - PDFs: Text extraction and analysis (future: OCR)
    - Text files: Direct content analysis
    """

    name = "analyze_file"
    description = (
        "Analyze uploaded files (images, PDFs, documents) using vision models. "
        "Use this to extract information, generate checklists, summarize content, "
        "or answer questions about uploaded files."
    )
    parameters = {
        "type": "object",
        "properties": {
            "file_id": {
                "type": "string",
                "description": "ID of the uploaded file to analyze",
            },
            "query": {
                "type": "string",
                "description": (
                    "What to analyze or extract from the file. "
                    "Examples: 'extract checklist items', 'summarize this document', "
                    "'what does this diagram show?', 'generate a todo list from this image'"
                ),
            },
        },
        "required": ["file_id", "query"],
    }
    behavior = ToolBehavior.CONFIRM_REQUIRED
    required_plan_feature = "tools.file_analysis"

    async def execute(self, arguments: dict[str, Any], context: dict[str, Any]) -> str:
        """Execute file analysis using vision models.

        Args:
            arguments: Tool arguments (file_id, query)
            context: Execution context (job_id, tenant_id)

        Returns:
            Analysis result as string
        """
        file_id = arguments["file_id"]
        query = arguments["query"]

        logger.info(
            "Analyzing file",
            file_id=file_id,
            query=query,
            job_id=context.get("job_id"),
        )

        try:
            # Retrieve file from Redis
            redis = await get_redis_client()
            cache_key = f"file:{file_id}"

            cached_data = await redis.client.get(cache_key)

            if not cached_data:
                logger.warning(
                    "File not found in Redis (may have expired)",
                    file_id=file_id,
                )
                return (
                    f"Error: File {file_id} not found or expired. "
                    "Files are only available for 15 minutes after upload. "
                    "Please re-upload the file and try again."
                )

            # Parse file data
            import json
            import base64

            storage_payload = json.loads(cached_data)
            encoded_data = storage_payload["data"]
            metadata = storage_payload["metadata"]

            # Decode base64 data
            file_data_bytes = base64.b64decode(encoded_data)

            content_type = metadata.get("content_type", "")
            filename = metadata.get("filename", "unknown")

            logger.info(
                "File retrieved from Redis",
                file_id=file_id,
                filename=filename,
                content_type=content_type,
                size_bytes=len(file_data_bytes),
            )

            # Route based on content type
            if content_type.startswith("image/"):
                # Use vision model for image analysis
                return await self._analyze_image(
                    file_data_bytes, content_type, query, filename
                )
            elif content_type == "application/pdf":
                # Future: Add PDF processing with OCR
                return await self._analyze_pdf(file_data_bytes, query, filename)
            elif content_type.startswith("text/"):
                # Analyze text files
                return await self._analyze_text(file_data_bytes, query, filename)
            else:
                return f"Error: Unsupported file type '{content_type}' for analysis."

        except Exception as e:
            logger.error(
                "File analysis failed",
                file_id=file_id,
                error=str(e),
                error_type=type(e).__name__,
            )
            return f"Error analyzing file: {str(e)}"

    async def _analyze_image(
        self, file_data: bytes, content_type: str, query: str, filename: str
    ) -> str:
        """Analyze image using vision model.

        Args:
            file_data: Raw image bytes
            content_type: MIME type (e.g., image/jpeg)
            query: Analysis query from user
            filename: Original filename

        Returns:
            Analysis result
        """
        import base64

        # Re-encode to base64 for LLM
        encoded_data = base64.b64encode(file_data).decode("utf-8")

        # Use Anthropic Claude with vision
        provider = get_provider("anthropic")

        # Build vision message
        message = LLMMessage(
            role=MessageRole.USER,
            content=[
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": content_type,
                        "data": encoded_data,
                    },
                },
                {
                    "type": "text",
                    "text": f"Analyze this image (filename: {filename}).\n\nTask: {query}\n\nProvide a detailed, structured response.",
                },
            ],
        )

        logger.info(
            "Calling vision model for image analysis",
            filename=filename,
            content_type=content_type,
            query_length=len(query),
        )

        # Call vision model
        response = await provider.complete(
            [message],
            model="claude-3-5-sonnet-20241022",  # Vision-capable model
            max_tokens=4096,
        )

        logger.info(
            "Vision model analysis complete",
            filename=filename,
            output_tokens=response.output_tokens,
        )

        return response.content or "No analysis generated."

    async def _analyze_pdf(self, file_data: bytes, query: str, filename: str) -> str:
        """Analyze PDF file.

        Future enhancement: Add OCR for scanned PDFs.

        Args:
            file_data: Raw PDF bytes
            query: Analysis query
            filename: Original filename

        Returns:
            Analysis result
        """
        # Placeholder for PDF analysis
        # In production, you'd use libraries like PyPDF2, pdfplumber, or Tesseract OCR
        return (
            f"PDF analysis is not yet fully implemented for '{filename}'. "
            "Future versions will support text extraction and OCR for scanned documents. "
            "For now, please convert PDFs to images for analysis."
        )

    async def _analyze_text(self, file_data: bytes, query: str, filename: str) -> str:
        """Analyze text file.

        Args:
            file_data: Raw text bytes
            query: Analysis query
            filename: Original filename

        Returns:
            Analysis result
        """
        # Decode text content
        try:
            text_content = file_data.decode("utf-8")
        except UnicodeDecodeError:
            # Try other encodings
            try:
                text_content = file_data.decode("latin-1")
            except UnicodeDecodeError:
                return f"Error: Unable to decode text file '{filename}'. File may be corrupted or in an unsupported encoding."

        # Use standard LLM for text analysis
        provider = get_provider("anthropic")

        message = LLMMessage(
            role=MessageRole.USER,
            content=f"Analyze this text file (filename: {filename}).\n\nFile content:\n{text_content}\n\nTask: {query}\n\nProvide a detailed, structured response.",
        )

        logger.info(
            "Calling LLM for text file analysis",
            filename=filename,
            content_length=len(text_content),
        )

        response = await provider.complete(
            [message],
            model="claude-sonnet-4-20250514",  # Standard text model
            max_tokens=4096,
        )

        return response.content or "No analysis generated."
