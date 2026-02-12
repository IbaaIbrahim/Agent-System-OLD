"""Base TTS interface."""

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator


class BaseTTSClient(ABC):
    """Abstract base for text-to-speech clients."""

    @abstractmethod
    async def synthesize(self, text: str) -> AsyncIterator[str]:
        """Synthesize text to speech, yielding base64-encoded audio chunks.

        Args:
            text: Text to synthesize.

        Yields:
            Base64-encoded audio chunks (PCM 24kHz).
        """
        ...

    @abstractmethod
    async def flush(self) -> None:
        """Flush any buffered audio (for interruption support)."""
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        """Disconnect from TTS service."""
        ...
