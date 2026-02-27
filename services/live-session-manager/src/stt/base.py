"""Base STT interface."""

from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any


class BaseSTTClient(ABC):
    """Abstract base for speech-to-text clients."""

    @abstractmethod
    async def connect(
        self, on_transcript: Callable[[str, bool], Any]
    ) -> None:
        """Connect to STT service.

        Args:
            on_transcript: Callback(text, is_final) for transcript results.
        """
        ...

    @abstractmethod
    async def send_audio(self, audio_base64: str) -> None:
        """Send an audio chunk (base64 encoded)."""
        ...

    @abstractmethod
    async def pause(self) -> None:
        """Pause transcription."""
        ...

    @abstractmethod
    async def resume(self) -> None:
        """Resume transcription."""
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        """Disconnect from STT service."""
        ...
