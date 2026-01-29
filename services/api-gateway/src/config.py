"""API Gateway configuration."""

from functools import lru_cache

from libs.common.config import Settings


class APIGatewayConfig(Settings):
    """API Gateway specific configuration."""

    # Stream edge URL for redirects
    stream_edge_url: str = "http://localhost:8001"

    # Job queue topic
    jobs_topic: str = "agent.jobs"

    # Request timeout (seconds)
    request_timeout: int = 30


@lru_cache
def get_config() -> APIGatewayConfig:
    """Get cached API Gateway configuration."""
    return APIGatewayConfig()
