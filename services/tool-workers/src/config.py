"""Tool Workers configuration."""

from functools import lru_cache

from libs.common.config import Settings


class ToolWorkersConfig(Settings):
    """Tool Workers specific configuration."""

    # Kafka topics
    tools_topic: str = "agent.tools"
    tools_dlq_topic: str = "agent.tools.dlq"
    tool_results_topic: str = "agent.tool-results"

    # Consumer settings
    consumer_group: str = "tool-workers"

    # Tool execution
    tool_timeout_seconds: int = 60

    # Code execution
    code_executor_enabled: bool = True
    code_executor_timeout: int = 30
    code_executor_memory_limit_mb: int = 256


@lru_cache
def get_config() -> ToolWorkersConfig:
    """Get cached Tool Workers configuration."""
    return ToolWorkersConfig()
