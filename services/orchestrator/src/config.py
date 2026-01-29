"""Orchestrator configuration."""

from functools import lru_cache

from libs.common.config import Settings


class OrchestratorConfig(Settings):
    """Orchestrator specific configuration."""

    # Kafka topics
    jobs_topic: str = "agent.jobs"
    jobs_dlq_topic: str = "agent.jobs.dlq"
    tools_topic: str = "agent.tools"
    tool_results_topic: str = "agent.tool-results"

    # Consumer settings
    consumer_group: str = "orchestrator"

    # Agent settings
    max_iterations: int = 10
    tool_timeout_seconds: int = 60
    snapshot_interval: int = 5  # Save state every N iterations


@lru_cache
def get_config() -> OrchestratorConfig:
    """Get cached Orchestrator configuration."""
    return OrchestratorConfig()
