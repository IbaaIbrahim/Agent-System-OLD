"""ElevenLabs real-time TTS client using WebSocket streaming."""

import base64
from collections.abc import AsyncIterator
from typing import Any

from libs.common import get_logger

from .base import BaseTTSClient

logger = get_logger(__name__)


class ElevenLabsTTSClient(BaseTTSClient):
    """ElevenLabs streaming text-to-speech via WebSocket."""

    def __init__(
        self,
        api_key: str,
        voice_id: str = "21m00Tcm4TlvDq8ikWAM",
        model: str = "eleven_turbo_v2_5",
    ) -> None:
        self._api_key = api_key
        self._voice_id = voice_id
        self._model = model
        self._client: Any = None

    async def synthesize(self, text: str) -> AsyncIterator[str]:
        """Synthesize text to speech, yielding base64-encoded audio chunks."""
        try:
            from elevenlabs import ElevenLabs

            client = ElevenLabs(api_key=self._api_key)

            # Use streaming generation
            audio_stream = client.text_to_speech.convert(
                text=text,
                voice_id=self._voice_id,
                model_id=self._model,
                output_format="pcm_24000",
            )

            # ElevenLabs returns bytes iterator
            # Chunk into ~100ms segments (24000 Hz * 2 bytes * 0.1s = 4800 bytes)
            buffer = b""
            chunk_size = 4800

            for audio_bytes in audio_stream:
                buffer += audio_bytes
                while len(buffer) >= chunk_size:
                    chunk = buffer[:chunk_size]
                    buffer = buffer[chunk_size:]
                    yield base64.b64encode(chunk).decode("ascii")

            # Flush remaining buffer
            if buffer:
                yield base64.b64encode(buffer).decode("ascii")

        except ImportError:
            logger.error(
                "elevenlabs not installed. Install with: pip install elevenlabs"
            )
            raise
        except Exception as e:
            logger.error("ElevenLabs TTS error", error=str(e))
            raise

    async def flush(self) -> None:
        """Flush any buffered audio."""
        pass

    async def disconnect(self) -> None:
        """Clean up TTS client."""
        self._client = None
