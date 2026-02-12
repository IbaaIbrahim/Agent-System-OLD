"""Screen frame processor — resize and upload for vision analysis."""

import base64
import io
import uuid
from datetime import UTC, datetime

from libs.common import get_logger
from libs.db.models import FileUpload
from libs.db.session import get_session_context
from libs.messaging.redis import get_redis_client

from ..config import get_config

logger = get_logger(__name__)


class FrameProcessor:
    """Processes screen frames: resize, store, return file_id."""

    async def process_frame(
        self,
        frame_base64: str,
        tenant_id: str,
        user_id: str | None,
        job_id: str | None = None,
    ) -> str:
        """Process a screen frame and return a file_id for vision analysis.

        Args:
            frame_base64: Base64-encoded PNG image
            tenant_id: Tenant ID
            user_id: User ID
            job_id: Optional job ID to associate

        Returns:
            file_id (UUID string) for the uploaded frame
        """
        config = get_config()

        # Decode and resize
        image_bytes = base64.b64decode(frame_base64)
        resized_bytes = await self._resize_image(
            image_bytes,
            max_width=config.screen_frame_max_width,
            max_height=config.screen_frame_max_height,
        )

        # Generate file ID and store
        file_id = str(uuid.uuid4())
        storage_key = f"file:{file_id}"

        # Store in Redis (hot storage, 15 min TTL)
        redis = await get_redis_client()
        await redis.set(storage_key, resized_bytes, ex=900)

        # Persist metadata to DB
        async with get_session_context() as session:
            file_upload = FileUpload(
                id=uuid.UUID(file_id),
                tenant_id=uuid.UUID(tenant_id),
                user_id=uuid.UUID(user_id) if user_id else None,
                job_id=uuid.UUID(job_id) if job_id else None,
                filename=f"screen_frame_{datetime.now(UTC).strftime('%H%M%S')}.png",
                content_type="image/png",
                size_bytes=len(resized_bytes),
                storage_key=storage_key,
                metadata_={"source": "live_screen_capture"},
            )
            session.add(file_upload)
            await session.flush()

        logger.debug(
            "Screen frame processed",
            file_id=file_id,
            original_size=len(image_bytes),
            resized_size=len(resized_bytes),
        )

        return file_id

    async def _resize_image(
        self,
        image_bytes: bytes,
        max_width: int = 720,
        max_height: int = 512,
    ) -> bytes:
        """Resize image to fit within max dimensions while maintaining aspect ratio."""
        try:
            from PIL import Image

            img = Image.open(io.BytesIO(image_bytes))

            # Calculate new size maintaining aspect ratio
            ratio = min(max_width / img.width, max_height / img.height)
            if ratio < 1:
                new_size = (int(img.width * ratio), int(img.height * ratio))
                img = img.resize(new_size, Image.Resampling.LANCZOS)

            # Convert back to PNG bytes
            output = io.BytesIO()
            img.save(output, format="PNG", optimize=True)
            return output.getvalue()

        except ImportError:
            logger.warning("Pillow not installed, returning original image")
            return image_bytes
