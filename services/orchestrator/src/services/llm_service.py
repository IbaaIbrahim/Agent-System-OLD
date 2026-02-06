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
        tools = self._build_tools(state.tools)

        logger.debug(
            "Calling LLM",
            job_id=str(state.job_id),
            provider=state.provider,
            model=state.model,
            message_count=len(state.messages),
        )

        response = await provider.complete(
            messages=state.messages,
            model=state.model,
            system=state.system_prompt,
            tools=tools,
            temperature=state.temperature,
            max_tokens=state.max_tokens,
        )

        logger.debug(
            "LLM response received",
            job_id=str(state.job_id),
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            has_tool_calls=bool(response.tool_calls),
            finish_reason=response.finish_reason,
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
        tools = self._build_tools(state.tools)

        logger.debug(
            "Starting LLM stream",
            job_id=str(state.job_id),
            provider=state.provider,
            model=state.model,
        )

        async for chunk in provider.stream(
            messages=state.messages,
            model=state.model,
            system=state.system_prompt,
            tools=tools,
            temperature=state.temperature,
            max_tokens=state.max_tokens,
        ):
            yield chunk

            if chunk.is_final:
                logger.debug(
                    "LLM stream complete",
                    job_id=str(state.job_id),
                    input_tokens=chunk.input_tokens,
                    output_tokens=chunk.output_tokens,
                    finish_reason=chunk.finish_reason,
                )
