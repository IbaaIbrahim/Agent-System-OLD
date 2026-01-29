"""Stream Edge configuration."""

from functools import lru_cache

from libs.common.config import Settings


class StreamEdgeConfig(Settings):
    """Stream Edge specific configuration."""

    # SSE settings
    sse_retry_ms: int = 3000
    sse_keepalive_interval: int = 15

    # Catch-up settings
    catchup_max_events: int = 1000
    catchup_hot_window_seconds: int = 300  # 5 minutes

    # Connection limits
    max_connections_per_job: int = 10


@lru_cache
def get_config() -> StreamEdgeConfig:
    """Get cached Stream Edge configuration."""
    return StreamEdgeConfig()
