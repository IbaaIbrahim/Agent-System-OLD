"""Archiver configuration."""

import os
from functools import lru_cache

from libs.common.config import Settings


class ArchiverConfig(Settings):
    """Archiver specific configuration."""

    # Consumer settings
    consumer_group: str = "archiver"
    consumer_name: str = f"archiver-{os.getpid()}"

    # Stream patterns to consume
    stream_pattern: str = "events:*"

    # Batching settings
    batch_size: int = 100
    flush_interval: int = 5  # seconds

    # Retention settings
    stream_retention_hours: int = 24
    cleanup_interval: int = 3600  # 1 hour


@lru_cache
def get_config() -> ArchiverConfig:
    """Get cached Archiver configuration."""
    return ArchiverConfig()
