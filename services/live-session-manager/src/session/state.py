"""Live session state model."""

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum


class SessionState(str, Enum):
    """Live session states."""

    STARTING = "starting"
    LISTENING = "listening"
    PROCESSING = "processing"
    SPEAKING = "speaking"
    PAUSED = "paused"
    ENDED = "ended"


@dataclass
class LiveSessionData:
    """In-memory state for an active live session."""

    session_id: str
    tenant_id: str
    user_id: str | None
    partner_id: str | None
    conversation_id: str | None = None
    language: str = "en"
    tts_voice_id: str | None = None

    # State
    state: SessionState = SessionState.STARTING
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    # Active job tracking (each voice turn = one job)
    current_job_id: str | None = None

    # Usage counters
    audio_input_seconds: float = 0.0
    audio_output_seconds: float = 0.0
    screen_frames_count: int = 0
    total_turns: int = 0

    # Async handles for cleanup
    stt_task: asyncio.Task | None = field(default=None, repr=False)
    tts_task: asyncio.Task | None = field(default=None, repr=False)
    response_task: asyncio.Task | None = field(default=None, repr=False)
    timeout_task: asyncio.Task | None = field(default=None, repr=False)

    # TTS interrupt flag
    tts_interrupted: bool = False
