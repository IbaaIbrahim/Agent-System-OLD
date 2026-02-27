"""Live Session Manager configuration."""

from functools import lru_cache

from pydantic import Field

from libs.common.config import Settings


class LiveSessionManagerConfig(Settings):
    """Live Session Manager specific configuration."""

    # Service port (HTTP health check)
    live_session_manager_port: int = Field(default=8003, ge=1, le=65535)

    # Deepgram STT
    deepgram_api_key: str = Field(default="", description="Deepgram API key for STT")
    deepgram_model: str = Field(default="nova-3", description="Deepgram model")
    deepgram_language: str = Field(default="en-US", description="Default STT language (e.g., en-US, es-ES)")
    deepgram_encoding: str = Field(default="linear16", description="Audio encoding")
    deepgram_sample_rate: int = Field(default=16000, description="Audio sample rate")
    deepgram_endpointing: int = Field(
        default=500, description="Silence ms to end utterance"
    )

    # ElevenLabs TTS
    elevenlabs_api_key: str = Field(
        default="", description="ElevenLabs API key for TTS"
    )
    elevenlabs_default_voice_id: str = Field(
        default="21m00Tcm4TlvDq8ikWAM",  # Rachel
        description="Default ElevenLabs voice ID",
    )
    elevenlabs_model: str = Field(
        default="eleven_turbo_v2_5", description="ElevenLabs model"
    )

    # Screen frame processing
    screen_frame_max_width: int = Field(default=720, ge=100, le=1920)
    screen_frame_max_height: int = Field(default=512, ge=100, le=1080)

    # Session limits
    live_session_timeout_seconds: int = Field(
        default=3600, description="Max session duration"
    )
    live_session_max_per_tenant: int = Field(
        default=10, description="Max concurrent sessions per tenant"
    )

    # Kafka topics
    jobs_topic: str = Field(default="agent.jobs")


@lru_cache
def get_config() -> LiveSessionManagerConfig:
    """Get cached Live Session Manager configuration."""
    return LiveSessionManagerConfig()
