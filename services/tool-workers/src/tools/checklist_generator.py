"""Checklist generator tool using LLM structured output."""

import json
from typing import Any

from libs.common import get_logger
from libs.llm import get_provider

from .assets import load_json_asset, load_text_asset
from .base import BaseTool, catalog_tool

logger = get_logger(__name__)

# Tool asset folder name
_TOOL_NAME = "checklist_generator"


@catalog_tool("generate_checklist")
class ChecklistGeneratorTool(BaseTool):
    """Tool for generating structured Flowdit checklists.

    This tool uses OpenAI's structured output capabilities to generate
    checklists that conform to the Flowdit schema. The agent calls this
    when the user wants to create a checklist, inspection form, or audit template.
    """

    def __init__(self) -> None:
        """Initialize the checklist generator tool.

        Loads the Flowdit schema and system prompt from files.
        """
        self._schema: dict[str, Any] | None = None
        self._system_prompt: str | None = None

    def _load_schema(self) -> dict[str, Any]:
        """Load and cache the Flowdit checklist schema."""
        if self._schema is None:
            self._schema = load_json_asset(_TOOL_NAME, "schema.json")
            logger.debug("Loaded Flowdit schema", tool=_TOOL_NAME)
        return self._schema

    def _load_system_prompt(self) -> str:
        """Load and cache the system prompt."""
        if self._system_prompt is None:
            self._system_prompt = load_text_asset(_TOOL_NAME, "system_prompt.txt")
            logger.debug("Loaded system prompt", tool=_TOOL_NAME)
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
            context_length=len(user_context),
        )

        try:
            # Load schema and prompt
            schema = self._load_schema()
            system_prompt = self._load_system_prompt()

            logger.debug(
                "Loaded checklist assets",
                schema_keys=len(schema.get("properties", {})),
                schema_definitions=len(schema.get("definitions", {})),
                prompt_length=len(system_prompt),
            )

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

            logger.info(
                "Calling OpenAI structured output API",
                job_id=job_id,
                model="gpt-4o-mini",
                user_prompt_length=len(user_prompt),
            )

            # Generate checklist using structured output (with retry logic)
            result = await provider.complete_structured(
                system=system_prompt,
                user_message=user_prompt,
                json_schema=schema,
                schema_name="FlowditChecklist",
                model="gpt-4o-mini",
                max_retries=3,
                base_delay=2.0,
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
            error_type = type(e).__name__
            logger.error(
                "Checklist generation failed",
                error=str(e),
                error_type=error_type,
                job_id=job_id,
                tenant_id=tenant_id,
                title=title,
                context_length=len(user_context),
            )
            return json.dumps({
                "error": f"{error_type}: {e}",
                "success": False,
                "message": "Failed to generate checklist",
            })
