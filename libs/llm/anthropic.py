"""Anthropic Claude LLM provider implementation."""

import json
from collections.abc import AsyncIterator
from typing import Any

import anthropic

from libs.common.exceptions import LLMError
from libs.common.logging import get_logger
from libs.llm.base import (
    LLMMessage,
    LLMProvider,
    LLMResponse,
    LLMStreamChunk,
    ToolCall,
    ToolDefinition,
)

logger = get_logger(__name__)


class AnthropicProvider(LLMProvider):
    """Anthropic Claude provider implementation."""

    provider_name = "anthropic"

    def __init__(
        self,
        api_key: str,
        default_model: str = "claude-sonnet-4-20250514",
        timeout: int = 60,
    ) -> None:
        super().__init__(api_key, default_model, timeout)
        self.client = anthropic.AsyncAnthropic(api_key=api_key, timeout=timeout)

    async def complete(
        self,
        messages: list[LLMMessage],
        model: str | None = None,
        system: str | None = None,
        tools: list[ToolDefinition] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs: Any,
    ) -> LLMResponse:
        """Generate a completion using Claude."""
        model = self.get_model(model)

        # Convert messages to Anthropic format
        anthropic_messages = []
        for msg in messages:
            if msg.role.value == "system":
                # System messages are handled separately
                continue
            anthropic_messages.append(msg.to_anthropic())

        # Build request
        request_kwargs: dict[str, Any] = {
            "model": model,
            "messages": anthropic_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }

        if system:
            request_kwargs["system"] = system

        if tools:
            request_kwargs["tools"] = [t.to_anthropic() for t in tools]

        # Enable extended thinking if budget tokens specified
        thinking_budget = kwargs.get("thinking_budget_tokens")
        if thinking_budget:
            request_kwargs["thinking"] = {
                "type": "enabled",
                "budget_tokens": thinking_budget,
            }

        try:
            response = await self.client.messages.create(**request_kwargs)

            # Parse response
            content = None
            tool_calls = None
            reasoning_content = None

            for block in response.content:
                if block.type == "text":
                    content = block.text
                elif block.type == "thinking":
                    reasoning_content = block.thinking
                elif block.type == "tool_use":
                    if tool_calls is None:
                        tool_calls = []
                    tool_calls.append(
                        ToolCall(
                            id=block.id,
                            name=block.name,
                            arguments=block.input,
                        )
                    )

            return LLMResponse(
                content=content,
                reasoning_content=reasoning_content,
                tool_calls=tool_calls,
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                finish_reason=response.stop_reason,
                model=response.model,
                raw_response=response,
            )

        except anthropic.APIError as e:
            logger.error(
                "Anthropic API error",
                error=str(e),
                model=model,
            )
            raise LLMError(
                provider="anthropic",
                message=f"Anthropic API error: {e}",
                details={"error_type": type(e).__name__},
            )

    async def stream(
        self,
        messages: list[LLMMessage],
        model: str | None = None,
        system: str | None = None,
        tools: list[ToolDefinition] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        **kwargs: Any,
    ) -> AsyncIterator[LLMStreamChunk]:
        """Stream a completion using Claude."""
        model = self.get_model(model)

        # Convert messages to Anthropic format
        anthropic_messages = []
        for msg in messages:
            if msg.role.value == "system":
                continue
            anthropic_messages.append(msg.to_anthropic())

        # Build request
        request_kwargs: dict[str, Any] = {
            "model": model,
            "messages": anthropic_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }

        if system:
            request_kwargs["system"] = system

        if tools:
            request_kwargs["tools"] = [t.to_anthropic() for t in tools]

        # Enable extended thinking if budget tokens specified
        thinking_budget = kwargs.get("thinking_budget_tokens")
        if thinking_budget:
            request_kwargs["thinking"] = {
                "type": "enabled",
                "budget_tokens": thinking_budget,
            }

        try:
            async with self.client.messages.stream(**request_kwargs) as stream:
                current_tool_calls: dict[int, dict[str, Any]] = {}
                input_tokens = 0
                output_tokens = 0

                async for event in stream:
                    if event.type == "message_start":
                        if hasattr(event.message, "usage"):
                            input_tokens = event.message.usage.input_tokens

                    elif event.type == "content_block_start":
                        if event.content_block.type == "tool_use":
                            current_tool_calls[event.index] = {
                                "id": event.content_block.id,
                                "name": event.content_block.name,
                                "arguments": "",
                            }
                        # Thinking blocks are tracked but content comes via deltas

                    elif event.type == "content_block_delta":
                        if event.delta.type == "text_delta":
                            yield LLMStreamChunk(content=event.delta.text)
                        elif event.delta.type == "thinking_delta":
                            # Stream thinking/reasoning content
                            yield LLMStreamChunk(reasoning_content=event.delta.thinking)
                        elif event.delta.type == "input_json_delta":
                            if event.index in current_tool_calls:
                                current_tool_calls[event.index][
                                    "arguments"
                                ] += event.delta.partial_json

                    elif event.type == "content_block_stop":
                        if event.index in current_tool_calls:
                            tc_data = current_tool_calls[event.index]
                            try:
                                args = json.loads(tc_data["arguments"])
                            except json.JSONDecodeError:
                                args = {}

                            yield LLMStreamChunk(
                                tool_calls=[
                                    ToolCall(
                                        id=tc_data["id"],
                                        name=tc_data["name"],
                                        arguments=args,
                                    )
                                ]
                            )
                            del current_tool_calls[event.index]

                    elif event.type == "message_delta":
                        if hasattr(event, "usage"):
                            output_tokens = event.usage.output_tokens
                        yield LLMStreamChunk(
                            is_final=True,
                            finish_reason=event.delta.stop_reason,
                            input_tokens=input_tokens,
                            output_tokens=output_tokens,
                        )

        except anthropic.APIError as e:
            logger.error(
                "Anthropic streaming error",
                error=str(e),
                model=model,
            )
            raise LLMError(
                provider="anthropic",
                message=f"Anthropic streaming error: {e}",
                details={"error_type": type(e).__name__},
            )
