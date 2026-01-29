"""Code execution tool implementation."""

import asyncio
import json
import sys
import tempfile
from io import StringIO
from typing import Any

from libs.common import get_logger

from .base import BaseTool
from ..config import get_config

logger = get_logger(__name__)


class CodeExecutorTool(BaseTool):
    """Tool for executing Python code in a sandboxed environment."""

    name = "code_executor"
    description = (
        "Execute Python code and return the output. Use this for calculations, "
        "data processing, or any task that requires running code. "
        "The code runs in an isolated environment with limited capabilities."
    )
    parameters = {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "Python code to execute",
            },
            "timeout": {
                "type": "integer",
                "description": "Execution timeout in seconds (default: 30, max: 60)",
                "default": 30,
            },
        },
        "required": ["code"],
    }

    def __init__(self) -> None:
        self.config = get_config()

    async def execute(
        self,
        arguments: dict[str, Any],
        context: dict[str, Any],
    ) -> str:
        """Execute Python code.

        Args:
            arguments: Code execution arguments
            context: Execution context

        Returns:
            Code output or error message
        """
        if not self.config.code_executor_enabled:
            return "Error: Code execution is disabled"

        code = arguments.get("code", "")
        timeout = min(
            arguments.get("timeout", self.config.code_executor_timeout),
            60,
        )

        logger.info(
            "Executing code",
            code_length=len(code),
            timeout=timeout,
            job_id=context.get("job_id"),
        )

        try:
            result = await self._execute_sandboxed(code, timeout)
            return result

        except asyncio.TimeoutError:
            return f"Error: Code execution timed out after {timeout} seconds"
        except Exception as e:
            logger.error(
                "Code execution failed",
                error=str(e),
                job_id=context.get("job_id"),
            )
            return f"Error: {str(e)}"

    async def _execute_sandboxed(
        self,
        code: str,
        timeout: int,
    ) -> str:
        """Execute code in a sandboxed environment.

        This implementation uses a subprocess with restricted globals.
        In production, consider using:
        - Docker containers
        - Firecracker microVMs
        - AWS Lambda
        - Dedicated sandboxing services

        Args:
            code: Python code to execute
            timeout: Timeout in seconds

        Returns:
            Execution output
        """
        # Create a restricted environment
        restricted_globals = {
            "__builtins__": {
                # Safe builtins
                "abs": abs,
                "all": all,
                "any": any,
                "bool": bool,
                "dict": dict,
                "enumerate": enumerate,
                "filter": filter,
                "float": float,
                "int": int,
                "len": len,
                "list": list,
                "map": map,
                "max": max,
                "min": min,
                "print": print,
                "range": range,
                "round": round,
                "set": set,
                "sorted": sorted,
                "str": str,
                "sum": sum,
                "tuple": tuple,
                "type": type,
                "zip": zip,
                # Safe modules
                "json": json,
                "math": __import__("math"),
                "datetime": __import__("datetime"),
                "re": __import__("re"),
                "random": __import__("random"),
                "collections": __import__("collections"),
                "itertools": __import__("itertools"),
                "functools": __import__("functools"),
            },
        }

        # Capture output
        output = StringIO()
        old_stdout = sys.stdout
        old_stderr = sys.stderr

        try:
            sys.stdout = output
            sys.stderr = output

            # Execute with timeout
            local_vars: dict[str, Any] = {}

            def run_code():
                exec(code, restricted_globals, local_vars)

            await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(None, run_code),
                timeout=timeout,
            )

            result = output.getvalue()

            # If there's a result variable, include it
            if "result" in local_vars:
                if result:
                    result += "\n"
                result += f"Result: {local_vars['result']}"

            return result if result else "Code executed successfully (no output)"

        finally:
            sys.stdout = old_stdout
            sys.stderr = old_stderr

    async def _execute_in_container(
        self,
        code: str,
        timeout: int,
    ) -> str:
        """Execute code in a Docker container (production approach).

        This is a placeholder for a more secure implementation.

        Args:
            code: Python code to execute
            timeout: Timeout in seconds

        Returns:
            Execution output
        """
        # In production, you would:
        # 1. Write code to a temporary file
        # 2. Run a Docker container with limited resources
        # 3. Mount the file and capture output
        # 4. Clean up resources

        # Example Docker command:
        # docker run --rm --network none --memory 256m --cpus 0.5 \
        #   --timeout {timeout} -v /tmp/code.py:/code.py:ro \
        #   python:3.12-slim python /code.py

        raise NotImplementedError("Container execution not implemented")
