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
            from deepgram.core.events import EventType

            self._client = DeepgramClient(api_key=self._api_key)

            # v1 listen supports nova-3; v2 listen is for Flux (flux-general-en) and rejects nova-3 with HTTP 400
            def on_message(msg: Any) -> None:
                if self._paused or not self._on_transcript:
                    return
                # ListenV1ResultsEvent has type "Results", channel.alternatives[0].transcript, is_final
                if getattr(msg, "type", None) != "Results":
                    return
                if not getattr(msg, "channel", None) or not getattr(msg.channel, "alternatives", None):
                    return
                alternatives = msg.channel.alternatives
                if not alternatives:
                    return
                transcript = getattr(alternatives[0], "transcript", "") or ""
                is_final = bool(getattr(msg, "is_final", False))
                if transcript:
                    self._on_transcript(transcript, is_final)

            def on_error(exc: Exception) -> None:
                logger.error("Deepgram STT error", error=str(exc))

            def run_connection() -> None:
                import time as _time
                last_error: Exception | None = None
                max_attempts = 3
                for attempt in range(1, max_attempts + 1):
                    try:
                        with self._client.listen.v1.connect(
                            model=self._model,
                            encoding=self._encoding,
                            sample_rate=str(self._sample_rate),
                            language=self._language,
                            endpointing=str(self._endpointing),
                        ) as self._connection:
                            self._connection.on(EventType.MESSAGE, on_message)
                            self._connection.on(EventType.ERROR, on_error)
                            self._connection.start_listening()

                            logger.info(
                                "Deepgram STT connected (v1 listen)",
                                model=self._model,
                                language=self._language,
                            )

                            while self._connection:
                                _time.sleep(1)
                        return
                    except (OSError, ConnectionError) as e:
                        last_error = e
                        if attempt < max_attempts:
                            logger.warning(
                                "Deepgram connection attempt failed, retrying",
                                attempt=attempt,
                                max_attempts=max_attempts,
                                error=str(e),
                            )
                            _time.sleep(1.5 * attempt)
                        else:
                            break
                    except Exception as e:
                        last_error = e
                        break

                e = last_error
                if e is None:
                    return
                logger.error("Deepgram connection error", error=str(e))
                raise e

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
                if hasattr(self._connection, "finish"):
                    self._connection.finish()
                elif hasattr(self._connection, "_websocket"):
                    self._connection._websocket.close()
            except Exception as e:
                logger.debug("Error closing Deepgram connection", error=str(e))
            self._connection = None
            self._thread = None
