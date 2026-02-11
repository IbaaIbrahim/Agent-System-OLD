"""File analysis tool using vision models for image analysis."""

import sys
from datetime import UTC, datetime
from typing import Any

# Add parent directory to path for imports
sys.path.insert(0, "services/tool-workers")

from libs.common import get_logger
from libs.common.tool_catalog import ToolBehavior, get_tool_metadata, get_tool_model_preference
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

    # Load configuration from Tool Catalog to avoid duplication
    _meta = get_tool_metadata("analyze_file")
    if not _meta:
        # Fallback if catalog not loaded (should not happen in app)
        raise ValueError("Tool definition for 'analyze_file' not found in catalog")

    name = _meta.name
    description = _meta.description
    parameters = _meta.parameters
    behavior = _meta.behavior
    required_plan_feature = _meta.required_plan_feature

    async def execute(self, arguments: dict[str, Any], context: dict[str, Any]) -> str:
        """Execute file analysis using vision models.

        Flow:
        1. Validate file_id format (reject hallucinated IDs early)
        2. Check DB for cached analysis → return immediately if found
        3. File exists but not analyzed → try Redis for file data → analyze → cache
        4. File not in DB → clear error

        Args:
            arguments: Tool arguments (file_id, query)
            context: Execution context (job_id, tenant_id)

        Returns:
            Analysis result as string
        """
        import uuid as uuid_mod

        file_id = arguments["file_id"]
        query = arguments["query"]

        logger.info(
            "Analyzing file",
            file_id=file_id,
            query=query,
            job_id=context.get("job_id"),
        )

        # Step 1: Validate UUID format — catch hallucinated IDs like "image_id"
        try:
            uuid_mod.UUID(file_id)
        except ValueError:
            return (
                f"Error: Invalid file ID '{file_id}'. "
                "Expected a UUID (e.g., 'd0908dde-37e7-4441-bbc9-8d7f27a736c4'). "
                "Use the actual file_id from a previous upload or screenshot."
            )

        # Step 2: Check DB for existing cached analysis
        file_record = await self._get_file_record(file_id)

        if file_record and file_record.analysis_description:
            logger.info(
                "Returning cached analysis from database",
                file_id=file_id,
                analyzed_at=str(file_record.analyzed_at),
            )
            return file_record.analysis_description

        # Step 3: No cached analysis — try to get file data from Redis
        try:
            redis = await get_redis_client()
            cache_key = f"file:{file_id}"
            cached_data = await redis.client.get(cache_key)

            if not cached_data:
                if file_record:
                    return (
                        f"File '{file_record.filename}' (type: {file_record.content_type}) "
                        "exists in the database but its data has expired from the cache. "
                        "The file has not been analyzed yet. Please ask the user to re-upload "
                        "the file so it can be analyzed."
                    )
                else:
                    return (
                        f"Error: File '{file_id}' not found. "
                        "No file with this ID exists in the database or cache. "
                        "Please verify the file_id is correct."
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
                result = await self._analyze_image(
                    file_data_bytes, content_type, query, filename
                )
            elif content_type == "application/pdf":
                result = await self._analyze_pdf(file_data_bytes, query, filename)
            elif content_type.startswith("text/"):
                result = await self._analyze_text(file_data_bytes, query, filename)
            else:
                return f"Error: Unsupported file type '{content_type}' for analysis."

            # Store analysis result in database for later retrieval
            await self._save_analysis_to_db(file_id, result)

            return result

        except Exception as e:
            logger.error(
                "File analysis failed",
                file_id=file_id,
                error=str(e),
                error_type=type(e).__name__,
            )
            return f"Error analyzing file: {str(e)}"

    async def _get_file_record(self, file_id: str):
        """Load the FileUpload record from the database.

        Args:
            file_id: UUID string of the file

        Returns:
            FileUpload model instance or None
        """
        try:
            import uuid as uuid_mod

            from libs.db.models import FileUpload
            from libs.db.session import get_session_context

            file_uuid = uuid_mod.UUID(file_id)

            async with get_session_context() as session:
                return await session.get(FileUpload, file_uuid)

        except Exception as e:
            logger.warning(
                "Failed to check database for file record",
                file_id=file_id,
                error=str(e),
            )
            return None

    async def _save_analysis_to_db(self, file_id: str, analysis: str) -> None:
        """Save analysis result to the file_uploads table for later retrieval.

        Args:
            file_id: UUID of the file
            analysis: The analysis text to store
        """
        try:
            import uuid as uuid_mod

            from sqlalchemy import update

            from libs.db.models import FileUpload
            from libs.db.session import get_session_context

            file_uuid = uuid_mod.UUID(file_id)

            async with get_session_context() as session:
                stmt = (
                    update(FileUpload)
                    .where(FileUpload.id == file_uuid)
                    .values(
                        analysis_description=analysis,
                        analyzed_at=datetime.now(UTC),
                    )
                )
                await session.execute(stmt)
                await session.commit()

            logger.info(
                "Analysis saved to database",
                file_id=file_id,
                analysis_length=len(analysis),
            )
        except Exception as e:
            # Don't fail the tool if DB save fails — the analysis was still returned
            logger.error(
                "Failed to save analysis to database",
                file_id=file_id,
                error=str(e),
            )

    async def _analyze_image(
        self, file_data: bytes, content_type: str, query: str, filename: str
    ) -> str:
        """Analyze image using vision model with exhaustive detail.

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

        # Use preferred provider/model from catalog
        provider_name, model_name = get_tool_model_preference("analyze_file")
        provider = get_provider(provider_name)

        # Build vision message with DETAILED description prompt
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
                    "text": (
                        f"Analyze this image (filename: {filename}).\n\n"
                        f"Task: {query}\n\n"
                        "IMPORTANT: Provide an exhaustive, highly detailed description. "
                        "Do NOT summarize or abbreviate. Describe every visible element, "
                        "text, color, layout, structure, icon, label, and spatial relationship "
                        "in full detail. If there is text in the image, transcribe it exactly. "
                        "If this is a UI screenshot, describe every component, its state, "
                        "positioning, and content. Do not omit anything."
                    ),
                },
            ],
        )

        logger.info(
            "Calling vision model for image analysis",
            filename=filename,
            content_type=content_type,
            query_length=len(query),
        )

        response = await provider.complete(
            [message],
            model=model_name,
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

        Args:
            file_data: Raw PDF bytes
            query: Analysis query
            filename: Original filename

        Returns:
            Analysis result
        """
        try:
            import io
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(file_data))
            text_content = ""
            page_count = len(reader.pages)

            logger.info("Extracting text from PDF", filename=filename, pages=page_count)

            for i, page in enumerate(reader.pages):
                page_text = page.extract_text()
                if page_text:
                    text_content += f"\n--- Page {i+1} ---\n{page_text}"

            if not text_content.strip():
                return (
                    f"Analysis Warning: No text could be extracted from '{filename}'. "
                    "The PDF might be a scanned image without OCR text layer. "
                    "Please convert it to an image format (PNG/JPEG) for visual analysis."
                )

            provider_name, model = get_tool_model_preference("analyze_file")
            provider = get_provider(provider_name)

            message = LLMMessage(
                role=MessageRole.USER,
                content=(
                    f"Analyze this PDF document (filename: {filename}).\n\n"
                    f"Task: {query}\n\n"
                    f"Document Content ({page_count} pages):\n{text_content}"
                ),
            )

            logger.info(
                "Calling LLM for PDF analysis",
                filename=filename,
                content_length=len(text_content),
                model=model,
            )

            response = await provider.complete(
                [message],
                model=model,
                max_tokens=4096,
            )

            return response.content or "No analysis generated."

        except ImportError:
            return "Error: PDF analysis dependency (pypdf) is missing."
        except Exception as e:
            logger.error("PDF analysis failed", error=str(e))
            return f"Error analyzing PDF: {str(e)}"

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
            try:
                text_content = file_data.decode("latin-1")
            except UnicodeDecodeError:
                return (
                    f"Error: Unable to decode text file '{filename}'. "
                    "File may be corrupted or in an unsupported encoding."
                )

        # Use proper provider/model from catalog
        provider_name, model = get_tool_model_preference("analyze_file")
        provider = get_provider(provider_name)

        message = LLMMessage(
            role=MessageRole.USER,
            content=(
                f"Analyze this text file (filename: {filename}).\n\n"
                f"Task: {query}\n\n"
                f"File Content:\n{text_content}"
            ),
        )

        logger.info(
            "Calling LLM for text file analysis",
            filename=filename,
            content_length=len(text_content),
            model=model,
        )

        response = await provider.complete(
            [message],
            model=model,
            max_tokens=4096,
        )

        return response.content or "No analysis generated."
