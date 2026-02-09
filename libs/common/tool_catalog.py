"""Unified tool catalog - single source of truth for all tool configurations."""

from enum import Enum

from pydantic import BaseModel


class ToolBehavior(str, Enum):
    """Tool behavior types determining execution flow."""

    AUTO_EXECUTE = "auto_execute"  # Executes automatically (plan-based, always on)
    USER_ENABLED = "user_enabled"  # Plan-based + user must toggle on in UI
    CONFIRM_REQUIRED = "confirm_required"  # Requires user confirmation per-call
    CLIENT_SIDE = "client_side"  # Executes in frontend


class ToolMetadata(BaseModel):
    """Tool configuration metadata."""

    name: str
    description: str
    parameters: dict  # JSON Schema for tool parameters
    behavior: ToolBehavior
    # Plan requirement (which plans include this tool)
    required_plan_feature: str | None = None  # e.g., "tools.web_search"
    # For USER_ENABLED tools - UI display
    toggle_label: str | None = None  # e.g., "Web Search"
    toggle_description: str | None = None  # e.g., "Allow agent to search the web"
    # For CONFIRM_REQUIRED tools
    confirm_button_label: str | None = None
    confirm_description_template: str | None = None


# Master catalog - workers use this to validate and route
TOOL_CATALOG: dict[str, ToolMetadata] = {
    "get_current_time": ToolMetadata(
        name="get_current_time",
        description=(
            "Get the current date and time. Use this when you need to know the current "
            "time, date, day of week, or need to perform time-related calculations. "
            "Supports different timezones and output formats."
        ),
        parameters={
            "type": "object",
            "properties": {
                "timezone": {
                    "type": "string",
                    "description": (
                        "Timezone name (e.g., 'UTC', 'America/New_York', 'Europe/London', "
                        "'Asia/Tokyo'). Defaults to UTC."
                    ),
                    "default": "UTC",
                },
                "format": {
                    "type": "string",
                    "description": (
                        "Output format: 'iso' for ISO 8601 format, 'human' for "
                        "human-readable format, 'unix' for Unix timestamp. Defaults to 'iso'."
                    ),
                    "enum": ["iso", "human", "unix"],
                    "default": "iso",
                },
            },
            "required": [],
        },
        behavior=ToolBehavior.AUTO_EXECUTE,
        required_plan_feature=None,  # Always available
    ),
    "web_search": ToolMetadata(
        name="web_search",
        description=(
            "Search the web for information. Use this when you need to find "
            "current information, facts, or data that may not be in your training data."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query",
                },
                "num_results": {
                    "type": "integer",
                    "description": "Number of results to return (default: 5, max: 10)",
                    "default": 5,
                },
            },
            "required": ["query"],
        },
        behavior=ToolBehavior.USER_ENABLED,
        required_plan_feature="tools.web_search",
        toggle_label="Web Search",
        toggle_description="Allow agent to search the web for information",
    ),
    "code_executor": ToolMetadata(
        name="code_executor",
        description="Execute Python code in sandbox",
        parameters={
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "Python code to execute",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Execution timeout in seconds (default: 30)",
                    "default": 30,
                },
            },
            "required": ["code"],
        },
        behavior=ToolBehavior.AUTO_EXECUTE,
        required_plan_feature="tools.code_executor",
    ),
    "generate_checklist": ToolMetadata(
        name="generate_checklist",
        description="Generate a structured Flowdit checklist",
        parameters={
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Checklist title",
                },
                "context": {
                    "type": "string",
                    "description": "Context or description for checklist generation",
                },
            },
            "required": ["title", "context"],
        },
        behavior=ToolBehavior.CONFIRM_REQUIRED,
        required_plan_feature="tools.checklist_generator",
        confirm_button_label="Generate Checklist",
        confirm_description_template="Create '{title}' checklist with {context}",
    ),
}


def get_tool_metadata(tool_name: str) -> ToolMetadata | None:
    """Get metadata for a tool by name.

    Args:
        tool_name: The name of the tool

    Returns:
        ToolMetadata if found, None otherwise
    """
    return TOOL_CATALOG.get(tool_name)


def get_tools_for_plan(plan_features: list[str]) -> list[ToolMetadata]:
    """Get all tools available for a given plan's features.

    Args:
        plan_features: List of feature strings from the plan

    Returns:
        List of ToolMetadata for tools available in the plan
    """
    return [
        tool
        for tool in TOOL_CATALOG.values()
        if tool.required_plan_feature is None
        or tool.required_plan_feature in plan_features
    ]


def get_user_toggleable_tools() -> list[ToolMetadata]:
    """Get all tools that can be toggled by users.

    Returns:
        List of ToolMetadata for USER_ENABLED tools
    """
    return [
        tool
        for tool in TOOL_CATALOG.values()
        if tool.behavior == ToolBehavior.USER_ENABLED
    ]


def get_confirm_required_tools() -> list[ToolMetadata]:
    """Get all tools that require user confirmation.

    Returns:
        List of ToolMetadata for CONFIRM_REQUIRED tools
    """
    return [
        tool
        for tool in TOOL_CATALOG.values()
        if tool.behavior == ToolBehavior.CONFIRM_REQUIRED
    ]
