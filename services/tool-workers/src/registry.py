"""Tool registry for discovering and managing tools."""

from typing import Any

from libs.common import get_logger

from .config import get_config
from .tools.base import BaseTool
from .tools.code_executor import CodeExecutorTool
from .tools.web_search import WebSearchTool

logger = get_logger(__name__)


class ToolRegistry:
    """Registry for managing available tools."""

    _instance: "ToolRegistry | None" = None
    _initialized: bool = False

    def __new__(cls) -> "ToolRegistry":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        if not ToolRegistry._initialized:
            self.tools: dict[str, BaseTool] = {}
            ToolRegistry._initialized = True

    def register(self, tool: BaseTool) -> None:
        """Register a tool.

        Args:
            tool: Tool instance to register
        """
        self.tools[tool.name] = tool
        logger.debug(f"Registered tool: {tool.name}")

    def unregister(self, name: str) -> None:
        """Unregister a tool.

        Args:
            name: Name of tool to unregister
        """
        if name in self.tools:
            del self.tools[name]
            logger.debug(f"Unregistered tool: {name}")

    def get_tool(self, name: str) -> BaseTool | None:
        """Get a tool by name.

        Args:
            name: Tool name

        Returns:
            Tool instance or None
        """
        return self.tools.get(name)

    def get_all_tools(self) -> list[BaseTool]:
        """Get all registered tools."""
        return list(self.tools.values())

    def get_tool_definitions(self) -> list[dict[str, Any]]:
        """Get tool definitions for LLM.

        Returns:
            List of tool definitions
        """
        return [tool.get_definition() for tool in self.tools.values()]

    def register_all(self) -> None:
        """Register all built-in tools."""
        config = get_config()

        # Web search tool with config
        self.register(
            WebSearchTool(
                provider=config.web_search_provider,
                api_key=config.brave_api_key or None,
                timeout=config.web_search_timeout,
            )
        )

        # Code executor tool
        self.register(CodeExecutorTool())

        logger.info(f"Registered {len(self.tools)} tools")

    def clear(self) -> None:
        """Clear all registered tools."""
        self.tools.clear()


def get_registry() -> ToolRegistry:
    """Get the global tool registry."""
    return ToolRegistry()
