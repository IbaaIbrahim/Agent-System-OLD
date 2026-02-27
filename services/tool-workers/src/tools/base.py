"""Base tool class for all tools."""

from abc import ABC, abstractmethod
from typing import Any, TypeVar

from libs.common.tool_catalog import ToolBehavior, get_tool_metadata

# Type variable for the decorator
T = TypeVar("T", bound="BaseTool")


def catalog_tool(tool_name: str):
    """Load tool metadata from TOOL_CATALOG at class definition time.

    This decorator eliminates duplication between tool classes and TOOL_CATALOG.
    Tool classes no longer need to define name, description, parameters, behavior,
    or required_plan_feature - these are loaded from the catalog.

    Args:
        tool_name: The tool name as defined in TOOL_CATALOG

    Returns:
        Class decorator that sets tool attributes from catalog

    Raises:
        ValueError: If tool_name is not found in TOOL_CATALOG

    Example:
        @catalog_tool("get_current_time")
        class DateTimeTool(BaseTool):
            async def execute(self, arguments, context):
                ...
    """

    def decorator(cls: type[T]) -> type[T]:
        meta = get_tool_metadata(tool_name)
        if not meta:
            raise ValueError(f"Tool '{tool_name}' not found in TOOL_CATALOG")

        cls.name = meta.name
        cls.description = meta.description
        cls.parameters = meta.parameters
        cls.behavior = meta.behavior
        cls.required_plan_feature = meta.required_plan_feature
        return cls

    return decorator


class BaseTool(ABC):
    """Abstract base class for all tools."""

    name: str = "base_tool"
    description: str = "Base tool description"
    parameters: dict[str, Any] = {}
    behavior: ToolBehavior = ToolBehavior.AUTO_EXECUTE
    required_plan_feature: str | None = None

    @abstractmethod
    async def execute(
        self,
        arguments: dict[str, Any],
        context: dict[str, Any],
    ) -> str:
        """Execute the tool.

        Args:
            arguments: Tool arguments from LLM
            context: Execution context (job_id, tenant_id, etc.)

        Returns:
            Tool execution result as string
        """
        pass

    def get_definition(self) -> dict[str, Any]:
        """Get tool definition for LLM.

        Returns:
            Tool definition dictionary
        """
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
            "behavior": self.behavior.value,
        }

    def validate_arguments(self, arguments: dict[str, Any]) -> list[str]:
        """Validate tool arguments.

        Args:
            arguments: Arguments to validate

        Returns:
            List of validation errors (empty if valid)
        """
        errors = []
        required = self.parameters.get("required", [])
        properties = self.parameters.get("properties", {})

        # Check required arguments
        for req in required:
            if req not in arguments:
                errors.append(f"Missing required argument: {req}")

        # Check argument types
        for arg_name, arg_value in arguments.items():
            if arg_name in properties:
                expected_type = properties[arg_name].get("type")
                if expected_type and not self._check_type(arg_value, expected_type):
                    errors.append(
                        f"Invalid type for {arg_name}: "
                        f"expected {expected_type}, got {type(arg_value).__name__}"
                    )

        return errors

    def _check_type(self, value: Any, expected_type: str) -> bool:
        """Check if value matches expected JSON schema type."""
        type_map = {
            "string": str,
            "number": (int, float),
            "integer": int,
            "boolean": bool,
            "array": list,
            "object": dict,
        }
        expected = type_map.get(expected_type)
        if expected is None:
            return True
        return isinstance(value, expected)
