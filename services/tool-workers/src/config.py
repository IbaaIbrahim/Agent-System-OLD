"""Tool Workers configuration."""

from functools import lru_cache

from libs.common.config import Settings


class ToolWorkersConfig(Settings):
    """Tool Workers specific configuration."""

    # Kafka topics
    tools_topic: str = "agent.tools"
    tools_dlq_topic: str = "agent.tools.dlq"
    tool_results_topic: str = "agent.tool-results"
    resume_topic: str = "agent.job-resume"

    # Consumer settings
    consumer_group: str = "tool-workers"

    # Tool execution
    tool_timeout_seconds: int = 60

    # Code execution
    code_executor_enabled: bool = True
    code_executor_timeout: int = 30
    code_executor_memory_limit_mb: int = 256

    # Web search configuration
    web_search_provider: str = "duckduckgo"  # "duckduckgo" or "brave"
    brave_api_key: str = ""  # Optional, only needed if provider="brave"
    web_search_timeout: int = 10


@lru_cache
def get_config() -> ToolWorkersConfig:
    """Get cached Tool Workers configuration."""
    return ToolWorkersConfig()
