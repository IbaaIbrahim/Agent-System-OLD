"""Snapshot service for persisting agent state."""

from uuid import UUID

from sqlalchemy import select

from libs.common import get_logger, NUL, sanitize_for_postgres
from libs.db import get_session_context
from libs.db.models import Job, JobSnapshot, JobStatus

from ..engine.serializer import StateSerializer
from ..engine.state import AgentState, AgentStatus

logger = get_logger(__name__)


class SnapshotService:
    """Manages persistence of agent state."""

    async def save_job(self, state: AgentState) -> None:
        """Create or update job record in database.

        Args:
            state: Agent state to save
        """
        async with get_session_context() as session:
            # Check if job exists
            result = await session.execute(
                select(Job).where(Job.id == state.job_id)
            )
            job = result.scalar_one_or_none()

            if job:
                # Update existing job
                job.status = self._map_status(state.status)
                job.total_input_tokens = state.total_input_tokens
                job.total_output_tokens = state.total_output_tokens
                if state.completed_at:
                    job.completed_at = state.completed_at
                if state.error:
                    job.error = (state.error or "").replace(NUL, "")
            else:
                # Create new job
                job = Job(
                    id=state.job_id,
                    tenant_id=state.tenant_id,
                    user_id=state.user_id,
                    status=self._map_status(state.status),
                    provider=state.provider,
                    model_id=state.model,
                    system_prompt=state.system_prompt,
                    tools_config=state.tools,
                    metadata_=state.metadata,
                    total_input_tokens=state.total_input_tokens,
                    total_output_tokens=state.total_output_tokens,
                )
                session.add(job)

            await session.commit()

            logger.debug(
                "Job saved",
                job_id=str(state.job_id),
                status=state.status.value,
            )

    async def update_job(self, state: AgentState) -> None:
        """Update job status and completion info.

        Args:
            state: Agent state with updated info
        """
        async with get_session_context() as session:
            result = await session.execute(
                select(Job).where(Job.id == state.job_id)
            )
            job = result.scalar_one_or_none()

            if not job:
                logger.warning(
                    "Job not found for update",
                    job_id=str(state.job_id),
                )
                return

            job.status = self._map_status(state.status)
            job.total_input_tokens = state.total_input_tokens
            job.total_output_tokens = state.total_output_tokens

            if state.completed_at:
                job.completed_at = state.completed_at
            if state.error:
                job.error = (state.error or "").replace(NUL, "")

            await session.commit()

    async def save_snapshot(self, state: AgentState) -> None:
        """Save a state snapshot for recovery.

        Args:
            state: Agent state to snapshot
        """
        async with get_session_context() as session:
            raw = StateSerializer.serialize(state)
            state_data = sanitize_for_postgres(raw)
            assert isinstance(state_data, dict)
            snapshot = JobSnapshot(
                job_id=state.job_id,
                sequence_num=state.iteration,
                state_data=state_data,
            )
            session.add(snapshot)
            await session.commit()

            logger.debug(
                "Snapshot saved",
                job_id=str(state.job_id),
                iteration=state.iteration,
            )

    async def load_latest_snapshot(self, job_id: UUID) -> AgentState | None:
        """Load the latest snapshot for a job.

        Args:
            job_id: Job ID to load snapshot for

        Returns:
            AgentState or None if no snapshot exists
        """
        async with get_session_context() as session:
            result = await session.execute(
                select(JobSnapshot)
                .where(JobSnapshot.job_id == job_id)
                .order_by(JobSnapshot.sequence_num.desc())
                .limit(1)
            )
            snapshot = result.scalar_one_or_none()

            if not snapshot:
                return None

            logger.info(
                "Snapshot loaded",
                job_id=str(job_id),
                sequence_num=snapshot.sequence_num,
            )

            return StateSerializer.deserialize(snapshot.state_data)

    async def delete_snapshots(self, job_id: UUID) -> int:
        """Delete all snapshots for a job.

        Args:
            job_id: Job ID

        Returns:
            Number of snapshots deleted
        """
        async with get_session_context() as session:
            result = await session.execute(
                select(JobSnapshot).where(JobSnapshot.job_id == job_id)
            )
            snapshots = result.scalars().all()

            count = len(snapshots)
            for snapshot in snapshots:
                await session.delete(snapshot)

            await session.commit()

            logger.debug(
                "Snapshots deleted",
                job_id=str(job_id),
                count=count,
            )

            return count

    def _map_status(self, status: AgentStatus) -> JobStatus:
        """Map AgentStatus to JobStatus enum."""
        mapping = {
            AgentStatus.PENDING: JobStatus.PENDING,
            AgentStatus.RUNNING: JobStatus.RUNNING,
            AgentStatus.WAITING_TOOL: JobStatus.RUNNING,
            AgentStatus.COMPLETED: JobStatus.COMPLETED,
            AgentStatus.FAILED: JobStatus.FAILED,
            AgentStatus.CANCELLED: JobStatus.CANCELLED,
        }
        return mapping.get(status, JobStatus.PENDING)
