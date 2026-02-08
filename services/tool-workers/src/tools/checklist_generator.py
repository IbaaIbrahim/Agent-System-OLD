"""Checklist generator tool using LLM structured output."""

import json
from pathlib import Path
from typing import Any

from libs.common import get_logger
from libs.llm import get_provider

from .base import BaseTool, ToolCategory

logger = get_logger(__name__)

# Resolve paths relative to repo root
_REPO_ROOT = Path(__file__).parent.parent.parent.parent.parent.parent
_SCHEMA_PATH = _REPO_ROOT / "old" / "constants" / "data" / "schema.json"
_SYSTEM_PROMPT_PATH = _REPO_ROOT / "old" / "constants" / "data" / "system_prompt.txt"


class ChecklistGeneratorTool(BaseTool):
    """Tool for generating structured Flowdit checklists.

    This tool uses OpenAI's structured output capabilities to generate
    checklists that conform to the Flowdit schema. The agent calls this
    when the user wants to create a checklist, inspection form, or audit template.
    """

    name = "generate_checklist"
    description = (
        "Generate a structured Flowdit checklist based on conversation context. "
        "Use this when the user wants to create a checklist, inspection form, "
        "audit template, or any structured data collection workflow. "
        "The tool will generate a complete checklist with sections and items."
    )
    parameters = {
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "Title for the checklist",
            },
            "description": {
                "type": "string",
                "description": "Brief description of the checklist purpose",
            },
            "context": {
                "type": "string",
                "description": (
                    "Full conversation context and user requirements. "
                    "Include all details about what items, sections, and structure the user wants."
                ),
            },
            "language": {
                "type": "string",
                "description": "Language code for the checklist content (default: en)",
                "default": "en",
            },
        },
        "required": ["title", "context"],
    }
    category = ToolCategory.CONFIGURABLE

    def __init__(self) -> None:
        """Initialize the checklist generator tool.

        Loads the Flowdit schema and system prompt from files.
        """
        self._schema: dict[str, Any] | None = None
        self._system_prompt: str | None = None

    def _load_schema(self) -> dict[str, Any]:
        """Load and cache the Flowdit checklist schema."""
        if self._schema is None:
            if not _SCHEMA_PATH.exists():
                raise FileNotFoundError(f"Checklist schema not found at: {_SCHEMA_PATH}")
            with open(_SCHEMA_PATH, encoding="utf-8") as f:
                self._schema = json.load(f)
            logger.debug("Loaded Flowdit schema", path=str(_SCHEMA_PATH))
        return self._schema

    def _load_system_prompt(self) -> str:
        """Load and cache the system prompt."""
        if self._system_prompt is None:
            if not _SYSTEM_PROMPT_PATH.exists():
                raise FileNotFoundError(
                    f"System prompt not found at: {_SYSTEM_PROMPT_PATH}"
                )
            with open(_SYSTEM_PROMPT_PATH, encoding="utf-8") as f:
                self._system_prompt = f.read().strip()
            logger.debug("Loaded system prompt", path=str(_SYSTEM_PROMPT_PATH))
        return self._system_prompt

    async def execute(
        self,
        arguments: dict[str, Any],
        context: dict[str, Any],
    ) -> str:
        """Generate a Flowdit checklist using LLM structured output.

        Args:
            arguments: Tool arguments (title, description, context, language)
            context: Execution context (job_id, tenant_id, etc.)

        Returns:
            JSON string of the generated checklist
        """
        title = arguments.get("title", "Untitled Checklist")
        description = arguments.get("description", "")
        user_context = arguments.get("context", "")
        language = arguments.get("language", "en")

        job_id = context.get("job_id", "unknown")
        tenant_id = context.get("tenant_id", "unknown")

        logger.info(
            "Generating checklist",
            title=title,
            language=language,
            job_id=job_id,
            tenant_id=tenant_id,
        )

        try:
            # Load schema and prompt
            schema = self._load_schema()
            system_prompt = self._load_system_prompt()

            # Build user prompt with requirements
            user_prompt = f"""
Title: {title}
Description: {description}
Language: {language}

User Requirements:
{user_context}
"""

            # Get OpenAI provider for structured output
            provider = get_provider("openai")

            # Generate checklist using structured output
            result = await provider.complete_structured(
                system=system_prompt,
                user_message=user_prompt,
                json_schema=schema,
                schema_name="FlowditChecklist",
                model="gpt-4o-mini",
            )

            logger.info(
                "Checklist generated successfully",
                title=title,
                item_count=len(result.get("items", [])),
                job_id=job_id,
            )

            return json.dumps(result, indent=2, ensure_ascii=False)

        except FileNotFoundError as e:
            logger.error(
                "Missing schema or prompt file",
                error=str(e),
                job_id=job_id,
            )
            return json.dumps({
                "error": str(e),
                "success": False,
                "message": "Checklist generation configuration error",
            })
        except Exception as e:
            logger.error(
                "Checklist generation failed",
                error=str(e),
                job_id=job_id,
                tenant_id=tenant_id,
            )
            return json.dumps({
                "error": str(e),
                "success": False,
                "message": "Failed to generate checklist",
            })
