"""Deepgram real-time STT client using WebSocket streaming."""

import base64
import threading
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
        self._thread: threading.Thread | None = None

    async def connect(
        self, on_transcript: Callable[[str, bool], Any]
    ) -> None:
        """Connect to Deepgram streaming STT."""
        self._on_transcript = on_transcript

        try:
            from deepgram import DeepgramClient

            self._client = DeepgramClient(api_key=self._api_key)

            # Define event handlers
            def on_message(result, **kwargs):
                if self._paused:
                    return
                transcript = result.channel.alternatives[0].transcript
                is_final = result.is_final
                if transcript and self._on_transcript:
                    self._on_transcript(transcript, is_final)

            def on_error(error, **kwargs):
                logger.error("Deepgram STT error", error=str(error))

            # Use context manager pattern - run in thread
            def run_connection():
                try:
                    with self._client.listen.v2.connect(
                        model=self._model,
                        encoding=self._encoding,
                        sample_rate=self._sample_rate,
                    ) as self._connection:

                        # Register event handlers using 'on' method
                        self._connection.on("Transcript", on_message)
                        self._connection.on("Error", on_error)

                        # Start listening
                        self._connection.start_listening()

                        logger.info(
                            "Deepgram STT connected",
                            model=self._model,
                            language=self._language,
                        )

                        # Keep thread alive
                        while self._connection:
                            import time
                            time.sleep(1)

                except Exception as e:
                    logger.error("Deepgram connection error", error=str(e))
                    raise

            # Run connection in a thread to not block
            self._thread = threading.Thread(target=run_connection, daemon=True)
            self._thread.start()

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
            self._connection.send_media(audio_bytes)
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
                self._connection.finish()
            except Exception as e:
                logger.debug("Error closing Deepgram connection", error=str(e))
            self._connection = None
            self._thread = None
