"""Effort level configuration for agent execution."""

from enum import Enum
from typing import NamedTuple


class EffortLevel(Enum):
    """Agent effort levels controlling iteration depth and behavior."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class EffortConfig(NamedTuple):
    """Configuration for a specific effort level."""

    max_iterations: int
    prompt_section: str


EFFORT_CONFIGS: dict[EffortLevel, EffortConfig] = {
    EffortLevel.LOW: EffortConfig(
        max_iterations=3,
        prompt_section=(
            "Answer directly. Only use tools when explicitly asked. "
            "Do not proactively search or research. Minimize iterations."
        ),
    ),
    EffortLevel.MEDIUM: EffortConfig(
        max_iterations=10,
        prompt_section=(
            "Enrich responses with relevant context. Use tools proactively "
            "when they would clearly benefit the response."
        ),
    ),
    EffortLevel.HIGH: EffortConfig(
        max_iterations=25,
        prompt_section=(
            "Be exhaustive. Conduct MULTIPLE searches with varied queries. "
            "Cross-reference sources. After drafting, critically self-evaluate "
            "for gaps. Iteratively refine \u2014 do not settle for a first draft. "
            "Prefer depth over brevity."
        ),
    ),
}


def get_effort_config(level_str: str | None = None) -> EffortConfig:
    """Get effort configuration for the given level string.

    Args:
        level_str: Effort level string (low/medium/high). Case-insensitive.
            Defaults to MEDIUM if None or invalid.

    Returns:
        EffortConfig with max_iterations and prompt_section.
    """
    if not level_str:
        return EFFORT_CONFIGS[EffortLevel.MEDIUM]

    try:
        level = EffortLevel(level_str.lower())
    except ValueError:
        return EFFORT_CONFIGS[EffortLevel.MEDIUM]

    return EFFORT_CONFIGS[level]
