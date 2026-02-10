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
    # Execution location
    client_side_execution: bool = False  # If True, executes in browser instead of backend workers
    # Model preferences (used when user doesn't specify a model)
    preferred_provider: str | None = None  # e.g., "anthropic", "openai"
    preferred_model: str | None = None  # e.g., "claude-3-5-sonnet-20241022"
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
        preferred_provider="anthropic",
        preferred_model="claude-3-5-haiku-20241022",  # Simple tool, use fast model
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
        preferred_provider="anthropic",
        preferred_model="claude-3-5-haiku-20241022",  # Search summarization, use fast model
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
        preferred_provider="anthropic",
        preferred_model="claude-3-5-sonnet-20241022",  # Code needs reasoning
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
        preferred_provider="anthropic",
        preferred_model="claude-3-5-sonnet-20241022",  # Complex generation needs strong model
        required_plan_feature="tools.checklist_generator",
        confirm_button_label="Generate Checklist",
        confirm_description_template="Create '{title}' checklist with {context}",
    ),
    "analyze_file": ToolMetadata(
        name="analyze_file",
        description=(
            "Analyze uploaded files (images, PDFs, documents) using vision models. "
            "Use this to extract information, generate checklists, summarize content, "
            "or answer questions about uploaded files."
        ),
        parameters={
            "type": "object",
            "properties": {
                "file_id": {
                    "type": "string",
                    "description": "ID of the uploaded file to analyze",
                },
                "query": {
                    "type": "string",
                    "description": (
                        "What to analyze or extract from the file. "
                        "Examples: 'extract checklist items', 'summarize this document', "
                        "'what does this diagram show?', 'generate a todo list from this image'"
                    ),
                },
            },
            "required": ["file_id", "query"],
        },
        behavior=ToolBehavior.AUTO_EXECUTE,
        preferred_provider="anthropic",
        preferred_model="claude-sonnet-4-5",  # Vision model for image analysis
        # required_plan_feature="tools.file_analysis",
        required_plan_feature=None,
        confirm_button_label="Analyze File",
        confirm_description_template="Analyze file with query: {query}",
    ),
    "read_page_content": ToolMetadata(
        name="read_page_content",
        description=(
            "Read and analyze the current webpage content. Use this tool whenever "
            "the user asks about the page they are on, what they are looking at, "
            "or requires information from the current screen. It extracts the visible "
            "text, semantic structure (headings, lists, tables), and links to provide "
            "full context of the user's current environment."
        ),
        parameters={
            "type": "object",
            "properties": {
                "selector": {
                    "type": "string",
                    "description": (
                        "Optional CSS selector to limit extraction to a specific element. "
                        "If not provided, extracts from entire document body."
                    ),
                },
                "include_metadata": {
                    "type": "boolean",
                    "description": "Include page metadata (title, URL, timestamp)",
                    "default": True,
                },
                "max_length": {
                    "type": "integer",
                    "description": "Maximum characters to return (default: 50000)",
                    "default": 50000,
                },
            },
            "required": [],
        },
        behavior=ToolBehavior.CONFIRM_REQUIRED,
        client_side_execution=True,  # Executes in browser, but requires confirmation first
        required_plan_feature=None,  # Available to all plans
        toggle_label="Page Context",
        toggle_description="Allow agent to read webpage content and structure",
        confirm_button_label="Read Page",
        confirm_description_template="Allow the assistant to read the current webpage content?",
    ),
    "read_page_content_advanced": ToolMetadata(
        name="read_page_content_advanced",
        description=(
            "Advanced page reading with HTML extraction, element finding, and screenshot analysis. "
            "Use this when you need precise DOM structure, specific element location, or visual "
            "analysis of the page. This tool can capture screenshots and use vision models to "
            "analyze visual elements, layouts, and images on the page."
        ),
        parameters={
            "type": "object",
            "properties": {
                "selector": {
                    "type": "string",
                    "description": "CSS selector to target (default: body)",
                },
                "include_html": {
                    "type": "boolean",
                    "description": "Include raw HTML in addition to text",
                    "default": False,
                },
                "find_element_query": {
                    "type": "string",
                    "description": (
                        "Natural language query to find a specific element. "
                        "Examples: 'submit button', 'login form', 'pricing table'"
                    ),
                },
                "capture_screenshot": {
                    "type": "boolean",
                    "description": "Capture screenshot for vision model analysis",
                    "default": False,
                },
                "screenshot_selector": {
                    "type": "string",
                    "description": "Element to screenshot (default: viewport). Requires capture_screenshot=true",
                },
                "screenshot_query": {
                    "type": "string",
                    "description": (
                        "What to analyze in the screenshot. "
                        "Examples: 'describe the layout', 'what colors are used?', 'analyze the design'"
                    ),
                },
            },
            "required": [],
        },
        behavior=ToolBehavior.CONFIRM_REQUIRED,
        client_side_execution=True,
        preferred_provider="anthropic",
        preferred_model="claude-3-5-sonnet-20241022",  # Vision model for screenshot analysis
        required_plan_feature=None,  # Available to all plans
        toggle_label="Advanced Page Reading",
        toggle_description="Allow HTML extraction, element finding, and screenshot analysis",
        confirm_button_label="Analyze Page",
        confirm_description_template="Analyze page with advanced features (HTML, screenshot, element finding)?",
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


def get_tool_model_preference(
    tool_name: str,
    default_provider: str = "anthropic",
    default_model: str = "claude-3-5-sonnet-20241022",
) -> tuple[str, str]:
    """Get the preferred provider and model for a tool.

    If the tool has a preferred provider/model, use that.
    Otherwise, fall back to the provided defaults.

    Args:
        tool_name: The name of the tool
        default_provider: Fallback provider if tool has no preference
        default_model: Fallback model if tool has no preference

    Returns:
        Tuple of (provider, model)
    """
    tool = TOOL_CATALOG.get(tool_name)
    if tool:
        provider = tool.preferred_provider or default_provider
        model = tool.preferred_model or default_model
        return (provider, model)
    return (default_provider, default_model)
