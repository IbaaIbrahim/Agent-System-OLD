"""File upload and management endpoints."""

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel

from libs.common import get_logger
from libs.common.auth import create_file_download_ott, verify_file_download_ott
from libs.common.exceptions import AuthenticationError
from libs.db.models import FileUpload as FileUploadModel
from libs.db.session import get_session_context

from ..middleware.tenant import get_tenant_id, get_user_id
from ..services.file_storage import FileStorageService

logger = get_logger(__name__)

router = APIRouter()


def get_download_tenant_id(
    request: Request,
    file_id: str,
    token: str | None = Query(None),
) -> uuid.UUID:
    """Resolve tenant_id from one-time token (query) or Bearer auth."""
    if token:
        try:
            payload = verify_file_download_ott(token)
            if payload.file_id != file_id:
                raise HTTPException(
                    status_code=400,
                    detail="Token does not match file",
                )
            return uuid.UUID(payload.tenant_id)
        except AuthenticationError as e:
            raise HTTPException(
                status_code=401,
                detail=e.message or "Invalid or expired download link",
            )
    return get_tenant_id(request)


class FileUploadResponse(BaseModel):
    """Response from file upload endpoint."""

    file_id: str
    filename: str
    content_type: str
    size_bytes: int
    created_at: str


class FileMetadataResponse(BaseModel):
    """Response with file metadata."""

    file_id: str
    filename: str
    content_type: str
    size_bytes: int
    created_at: str
    job_id: str | None = None


class FileDownloadUrlResponse(BaseModel):
    """Response with a short-lived openable download URL."""

    url: str


# Allowed MIME types for file uploads
ALLOWED_MIME_TYPES = [
    # Images
    "image/jpeg",
    "image/jpg",
    "image/png",
    "image/gif",
    "image/webp",
    # Documents
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",  # .docx
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",  # .xlsx
    # Text
    "text/plain",
    "text/markdown",
    "text/csv",
]


@router.post("/upload", response_model=FileUploadResponse)
async def upload_file(
    file: UploadFile = File(...),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    user_id: uuid.UUID | None = Depends(get_user_id),
) -> FileUploadResponse:
    """Upload a file for later analysis.

    Files are stored temporarily in Redis with a 15-minute TTL.
    Metadata is persisted to PostgreSQL for audit trail.

    Allowed file types:
    - Images: JPEG, PNG, GIF, WebP (max 10MB)
    - Documents: PDF, Word (.docx), Excel (.xlsx) (max 25MB)
    - Text: TXT, Markdown, CSV (max 5MB)
    """
    logger.info(
        "File upload request",
        tenant_id=str(tenant_id),
        user_id=str(user_id) if user_id else None,
        filename=file.filename,
        content_type=file.content_type,
    )

    # Validate content type
    if file.content_type not in ALLOWED_MIME_TYPES:
        logger.warning(
            "Invalid file type",
            content_type=file.content_type,
            allowed_types=ALLOWED_MIME_TYPES,
        )
        raise HTTPException(
            status_code=400,
            detail=f"Invalid file type: {file.content_type}. Allowed types: {', '.join(ALLOWED_MIME_TYPES)}",
        )

    # Read file data
    file_data = await file.read()

    if not file_data:
        raise HTTPException(status_code=400, detail="Empty file")

    # Prepare metadata
    metadata = {
        "filename": file.filename,
        "content_type": file.content_type,
        "tenant_id": str(tenant_id),
        "user_id": str(user_id) if user_id else None,
    }

    try:
        # Store in Redis (and disk if configured)
        file_id = await FileStorageService.store_file(file_data, metadata)

        # Determine storage_key based on persistence config
        from ..config import get_config
        config = get_config()

        if config.file_storage_persist:
            storage_key = f"disk:{config.file_storage_path}/{file_id[:2]}/{file_id}"
        else:
            storage_key = f"file:{file_id}"

        # Persist metadata to PostgreSQL
        async with get_session_context() as session:
            file_upload = FileUploadModel(
                id=uuid.UUID(file_id),
                tenant_id=tenant_id,
                user_id=user_id,
                filename=file.filename or "unknown",
                content_type=file.content_type or "application/octet-stream",
                size_bytes=len(file_data),
                storage_key=storage_key,
                metadata_=metadata,
            )
            session.add(file_upload)
            await session.commit()

        logger.info(
            "File uploaded successfully",
            file_id=file_id,
            filename=file.filename,
            size_bytes=len(file_data),
        )

        return FileUploadResponse(
            file_id=file_id,
            filename=file.filename or "unknown",
            content_type=file.content_type or "application/octet-stream",
            size_bytes=len(file_data),
            created_at=datetime.now(UTC).isoformat(),
        )

    except ValueError as e:
        # File size or validation errors
        logger.warning(
            "File upload validation failed",
            error=str(e),
            filename=file.filename,
        )
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(
            "File upload failed",
            error=str(e),
            filename=file.filename,
        )
        raise HTTPException(status_code=500, detail="File upload failed")


@router.get("/{file_id}", response_model=FileMetadataResponse)
async def get_file_metadata(
    file_id: str,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
) -> FileMetadataResponse:
    """Get file metadata.

    Retrieves file information from PostgreSQL database.
    """
    logger.debug(
        "Get file metadata request",
        file_id=file_id,
        tenant_id=str(tenant_id),
    )

    try:
        file_uuid = uuid.UUID(file_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid file ID format")

    # Retrieve from database
    async with get_session_context() as session:
        file_upload = await session.get(FileUploadModel, file_uuid)

        if not file_upload:
            raise HTTPException(status_code=404, detail="File not found")

        # Verify tenant ownership
        if file_upload.tenant_id != tenant_id:
            logger.warning(
                "Unauthorized file access attempt",
                file_id=file_id,
                tenant_id=str(tenant_id),
                file_tenant_id=str(file_upload.tenant_id),
            )
            raise HTTPException(status_code=404, detail="File not found")

        return FileMetadataResponse(
            file_id=str(file_upload.id),
            filename=file_upload.filename,
            content_type=file_upload.content_type,
            size_bytes=file_upload.size_bytes,
            created_at=file_upload.created_at.isoformat(),
            job_id=str(file_upload.job_id) if file_upload.job_id else None,
        )


@router.get("/{file_id}/download-url", response_model=FileDownloadUrlResponse)
async def get_file_download_url(
    request: Request,
    file_id: str,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    user_id: uuid.UUID | None = Depends(get_user_id),
) -> FileDownloadUrlResponse:
    """Return a short-lived openable URL for downloading the file.

    The URL includes a one-time token so it can be opened in a new tab
    or shared without sending the Bearer header.
    """
    try:
        file_uuid = uuid.UUID(file_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid file ID format")

    async with get_session_context() as session:
        file_upload = await session.get(FileUploadModel, file_uuid)
        if not file_upload or file_upload.tenant_id != tenant_id:
            raise HTTPException(status_code=404, detail="File not found")

    partner_id: uuid.UUID | None = getattr(request.state, "partner_id", None)
    ott = create_file_download_ott(
        file_id=file_id,
        tenant_id=tenant_id,
        user_id=user_id,
        partner_id=partner_id,
    )
    base = str(request.base_url).rstrip("/")
    download_path = f"/api/v1/files/{file_id}/download"
    url = f"{base}{download_path}?token={ott}"
    return FileDownloadUrlResponse(url=url)


@router.get("/{file_id}/download")
async def download_file(
    file_id: str,
    tenant_id: uuid.UUID = Depends(get_download_tenant_id),
):
    """Download file data.

    Authenticate either via Bearer token (tenant_id from auth) or via
    one-time token in query string (from GET /download-url).
    """
    logger.info(
        "File download request",
        file_id=file_id,
        tenant_id=str(tenant_id),
    )

    try:
        file_uuid = uuid.UUID(file_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid file ID format")

    # Verify ownership via database
    async with get_session_context() as session:
        file_upload = await session.get(FileUploadModel, file_uuid)

        if not file_upload:
            raise HTTPException(status_code=404, detail="File not found")

        # Verify tenant ownership
        if file_upload.tenant_id != tenant_id:
            logger.warning(
                "Unauthorized file download attempt",
                file_id=file_id,
                tenant_id=str(tenant_id),
                file_tenant_id=str(file_upload.tenant_id),
            )
            raise HTTPException(status_code=404, detail="File not found")

    # Retrieve file data from Redis (or disk if FILE_STORAGE_PERSIST enabled)
    try:
        file_data, metadata = await FileStorageService.retrieve_file(file_id)

        from fastapi.responses import Response

        # Use inline for PDF and images so they open in the browser tab; attachment for the rest
        content_type = file_upload.content_type or "application/octet-stream"
        is_viewable = content_type == "application/pdf" or (
            content_type.startswith("image/") if content_type else False
        )
        disposition = "inline" if is_viewable else "attachment"
        # Escape quotes in filename for the header
        safe_filename = file_upload.filename.replace("\\", "\\\\").replace('"', '\\"')

        return Response(
            content=file_data,
            media_type=content_type,
            headers={
                "Content-Disposition": f'{disposition}; filename="{safe_filename}"',
            },
        )

    except FileNotFoundError:
        logger.warning(
            "File expired from Redis",
            file_id=file_id,
        )
        raise HTTPException(
            status_code=410,
            detail=(
                "File has expired. Files are kept for 15 minutes in cache. "
                "Enable FILE_STORAGE_PERSIST to store files on disk so they remain available."
            ),
        )
    except Exception as e:
        logger.error(
            "File download failed",
            file_id=file_id,
            error=str(e),
        )
        raise HTTPException(status_code=500, detail="File download failed")
