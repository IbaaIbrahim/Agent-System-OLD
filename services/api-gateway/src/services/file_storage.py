"""File storage service using Redis for temporary file uploads."""

import base64
import json
import uuid
from typing import Any

from libs.common import get_logger
from libs.messaging.redis import get_redis_client

logger = get_logger(__name__)

# File cache TTL: 15 minutes (900 seconds)
FILE_CACHE_TTL_SECONDS = 900

# Maximum file sizes (in bytes)
MAX_IMAGE_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB
MAX_PDF_SIZE_BYTES = 25 * 1024 * 1024    # 25 MB
MAX_TEXT_SIZE_BYTES = 5 * 1024 * 1024     # 5 MB


class FileStorageService:
    """Service for storing and retrieving uploaded files in Redis.

    Files are stored temporarily with a 15-minute TTL for immediate use.
    Metadata is persisted to PostgreSQL for audit trail.

    Redis key format: file:{file_id}
    Storage format: JSON with base64-encoded file data and metadata
    """

    @staticmethod
    async def store_file(
        file_data: bytes,
        metadata: dict[str, Any],
    ) -> str:
        """Store file in Redis with TTL.

        Args:
            file_data: Raw file bytes
            metadata: File metadata (filename, content_type, tenant_id, etc.)

        Returns:
            file_id: Unique identifier for the stored file

        Raises:
            ValueError: If file size exceeds limits
        """
        try:
            file_id = str(uuid.uuid4())
            redis = await get_redis_client()

            # Validate file size based on content type
            content_type = metadata.get("content_type", "")
            size_bytes = len(file_data)

            if content_type.startswith("image/"):
                if size_bytes > MAX_IMAGE_SIZE_BYTES:
                    raise ValueError(f"Image size ({size_bytes} bytes) exceeds maximum ({MAX_IMAGE_SIZE_BYTES} bytes)")
            elif content_type == "application/pdf":
                if size_bytes > MAX_PDF_SIZE_BYTES:
                    raise ValueError(f"PDF size ({size_bytes} bytes) exceeds maximum ({MAX_PDF_SIZE_BYTES} bytes)")
            elif content_type.startswith("text/"):
                if size_bytes > MAX_TEXT_SIZE_BYTES:
                    raise ValueError(f"Text file size ({size_bytes} bytes) exceeds maximum ({MAX_TEXT_SIZE_BYTES} bytes)")
            else:
                # Default to image size limit for other types
                if size_bytes > MAX_IMAGE_SIZE_BYTES:
                    raise ValueError(f"File size ({size_bytes} bytes) exceeds maximum ({MAX_IMAGE_SIZE_BYTES} bytes)")

            # Encode file data as base64
            encoded_data = base64.b64encode(file_data).decode("utf-8")

            # Prepare storage payload
            storage_payload = {
                "file_id": file_id,
                "data": encoded_data,
                "metadata": metadata,
                "size_bytes": size_bytes,
            }

            # Serialize to JSON
            cache_key = f"file:{file_id}"
            cache_data = json.dumps(storage_payload)

            # Store with TTL
            await redis.client.set(cache_key, cache_data, ex=FILE_CACHE_TTL_SECONDS)

            logger.info(
                "File stored in Redis",
                file_id=file_id,
                filename=metadata.get("filename"),
                content_type=content_type,
                size_bytes=size_bytes,
                ttl_seconds=FILE_CACHE_TTL_SECONDS,
            )

            return file_id

        except ValueError:
            # Re-raise validation errors
            raise
        except Exception as e:
            logger.error(
                "File storage failed",
                error=str(e),
                metadata=metadata,
            )
            raise ValueError(f"Failed to store file: {e}")

    @staticmethod
    async def retrieve_file(file_id: str) -> tuple[bytes, dict[str, Any]]:
        """Retrieve file from Redis.

        Args:
            file_id: Unique file identifier

        Returns:
            Tuple of (file_data, metadata)

        Raises:
            FileNotFoundError: If file not found in Redis (expired or invalid ID)
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

            metadata = storage_payload["metadata"]

            logger.debug(
                "File retrieved from Redis",
                file_id=file_id,
                filename=metadata.get("filename"),
                size_bytes=storage_payload["size_bytes"],
            )

            return file_data, metadata

        except FileNotFoundError:
            # Re-raise file not found errors
            raise
        except Exception as e:
            logger.error(
                "File retrieval failed",
                file_id=file_id,
                error=str(e),
            )
            raise ValueError(f"Failed to retrieve file: {e}")

    @staticmethod
    async def delete_file(file_id: str) -> None:
        """Delete file from Redis.

        Args:
            file_id: Unique file identifier
        """
        try:
            redis = await get_redis_client()
            cache_key = f"file:{file_id}"

            # Delete from Redis
            await redis.client.delete(cache_key)

            logger.info(
                "File deleted from Redis",
                file_id=file_id,
            )

        except Exception as e:
            # Log error but don't fail - file may already be expired
            logger.warning(
                "File deletion failed (may already be expired)",
                file_id=file_id,
                error=str(e),
            )

    @staticmethod
    async def get_file_metadata(file_id: str) -> dict[str, Any] | None:
        """Get file metadata without retrieving the file data.

        Args:
            file_id: Unique file identifier

        Returns:
            File metadata dict or None if not found
        """
        try:
            redis = await get_redis_client()
            cache_key = f"file:{file_id}"

            # Retrieve from cache
            cached_data = await redis.client.get(cache_key)

            if not cached_data:
                return None

            # Parse JSON payload (but don't decode data)
            storage_payload = json.loads(cached_data)

            return storage_payload["metadata"]

        except Exception as e:
            logger.warning(
                "Failed to get file metadata",
                file_id=file_id,
                error=str(e),
            )
            return None
