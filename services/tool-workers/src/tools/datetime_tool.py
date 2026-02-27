"""Datetime tool for getting current time and date information."""

from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from libs.common import get_logger

from .base import BaseTool, catalog_tool

logger = get_logger(__name__)


@catalog_tool("get_current_time")
class DateTimeTool(BaseTool):
    """Tool for getting current date and time information.

    Supports multiple timezones and output formats.
    """

    async def execute(
        self,
        arguments: dict[str, Any],
        context: dict[str, Any],
    ) -> str:
        """Get the current date and time.

        Args:
            arguments: Tool arguments (timezone, format)
            context: Execution context (job_id, tenant_id, etc.)

        Returns:
            Current date/time in the requested format
        """
        timezone_str = arguments.get("timezone", "UTC")
        output_format = arguments.get("format", "iso")

        job_id = context.get("job_id", "unknown")

        logger.debug(
            "Getting current time",
            timezone=timezone_str,
            format=output_format,
            job_id=job_id,
        )

        try:
            # Get timezone
            if timezone_str.upper() == "UTC":
                tz = UTC
            else:
                tz = ZoneInfo(timezone_str)

            # Get current time in the specified timezone
            now = datetime.now(tz)

            # Format output
            if output_format == "unix":
                result = str(int(now.timestamp()))
            elif output_format == "human":
                result = now.strftime("%A, %B %d, %Y at %I:%M:%S %p %Z")
            else:  # iso
                result = now.isoformat()

            logger.debug(
                "Current time retrieved",
                result=result,
                timezone=timezone_str,
                job_id=job_id,
            )

            return result

        except Exception as e:
            logger.error(
                "Failed to get current time",
                error=str(e),
                timezone=timezone_str,
                job_id=job_id,
            )
            return f"Error: {str(e)}"
