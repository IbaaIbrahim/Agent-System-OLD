"""File service for retrieving and processing files."""
import base64
import io
import json
from typing import Any

from libs.common import get_logger
from libs.messaging.redis import get_redis_client

try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None  # Handle missing dependency gracefully

logger = get_logger(__name__)


class FileService:
    """Service for retrieving and processing files."""

    @staticmethod
    async def retrieve_file(file_id: str) -> tuple[bytes, dict[str, Any]]:
        """Retrieve file from Redis.
        
        Args:
            file_id: Unique file identifier
            
        Returns:
            Tuple of (file_data, metadata)
            
        Raises:
            FileNotFoundError: If file not found in Redis
        """
        try:
            redis = await get_redis_client()
            cache_key = f"file:{file_id}"

            # Retrieve from cache
            cached_data = await redis.client.get(cache_key)

            if not cached_data:
                logger.warning(
                    "File not found in Redis (may have expired)",
                    file_id=file_id,
                )
                raise FileNotFoundError(f"File {file_id} not found or expired")

            # Parse JSON payload
            storage_payload = json.loads(cached_data)

            # Decode base64 data
            encoded_data = storage_payload["data"]
            file_data = base64.b64decode(encoded_data)
            
            metadata = storage_payload.get("metadata", {})

            return file_data, metadata

        except Exception as e:
            logger.error(
                "File retrieval failed",
                file_id=file_id,
                error=str(e),
            )
            # Re-raise or return None? better to re-raise to handle upstream
            raise

    @staticmethod
    async def extract_text(file_id: str) -> str:
        """Retrieve file and extract text content.
        
        Args:
            file_id: Unique file identifier
            
        Returns:
            Extracted text content
        """
        try:
            file_data, metadata = await FileService.retrieve_file(file_id)
            content_type = metadata.get("content_type", "")
            filename = metadata.get("filename", "unknown")
            
            logger.info("Extracting text from file", file_id=file_id, content_type=content_type)

            text_content = ""

            if content_type == "application/pdf":
                if PdfReader:
                    try:
                        reader = PdfReader(io.BytesIO(file_data))
                        text_content = f"--- File: {filename} (PDF) ---\n"
                        page_texts = []
                        for i, page in enumerate(reader.pages):
                            page_text = page.extract_text()
                            if page_text:
                                page_texts.append(f"[Page {i+1}]\n{page_text}")
                        text_content += "\n".join(page_texts)
                    except Exception as e:
                        logger.error("PDF extraction failed", error=str(e))
                        text_content = f"--- File: {filename} (PDF) ---\n[Error extracting PDF content: {str(e)}]"
                else:
                    text_content = f"--- File: {filename} (PDF) ---\n[PDF extraction library not installed]"

            elif content_type.startswith("text/") or content_type in ["application/json", "application/xml"]:
                try:
                    # Try utf-8 decoding
                    text = file_data.decode("utf-8")
                    text_content = f"--- File: {filename} ({content_type}) ---\n{text}"
                except UnicodeDecodeError:
                    text_content = f"--- File: {filename} ---\n[Binary content or encoding error]"
            else:
                # Unsupported or binary type
                text_content = f"--- File: {filename} ({content_type}) ---\n[Content extraction not supported for this file type]"
            
            return text_content + "\n--- End of File ---\n"

        except Exception as e:
            logger.error("Text extraction failed", file_id=file_id, error=str(e))
            return f"[Error processing file {file_id}: {str(e)}]"

    @staticmethod
    async def get_content_blocks(file_id: str) -> list[dict[str, Any]]:
        """Retrieve file and get content blocks (text or image).
        
        Args:
            file_id: Unique file identifier
            
        Returns:
            List of content blocks (dicts) compatible with LLMMessage content
        """
        blocks = []
        try:
            # First check metadata to determine type without full fetch if possible?
            # No, we need data for image.
            
            # Since extract_text handles PDF/Text specifics well, we reuse it for non-images
            # But for images we need raw bytes.
            
            # Helper to avoid double fetch would require refactoring extract_text
            # For now, we'll fetch metadata via retrieve_file (which fetches data too)
            
            file_data, metadata = await FileService.retrieve_file(file_id)
            content_type = metadata.get("content_type", "")
            filename = metadata.get("filename", "unknown")
            
            if content_type.startswith("image/"):
                # Image handling
                # Encode base64
                b64_data = base64.b64encode(file_data).decode("utf-8")
                
                # Add label
                blocks.append({
                    "type": "text",
                    "text": f"\n[File: {filename} ({content_type})]\n"
                })
                
                # Add image block
                blocks.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": content_type,
                        "data": b64_data,
                    }
                })
            else:
                # Text/PDF handling
                # We already fetched data, but extract_text will fetch again.
                # To avoid refactoring risk now, let's just use extract_text
                # It's a bit inefficient but safe.
                text = await FileService.extract_text(file_id)
                blocks.append({
                    "type": "text",
                    "text": text
                })
                
        except Exception as e:
            blocks.append({
                "type": "text",
                "text": f"[Error processing file {file_id}: {str(e)}]"
            })
            
        return blocks
