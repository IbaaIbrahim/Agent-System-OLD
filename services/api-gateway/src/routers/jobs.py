"""Job status endpoints."""

import uuid
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select

from libs.common import get_logger
from libs.common.exceptions import NotFoundError, AuthorizationError
from libs.db import get_session_context
from libs.db.models import Job, ChatMessage

from ..middleware.tenant import get_tenant_id

logger = get_logger(__name__)

router = APIRouter()


class JobStatusResponse(BaseModel):
    """Job status response."""

    job_id: str
    status: str
    provider: str
    model: str
    created_at: str
    completed_at: str | None = None
    error: str | None = None
    total_input_tokens: int
    total_output_tokens: int
    metadata: dict[str, Any]


class JobMessagesResponse(BaseModel):
    """Job messages response."""

    job_id: str
    messages: list[dict[str, Any]]
    total: int


@router.get("/jobs/{job_id}", response_model=JobStatusResponse)
async def get_job_status(
    job_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
) -> JobStatusResponse:
    """Get the status of a job."""
    async with get_session_context() as session:
        result = await session.execute(
            select(Job).where(Job.id == job_id)
        )
        job = result.scalar_one_or_none()

        if not job:
            raise NotFoundError("Job", str(job_id))

        # Verify tenant owns this job
        if job.tenant_id != tenant_id:
            raise AuthorizationError(
                "Access denied to this job",
                details={"job_id": str(job_id)},
            )

        return JobStatusResponse(
            job_id=str(job.id),
            status=job.status.value,
            provider=job.provider,
            model=job.model_id,
            created_at=job.created_at.isoformat(),
            completed_at=job.completed_at.isoformat() if job.completed_at else None,
            error=job.error,
            total_input_tokens=job.total_input_tokens,
            total_output_tokens=job.total_output_tokens,
            metadata=job.metadata_,
        )


@router.get("/jobs/{job_id}/messages", response_model=JobMessagesResponse)
async def get_job_messages(
    job_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    limit: int = 100,
    offset: int = 0,
) -> JobMessagesResponse:
    """Get messages from a completed job."""
    async with get_session_context() as session:
        # Verify job exists and belongs to tenant
        job_result = await session.execute(
            select(Job).where(Job.id == job_id)
        )
        job = job_result.scalar_one_or_none()

        if not job:
            raise NotFoundError("Job", str(job_id))

        if job.tenant_id != tenant_id:
            raise AuthorizationError(
                "Access denied to this job",
                details={"job_id": str(job_id)},
            )

        # Get messages
        result = await session.execute(
            select(ChatMessage)
            .where(ChatMessage.job_id == job_id)
            .order_by(ChatMessage.sequence_num)
            .offset(offset)
            .limit(limit)
        )
        messages = result.scalars().all()

        # Get total count
        count_result = await session.execute(
            select(ChatMessage.id)
            .where(ChatMessage.job_id == job_id)
        )
        total = len(count_result.all())

        return JobMessagesResponse(
            job_id=str(job_id),
            messages=[
                {
                    "id": str(msg.id),
                    "sequence_num": msg.sequence_num,
                    "role": msg.role.value,
                    "content": msg.content,
                    "tool_calls": msg.tool_calls,
                    "tool_call_id": msg.tool_call_id,
                    "input_tokens": msg.input_tokens,
                    "output_tokens": msg.output_tokens,
                    "created_at": msg.created_at.isoformat(),
                }
                for msg in messages
            ],
            total=total,
        )


@router.delete("/jobs/{job_id}")
async def cancel_job(
    job_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
) -> dict[str, str]:
    """Cancel a running job."""
    async with get_session_context() as session:
        result = await session.execute(
            select(Job).where(Job.id == job_id)
        )
        job = result.scalar_one_or_none()

        if not job:
            raise NotFoundError("Job", str(job_id))

        if job.tenant_id != tenant_id:
            raise AuthorizationError(
                "Access denied to this job",
                details={"job_id": str(job_id)},
            )

        # Only pending or running jobs can be cancelled
        if job.status.value not in ("pending", "running"):
            return {
                "message": f"Job is already {job.status.value}",
                "job_id": str(job_id),
            }

        # Update job status
        job.status = "cancelled"
        await session.commit()

        logger.info(
            "Job cancelled",
            job_id=str(job_id),
            tenant_id=str(tenant_id),
        )

        return {
            "message": "Job cancelled",
            "job_id": str(job_id),
        }
