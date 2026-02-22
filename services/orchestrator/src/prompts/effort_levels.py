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
    enable_multi_phase: bool = False
    max_evaluations: int = 0
    evaluation_pass_score: int = 6


EFFORT_CONFIGS: dict[EffortLevel, EffortConfig] = {
    EffortLevel.LOW: EffortConfig(
        max_iterations=3,
        prompt_section=(
            "Answer directly. Only use tools when explicitly asked. "
            "Do not proactively search or research. Minimize iterations."
        ),
        enable_multi_phase=False,
        max_evaluations=0,
        evaluation_pass_score=5,
    ),
    EffortLevel.MEDIUM: EffortConfig(
        max_iterations=10,
        prompt_section=(
            "Enrich responses with relevant context. Use tools proactively "
            "when they would clearly benefit the response."
        ),
        enable_multi_phase=False,
        max_evaluations=0,
        evaluation_pass_score=6,
    ),
    EffortLevel.HIGH: EffortConfig(
        max_iterations=50,
        prompt_section=(
            "## DEEP RESEARCH MODE - MAXIMUM THOROUGHNESS REQUIRED\n\n"
            "You are operating in DEEP RESEARCH MODE. This means:\n\n"
            "**MULTIPLE TOOL CALLS - SIMULTANEOUSLY:**\n"
            "- Call MULTIPLE tools in parallel when investigating a topic\n"
            "- Use different tools to gather diverse perspectives (e.g., multiple web searches with varied queries, file analysis, page reading)\n"
            "- Do NOT duplicate identical tool calls - but DO make multiple RELATED calls with different parameters\n"
            "- Example: For 'climate change impacts', call web_search with 'climate change effects', 'global warming consequences', 'environmental impacts' simultaneously\n"
            "- Example: If analyzing a document, call analyze_file AND web_search for related context\n\n"
            "**DEEP THINKING PROCESS:**\n"
            "- Think EXTENSIVELY before responding - show your full reasoning in <thinking></thinking> tags\n"
            "- Consider multiple angles, edge cases, and alternative perspectives\n"
            "- Cross-reference information from different sources\n"
            "- Question assumptions and verify claims\n"
            "- There is NO TIME PRESSURE - take as long as needed to think deeply\n\n"
            "**CREATIVE AND COMPREHENSIVE:**\n"
            "- Be creative in your approach - explore unconventional angles\n"
            "- Don't settle for surface-level answers - dig deeper\n"
            "- Synthesize information from multiple sources into coherent insights\n"
            "- Consider implications, connections, and broader context\n\n"
            "**ITERATIVE REFINEMENT:**\n"
            "- After gathering initial information, critically evaluate gaps\n"
            "- Make additional tool calls to fill knowledge gaps\n"
            "- Refine your understanding through multiple iterations\n"
            "- Do NOT settle for a first draft - iterate until comprehensive\n"
            "- Cross-check facts and verify information from multiple sources\n\n"
            "**THOROUGHNESS OVER SPEED:**\n"
            "- Prefer depth over brevity - provide comprehensive, detailed responses\n"
            "- Include relevant context, background, and supporting information\n"
            "- Cite sources and explain your reasoning\n"
            "- Quality and completeness are more important than speed\n\n"
            "**TOOL USAGE STRATEGY:**\n"
            "- Use ALL available tools that could provide relevant information\n"
            "- Combine tools strategically (e.g., search → read pages → analyze files)\n"
            "- Make parallel tool calls when possible to gather information efficiently\n"
            "- After receiving results, evaluate if additional tools would help\n"
            "- Continue tool usage until you have comprehensive coverage of the topic\n\n"
            "Remember: In DEEP RESEARCH MODE, your goal is to provide the MOST COMPREHENSIVE, THOROUGHLY RESEARCHED, and WELL-REASONED response possible. "
            "Take your time, use multiple tools, think deeply, and iterate until you're confident you've covered all important aspects."
        ),
        enable_multi_phase=True,
        max_evaluations=3,
        evaluation_pass_score=7,
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
