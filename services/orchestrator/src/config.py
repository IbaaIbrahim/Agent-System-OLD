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
    resume_topic: str = "agent.job-resume"
    confirm_topic: str = "agent.confirm"

    # Consumer settings
    consumer_group: str = "orchestrator"
    resume_consumer_group: str = "orchestrator-resume"
    confirm_consumer_group: str = "orchestrator-confirm"

    # Agent settings
    max_iterations: int = 10
    tool_timeout_seconds: int = 60
    snapshot_interval: int = 5  # Save state every N iterations

    # Suspend/Resume settings
    enable_suspend_resume: bool = True  # Feature flag for safe rollout
    job_lock_ttl_seconds: int = 300  # 5 minutes
    job_lock_extend_threshold_seconds: int = 240  # Extend at 4 minutes

    # Multi-phase settings
    enable_multi_phase: bool = True
    user_response_topic: str = "agent.user-response"
    user_response_consumer_group: str = "orchestrator-user-response"


@lru_cache
def get_config() -> OrchestratorConfig:
    """Get cached Orchestrator configuration."""
    return OrchestratorConfig()
