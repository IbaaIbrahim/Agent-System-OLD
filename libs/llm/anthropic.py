"""Anthropic Claude LLM provider implementation."""

import asyncio
import json
import random
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
        default_model: str = "claude-sonnet-4-5",
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

        except anthropic.APIConnectionError as e:
            logger.error(
                "💀 Anthropic API connection error (network/DNS issue)",
                error=str(e),
                model=model,
                error_type=type(e).__name__,
            )
            raise LLMError(
                provider="anthropic",
                message=f"Anthropic API connection error: {e}. Check network connectivity and DNS configuration.",
                details={"error_type": type(e).__name__, "is_network_error": True},
            )
        except anthropic.APITimeoutError as e:
            logger.error(
                "💀 Anthropic API timeout error",
                error=str(e),
                model=model,
                error_type=type(e).__name__,
            )
            raise LLMError(
                provider="anthropic",
                message=f"Anthropic API timeout: {e}. Check network connectivity.",
                details={"error_type": type(e).__name__, "is_network_error": True},
            )
        except anthropic.APIError as e:
            logger.error(
                "💀 Anthropic API error",
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

        except anthropic.APIConnectionError as e:
            logger.error(
                "💀 Anthropic API connection error during streaming (network/DNS issue)",
                error=str(e),
                model=model,
                error_type=type(e).__name__,
            )
            raise LLMError(
                provider="anthropic",
                message=f"Anthropic API connection error: {e}. Check network connectivity and DNS configuration.",
                details={"error_type": type(e).__name__, "is_network_error": True},
            )
        except anthropic.APITimeoutError as e:
            logger.error(
                "💀 Anthropic API timeout error during streaming",
                error=str(e),
                model=model,
                error_type=type(e).__name__,
            )
            raise LLMError(
                provider="anthropic",
                message=f"Anthropic API timeout: {e}. Check network connectivity.",
                details={"error_type": type(e).__name__, "is_network_error": True},
            )
        except anthropic.APIError as e:
            logger.error(
                "💀 Anthropic streaming error",
                error=str(e),
                model=model,
            )
            raise LLMError(
                provider="anthropic",
                message=f"Anthropic streaming error: {e}",
                details={"error_type": type(e).__name__},
            )

    async def complete_structured(
        self,
        system: str,
        user_message: str,
        json_schema: dict[str, Any],
        schema_name: str = "StructuredOutput",
        model: str | None = None,
        max_retries: int = 3,
        base_delay: float = 2.0,
        timeout: float | None = None,
        max_output_tokens: int | None = None,
    ) -> dict[str, Any]:
        """Generate structured output following a JSON schema.

        Uses Anthropic's tool use with forced tool choice to produce
        structured output matching the schema. The JSON schema becomes
        the tool's input_schema, and tool_choice forces the model to
        call it — returning parsed arguments directly as a dict.

        Includes retry logic with exponential backoff for transient failures.

        Args:
            system: System prompt
            user_message: User message/prompt
            json_schema: JSON schema to validate output against
            schema_name: Name for the schema (default: "StructuredOutput")
            model: Model to use (default: provider default)
            max_retries: Maximum number of retry attempts (default: 3)
            base_delay: Base delay in seconds for exponential backoff (default: 2.0)
            timeout: Per-request timeout in seconds (default: self.timeout * 2.5)
            max_output_tokens: Maximum output tokens (default: 16384)

        Returns:
            Parsed JSON object matching the schema

        Raises:
            LLMError: If generation fails after all retries
        """
        model = self.get_model(model)
        max_tokens = max_output_tokens or 16384
        request_timeout = timeout if timeout is not None else self.timeout * 2.5
        last_error: Exception | None = None

        # Define a tool whose input_schema is the desired JSON schema,
        # then force the model to call it via tool_choice.
        tool_def = {
            "name": schema_name,
            "description": f"Generate structured output matching the {schema_name} schema.",
            "input_schema": json_schema,
        }

        for attempt in range(max_retries):
            try:
                logger.info(
                    "Anthropic structured output request",
                    model=model,
                    schema_name=schema_name,
                    attempt=attempt + 1,
                    max_retries=max_retries,
                    max_tokens=max_tokens,
                    request_timeout_seconds=request_timeout,
                )

                response = await self.client.messages.create(
                    model=model,
                    system=system,
                    messages=[{"role": "user", "content": user_message}],
                    tools=[tool_def],
                    tool_choice={"type": "tool", "name": schema_name},
                    max_tokens=max_tokens,
                    timeout=request_timeout,
                )

                # Check for truncation — stop_reason "max_tokens" means
                # the tool call JSON was cut short, retry.
                if response.stop_reason == "max_tokens":
                    logger.warning(
                        "Anthropic response truncated (max_tokens), retrying",
                        model=model,
                        attempt=attempt + 1,
                        max_retries=max_retries,
                        max_tokens=max_tokens,
                    )
                    if attempt < max_retries - 1:
                        delay = base_delay * (2 ** attempt) + random.uniform(0, 1)
                        await asyncio.sleep(delay)
                        continue
                    else:
                        raise LLMError(
                            provider="anthropic",
                            message=f"Response truncated (max_tokens) after {max_retries} attempts",
                            details={"stop_reason": "max_tokens", "attempts": max_retries},
                        )

                # Extract the tool call arguments (already a parsed dict)
                for block in response.content:
                    if block.type == "tool_use" and block.name == schema_name:
                        logger.info(
                            "Anthropic structured output received",
                            model=model,
                            schema_name=schema_name,
                            attempt=attempt + 1,
                            input_tokens=response.usage.input_tokens,
                            output_tokens=response.usage.output_tokens,
                        )
                        return block.input

                # No tool call found — unexpected
                raise LLMError(
                    provider="anthropic",
                    message=f"No tool call in response for schema {schema_name}",
                    details={"model": model, "stop_reason": response.stop_reason},
                )

            except anthropic.APIError as e:
                last_error = e
                error_type = type(e).__name__

                is_retryable = isinstance(e, (
                    anthropic.APITimeoutError,
                    anthropic.RateLimitError,
                    anthropic.InternalServerError,
                    anthropic.APIConnectionError,
                ))

                if is_retryable and attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt) + random.uniform(0, 1)
                    logger.warning(
                        "Anthropic API error, retrying",
                        error=str(e),
                        error_type=error_type,
                        model=model,
                        attempt=attempt + 1,
                        max_retries=max_retries,
                        retry_delay_seconds=round(delay, 2),
                    )
                    await asyncio.sleep(delay)
                    continue
                else:
                    logger.error(
                        "Anthropic API error during structured output (no more retries)",
                        error=str(e),
                        error_type=error_type,
                        model=model,
                        attempt=attempt + 1,
                        max_retries=max_retries,
                    )
                    raise LLMError(
                        provider="anthropic",
                        message=f"Anthropic API error: {e}",
                        details={"error_type": error_type, "attempts": attempt + 1},
                    )

        # Should not reach here, but just in case
        raise LLMError(
            provider="anthropic",
            message=f"Anthropic API error after {max_retries} attempts: {last_error}",
            details={"error_type": type(last_error).__name__ if last_error else "Unknown"},
        )
