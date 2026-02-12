"""Deepgram real-time STT client using WebSocket streaming."""

import base64
from collections.abc import Callable
from typing import Any

from libs.common import get_logger

from .base import BaseSTTClient

logger = get_logger(__name__)


class DeepgramSTTClient(BaseSTTClient):
    """Deepgram Nova streaming speech-to-text via WebSocket."""

    def __init__(
        self,
        api_key: str,
        model: str = "nova-3",
        language: str = "en",
        sample_rate: int = 16000,
        encoding: str = "linear16",
        endpointing: int = 500,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._language = language
        self._sample_rate = sample_rate
        self._encoding = encoding
        self._endpointing = endpointing
        self._connection: Any = None
        self._client: Any = None
        self._on_transcript: Callable[[str, bool], Any] | None = None
        self._paused = False

    async def connect(
        self, on_transcript: Callable[[str, bool], Any]
    ) -> None:
        """Connect to Deepgram streaming STT."""
        self._on_transcript = on_transcript

        try:
            from deepgram import DeepgramClient, LiveTranscriptionEvents

            self._client = DeepgramClient(self._api_key)
            self._connection = self._client.listen.asyncwebsocket.v("1")

            # Register event handlers
            async def on_message(_, result, **kwargs):
                if self._paused:
                    return
                transcript = result.channel.alternatives[0].transcript
                is_final = result.is_final
                if transcript and self._on_transcript:
                    self._on_transcript(transcript, is_final)

            async def on_error(_, error, **kwargs):
                logger.error("Deepgram STT error", error=str(error))

            self._connection.on(LiveTranscriptionEvents.Transcript, on_message)
            self._connection.on(LiveTranscriptionEvents.Error, on_error)

            await self._connection.start({
                "model": self._model,
                "language": self._language,
                "encoding": self._encoding,
                "sample_rate": self._sample_rate,
                "smart_format": True,
                "interim_results": True,
                "endpointing": self._endpointing,
                "vad_events": True,
            })

            logger.info(
                "Deepgram STT connected",
                model=self._model,
                language=self._language,
            )

        except ImportError:
            logger.error(
                "deepgram-sdk not installed. Install with: pip install deepgram-sdk"
            )
            raise
        except Exception as e:
            logger.error("Failed to connect to Deepgram", error=str(e))
            raise

    async def send_audio(self, audio_base64: str) -> None:
        """Send base64-encoded audio data to Deepgram."""
        if not self._connection or self._paused:
            return

        try:
            audio_bytes = base64.b64decode(audio_base64)
            await self._connection.send(audio_bytes)
        except Exception as e:
            logger.error("Error sending audio to Deepgram", error=str(e))

    async def pause(self) -> None:
        """Pause transcription (stop processing audio)."""
        self._paused = True

    async def resume(self) -> None:
        """Resume transcription."""
        self._paused = False

    async def disconnect(self) -> None:
        """Close Deepgram connection."""
        if self._connection:
            try:
                await self._connection.finish()
            except Exception as e:
                logger.debug("Error closing Deepgram connection", error=str(e))
            self._connection = None
