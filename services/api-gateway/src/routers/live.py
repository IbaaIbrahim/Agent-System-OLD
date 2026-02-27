"""Live assistant session management endpoints."""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, update as sa_update

from libs.common import get_logger
from libs.db.models import LiveSession, LiveSessionStatus
from libs.db.session import get_session_context

from ..middleware.tenant import get_tenant_id, get_user_id

logger = get_logger(__name__)

router = APIRouter(prefix="/live", tags=["Live Assistant"])


class StartSessionRequest(BaseModel):
    """Request to start a live session."""

    language: str = Field(default="en", max_length=10)
    tts_voice_id: str | None = Field(default=None, max_length=100)
    conversation_id: str | None = None


class SessionResponse(BaseModel):
    """Response with session info."""

    session_id: str
    status: str
    ws_url: str
    started_at: str | None = None
    audio_input_seconds: float = 0
    audio_output_seconds: float = 0
    total_turns: int = 0


@router.post("/sessions", response_model=SessionResponse)
async def start_live_session(
    body: StartSessionRequest,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
) -> SessionResponse:
    """Start a new live assistant session.

    Returns a session_id and WebSocket URL for the client to connect.
    The actual session initialization happens when the client connects
    to the WebSocket gateway and sends a start_session message.
    """
    user_id = None  # Will be set from request state in middleware

    session_id = uuid.uuid4()

    # Persist session placeholder
    async with get_session_context() as session:
        live_session = LiveSession(
            id=session_id,
            tenant_id=tenant_id,
            user_id=user_id,
            conversation_id=(
                uuid.UUID(body.conversation_id)
                if body.conversation_id
                else None
            ),
            language=body.language,
            tts_voice_id=body.tts_voice_id,
            status=LiveSessionStatus.ACTIVE,
        )
        session.add(live_session)
        await session.flush()

    # Build WS URL — client connects to websocket-gateway
    ws_url = f"ws://localhost:8002/ws"

    logger.info(
        "Live session created",
        session_id=str(session_id),
        tenant_id=str(tenant_id),
    )

    return SessionResponse(
        session_id=str(session_id),
        status="active",
        ws_url=ws_url,
        started_at=datetime.now(UTC).isoformat(),
    )


@router.get("/sessions/{session_id}", response_model=SessionResponse)
async def get_live_session(
    session_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
) -> SessionResponse:
    """Get status of a live session."""
    async with get_session_context() as session:
        stmt = select(LiveSession).where(
            LiveSession.id == session_id,
            LiveSession.tenant_id == tenant_id,
        )
        live_session = (await session.execute(stmt)).scalar_one_or_none()

        if not live_session:
            raise HTTPException(status_code=404, detail="Session not found")

        return SessionResponse(
            session_id=str(live_session.id),
            status=live_session.status,
            ws_url="ws://localhost:8002/ws",
            started_at=live_session.started_at.isoformat() if live_session.started_at else None,
            audio_input_seconds=float(live_session.audio_input_seconds),
            audio_output_seconds=float(live_session.audio_output_seconds),
            total_turns=live_session.total_turns,
        )


@router.delete("/sessions/{session_id}")
async def end_live_session(
    session_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
) -> dict:
    """End a live session."""
    async with get_session_context() as session:
        stmt = (
            sa_update(LiveSession)
            .where(
                LiveSession.id == session_id,
                LiveSession.tenant_id == tenant_id,
            )
            .values(
                status=LiveSessionStatus.ENDED,
                ended_at=datetime.now(UTC),
            )
        )
        result = await session.execute(stmt)

        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Session not found")

    return {"status": "ended", "session_id": str(session_id)}


@router.post("/sessions/{session_id}/pause")
async def pause_live_session(
    session_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
) -> dict:
    """Pause a live session."""
    async with get_session_context() as session:
        stmt = (
            sa_update(LiveSession)
            .where(
                LiveSession.id == session_id,
                LiveSession.tenant_id == tenant_id,
                LiveSession.status == LiveSessionStatus.ACTIVE,
            )
            .values(status=LiveSessionStatus.PAUSED)
        )
        result = await session.execute(stmt)

        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Session not found or not active")

    return {"status": "paused", "session_id": str(session_id)}


@router.post("/sessions/{session_id}/resume")
async def resume_live_session(
    session_id: uuid.UUID,
    tenant_id: uuid.UUID = Depends(get_tenant_id),
) -> dict:
    """Resume a paused live session."""
    async with get_session_context() as session:
        stmt = (
            sa_update(LiveSession)
            .where(
                LiveSession.id == session_id,
                LiveSession.tenant_id == tenant_id,
                LiveSession.status == LiveSessionStatus.PAUSED,
            )
            .values(status=LiveSessionStatus.ACTIVE)
        )
        result = await session.execute(stmt)

        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Session not found or not paused")

    return {"status": "active", "session_id": str(session_id)}
