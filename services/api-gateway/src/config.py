"""API Gateway configuration."""

from functools import lru_cache

from libs.common.config import Settings


class APIGatewayConfig(Settings):
    """API Gateway specific configuration."""

    # Stream edge URL for redirects
    stream_edge_url: str = "http://localhost:8001"

    # Job queue topic
    jobs_topic: str = "agent.jobs"

    # Confirm response topic
    confirm_topic: str = "agent.confirm"

    # Resume topic (for client-side tool results)
    resume_topic: str = "agent.job-resume"

    # Request timeout (seconds)
    request_timeout: int = 30

    # File storage persistence
    file_storage_persist: bool = False  # Write uploaded files to disk
    file_storage_path: str = "./file_uploads"  # Directory for persistent files

    # Billing feature flag (disabled by default)
    enable_billing_checks: bool = False
    default_credit_balance_micros: int = 100_000_000  # $100.00 in microdollars


@lru_cache
def get_config() -> APIGatewayConfig:
    """Get cached API Gateway configuration."""
    return APIGatewayConfig()
