"""LLM interaction service."""

from collections.abc import AsyncIterator
from typing import Any

from libs.common import get_logger
from libs.llm import (
    LLMProvider,
    LLMResponse,
    LLMStreamChunk,
    ToolDefinition,
    get_provider,
)
from ..engine.state import AgentState
import json
import time

logger = get_logger(__name__)


class LLMService:
    """Service for interacting with LLM providers."""

    def __init__(self) -> None:
        self._providers: dict[str, LLMProvider] = {}

    def _get_provider(self, provider_name: str) -> LLMProvider:
        """Get or create an LLM provider instance.

        Args:
            provider_name: Provider name (anthropic or openai)

        Returns:
            LLMProvider instance
        """
        if provider_name not in self._providers:
            self._providers[provider_name] = get_provider(provider_name)
        return self._providers[provider_name]

    def _filter_tools_by_config(
        self,
        tools: list[dict[str, Any]] | None,
        plan_tools: list[str] | None,
        enabled_tools: list[str] | None,
    ) -> list[dict[str, Any]] | None:
        """Filter tools based on plan access and user preferences.

        Args:
            tools: All available tool definitions
            plan_tools: Tools allowed by subscription plan (from API Gateway)
            enabled_tools: Tools enabled by user preference

        Returns:
            Filtered tools list
        """
        if not tools:
            return None

        filtered = []
        for tool in tools:
            category = tool.get("category", "builtin")
            name = tool["name"]

            if category == "builtin":
                # Built-in tools are always included
                filtered.append(tool)
            elif category == "configurable":
                # Configurable tools require both plan access and user enablement
                plan_allowed = plan_tools is None or name in plan_tools
                user_enabled = enabled_tools is None or name in enabled_tools
                if plan_allowed and user_enabled:
                    filtered.append(tool)
            elif category == "client_side":
                # Client-side tools only need plan access (frontend handles user toggle)
                plan_allowed = plan_tools is None or name in plan_tools
                if plan_allowed:
                    filtered.append(tool)

        return filtered if filtered else None

    def _build_system_prompt_with_tool_info(
        self,
        base_prompt: str | None,
        tools: list[dict[str, Any]] | None,
        plan_tools: list[str] | None,
        enabled_tools: list[str] | None,
        effort_level: str | None = None,
    ) -> str | None:
        """Build system prompt with information about disabled tools.

        Three-layer system prompt:
        1. Default layer: Agent scope and tool orchestration instructions
        2. User layer: Optional user-provided system prompt
        3. Tool context layer: Auto-injected disabled tool info

        Args:
            base_prompt: User-provided system prompt (optional)
            tools: All available tool definitions
            plan_tools: Tools allowed by plan
            enabled_tools: Tools enabled by user

        Returns:
            Enhanced system prompt with all layers
        """
        from ..prompts.default_system_prompt import DEFAULT_SYSTEM_PROMPT

        # Layer 1: Default orchestration prompt (always present)
        final_prompt = DEFAULT_SYSTEM_PROMPT

        # Layer 1.5: Effort level behavioral directive
        if effort_level:
            from ..prompts.effort_levels import get_effort_config

            effort_config = get_effort_config(effort_level)
            final_prompt += (
                f"\n\n## Effort Level\n\n{effort_config.prompt_section}"
            )

        # Layer 2: User-provided system prompt (optional override/extension)
        if base_prompt:
            final_prompt += f"\n\n## Additional Instructions\n\n{base_prompt}"

        # Layer 3: Tool availability context (auto-injected)
        if tools:
            plan_locked = []
            user_disabled = []

            for tool in tools:
                category = tool.get("category", "builtin")
                name = tool["name"]

                if category in ("configurable", "client_side"):
                    # Check plan access
                    if plan_tools is not None and name not in plan_tools:
                        plan_locked.append(name)
                    # Check user enablement (only if plan allows)
                    elif enabled_tools is not None and name not in enabled_tools:
                        user_disabled.append(name)

            if plan_locked or user_disabled:
                # Build tool availability context
                disabled_info_parts = []
                if plan_locked:
                    disabled_info_parts.append(
                        f"The following tools require a plan upgrade: {', '.join(plan_locked)}. "
                        "If the user's request would benefit from one of these tools, politely inform "
                        "them that they can upgrade their plan to access this feature."
                    )
                if user_disabled:
                    disabled_info_parts.append(
                        f"The following tools are available but currently disabled by the user: "
                        f"{', '.join(user_disabled)}. If the user's request would benefit from one "
                        "of these tools, politely inform them that they can enable it in the chat settings."
                    )

                disabled_info = "\n\n".join(disabled_info_parts)
                final_prompt += f"\n\n## Tool Availability\n\n{disabled_info}"

        return final_prompt

    def _build_tools(
        self,
        tools: list[dict[str, Any]] | None,
    ) -> list[ToolDefinition] | None:
        """Convert tool dicts to ToolDefinition objects.

        Args:
            tools: List of tool definitions as dicts

        Returns:
            List of ToolDefinition objects or None
        """
        if not tools:
            return None

        return [
            ToolDefinition(
                name=t["name"],
                description=t["description"],
                parameters=t["parameters"],
            )
            for t in tools
        ]

    async def complete(self, state: AgentState) -> LLMResponse:
        """Generate a completion for the current state.

        Args:
            state: Current agent state

        Returns:
            LLM response
        """
        provider = self._get_provider(state.provider)

        # Extract tool config from metadata
        plan_tools = state.metadata.get("plan_tools") if state.metadata else None
        enabled_tools = state.metadata.get("enabled_tools") if state.metadata else None

        # Filter tools based on plan and user preferences
        filtered_tools = self._filter_tools_by_config(
            state.tools, plan_tools, enabled_tools
        )
        tools = self._build_tools(filtered_tools)

        logger.info(
            "Tool filtering details",
            job_id=str(state.job_id),
            all_tools=[t["name"] for t in (state.tools or [])],
            plan_tools=plan_tools,
            enabled_tools=enabled_tools,
            filtered_tools=[t["name"] for t in (filtered_tools or [])],
        )

        # Extract effort level from metadata
        effort_level = state.metadata.get("effort_level") if state.metadata else None

        # Enhance system prompt with disabled tool info
        system_prompt = self._build_system_prompt_with_tool_info(
            state.system_prompt, state.tools, plan_tools, enabled_tools,
            effort_level=effort_level,
        )

        logger.debug(
            "Calling LLM",
            job_id=str(state.job_id),
            provider=state.provider,
            model=state.model,
            message_count=len(state.messages),
        )
        # Visible info log for container stdout
        logger.info(
            "LLM call params",
            job_id=str(state.job_id),
            provider=state.provider,
            model=state.model,
            message_count=len(state.messages),
            metadata_keys=list(state.metadata.keys()) if state.metadata else None,
            filtered_tools=[t["name"] for t in (filtered_tools or [])],
        )

        # Pass thinking/reasoning configuration from metadata if present
        thinking_budget = state.metadata.get("thinking_budget_tokens")  # Anthropic
        reasoning_effort = state.metadata.get("reasoning_effort")  # OpenAI

        response = await provider.complete(
            messages=state.messages,
            model=state.model,
            system=system_prompt,
            tools=tools,
            temperature=state.temperature,
            max_tokens=state.max_tokens,
            thinking_budget_tokens=thinking_budget,
            reasoning_effort=reasoning_effort,
        )

        logger.debug(
            "LLM response received",
            job_id=str(state.job_id),
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            has_tool_calls=bool(response.tool_calls),
            finish_reason=response.finish_reason,
        )
        logger.info(
            "LLM response summary",
            job_id=str(state.job_id),
            has_content=bool(response.content),
            content_len=len(response.content) if response.content else 0,
            has_reasoning=bool(response.reasoning_content),
            tool_calls_count=len(response.tool_calls) if response.tool_calls else 0,
        )

        return response

    async def stream(self, state: AgentState) -> AsyncIterator[LLMStreamChunk]:
        """Stream a completion for the current state.

        Args:
            state: Current agent state

        Yields:
            LLM stream chunks
        """
        provider = self._get_provider(state.provider)

        # Extract tool config from metadata
        plan_tools = state.metadata.get("plan_tools") if state.metadata else None
        enabled_tools = state.metadata.get("enabled_tools") if state.metadata else None

        # Filter tools based on plan and user preferences
        filtered_tools = self._filter_tools_by_config(
            state.tools, plan_tools, enabled_tools
        )
        tools = self._build_tools(filtered_tools)

        logger.info(
            "Tool filtering details (streaming)",
            job_id=str(state.job_id),
            all_tools=[t["name"] for t in (state.tools or [])],
            plan_tools=plan_tools,
            enabled_tools=enabled_tools,
            filtered_tools=[t["name"] for t in (filtered_tools or [])],
        )

        # Extract effort level from metadata
        effort_level = state.metadata.get("effort_level") if state.metadata else None

        # Enhance system prompt with disabled tool info
        system_prompt = self._build_system_prompt_with_tool_info(
            state.system_prompt, state.tools, plan_tools, enabled_tools,
            effort_level=effort_level,
        )

        logger.debug(
            "Starting LLM stream",
            job_id=str(state.job_id),
            provider=state.provider,
            model=state.model,
        )
        # Visible info log for container stdout
        logger.info(
            "LLM stream params",
            job_id=str(state.job_id),
            provider=state.provider,
            model=state.model,
            message_count=len(state.messages),
            metadata_keys=list(state.metadata.keys()) if state.metadata else None,
            filtered_tools=[t["name"] for t in (filtered_tools or [])],
        )

        # Pass thinking/reasoning configuration from metadata if present
        thinking_budget = state.metadata.get("thinking_budget_tokens")  # Anthropic
        reasoning_effort = state.metadata.get("reasoning_effort")  # OpenAI

        async for chunk in provider.stream(
            messages=state.messages,
            model=state.model,
            system=system_prompt,
            tools=tools,
            temperature=state.temperature,
            max_tokens=state.max_tokens,
            thinking_budget_tokens=thinking_budget,
            reasoning_effort=reasoning_effort,
        ):
            logger.debug(
                "llm_service.stream yielding chunk",
                job_id=str(state.job_id),
                has_content=bool(getattr(chunk, "content", None)),
                content_len=len(getattr(chunk, "content", "")) if getattr(chunk, "content", None) else 0,
                has_reasoning=bool(getattr(chunk, "reasoning_content", None)),
                reasoning_len=len(getattr(chunk, "reasoning_content", "")) if getattr(chunk, "reasoning_content", None) else 0,
                is_final=bool(getattr(chunk, "is_final", False)),
            )
            yield chunk

            if chunk.is_final:
                logger.debug(
                    "LLM stream complete",
                    job_id=str(state.job_id),
                    input_tokens=chunk.input_tokens,
                    output_tokens=chunk.output_tokens,
                    finish_reason=chunk.finish_reason,
                )
