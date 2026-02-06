"""OpenAI LLM provider implementation."""

import json
from collections.abc import AsyncIterator
from typing import Any
import sys

import openai

from libs.common.exceptions import LLMError
from libs.common.logging import get_logger
from libs.llm.base import (
    LLMMessage,
    LLMProvider,
    LLMResponse,
    LLMStreamChunk,
    MessageRole,
    ToolCall,
    ToolDefinition,
)

logger = get_logger(__name__)


class OpenAIProvider(LLMProvider):
    """OpenAI GPT provider implementation."""

    provider_name = "openai"

    def __init__(
        self,
        api_key: str,
        default_model: str = "gpt-4-turbo-preview",
    ) -> None:
        super().__init__(api_key, default_model)
        self.client = openai.AsyncOpenAI(api_key=api_key)

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
        """Generate a completion using OpenAI."""
        model = self.get_model(model)

        # Convert messages to OpenAI format
        openai_messages = []

        # Add system message first
        if system:
            openai_messages.append({"role": "system", "content": system})

        for msg in messages:
            if msg.role == MessageRole.SYSTEM:
                openai_messages.append({"role": "system", "content": msg.content})
            else:
                openai_messages.append(msg.to_openai())

        # DEBUG: Print model info
        print(f"XXX DEBUG: complete model='{model}' type={type(model)}")
        sys.stdout.flush()

        # Build request
        request_kwargs: dict[str, Any] = {
            "model": model,
            "messages": openai_messages,
        }

        # Handle params for different models
        if model.startswith("o1") or model.startswith("gpt-5-mini"):
            request_kwargs["max_completion_tokens"] = max_tokens
            # o1 models don't support temperature (fixed at 1)
        elif model.startswith("o3"):
            request_kwargs["max_completion_tokens"] = max_tokens
            request_kwargs["temperature"] = temperature
        else:
            request_kwargs["max_tokens"] = max_tokens
            request_kwargs["temperature"] = temperature

        if tools:
            request_kwargs["tools"] = [t.to_openai() for t in tools]

        # DEBUG: Print final kwargs
        print(f"XXX DEBUG: complete kwargs={list(request_kwargs.keys())}")
        sys.stdout.flush()

        logger.info(f"DEBUG: OpenAI complete final kwargs keys: {list(request_kwargs.keys())}")

        try:
            response = await self.client.chat.completions.create(**request_kwargs)

            # Parse response
            choice = response.choices[0]
            message = choice.message

            content = message.content
            tool_calls = None

            if message.tool_calls:
                tool_calls = []
                for tc in message.tool_calls:
                    try:
                        args = json.loads(tc.function.arguments)
                    except json.JSONDecodeError:
                        args = {}

                    tool_calls.append(
                        ToolCall(
                            id=tc.id,
                            name=tc.function.name,
                            arguments=args,
                        )
                    )

            return LLMResponse(
                content=content,
                reasoning_content=getattr(message, "reasoning_content", None) or getattr(message, "reasoning", None),
                tool_calls=tool_calls,
                input_tokens=response.usage.prompt_tokens if response.usage else 0,
                output_tokens=response.usage.completion_tokens if response.usage else 0,
                finish_reason=choice.finish_reason,
                model=response.model,
                raw_response=response,
            )

        except openai.APIError as e:
            logger.error(
                "OpenAI API error",
                error=str(e),
                model=model,
            )
            raise LLMError(
                provider="openai",
                message=f"OpenAI API error: {e}",
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
        """Stream a completion using OpenAI."""
        model = self.get_model(model)

        # Convert messages to OpenAI format
        openai_messages = []

        if system:
            openai_messages.append({"role": "system", "content": system})

        for msg in messages:
            if msg.role == MessageRole.SYSTEM:
                openai_messages.append({"role": "system", "content": msg.content})
            else:
                openai_messages.append(msg.to_openai())

        # DEBUG: Print model info
        print(f"XXX DEBUG: stream model='{model}' type={type(model)}")
        sys.stdout.flush()

        # Build request
        request_kwargs: dict[str, Any] = {
            "model": model,
            "messages": openai_messages,
            "stream": True,
            "stream_options": {"include_usage": True},
        }


        # Handle params for different models
        if model.startswith("o1") or model.startswith("gpt-5-mini"):
            request_kwargs["max_completion_tokens"] = max_tokens
            # o1 models don't support temperature (fixed at 1)
        elif model.startswith("o3"):
            request_kwargs["max_completion_tokens"] = max_tokens
            request_kwargs["temperature"] = temperature
        else:
            request_kwargs["max_tokens"] = max_tokens
            request_kwargs["temperature"] = temperature

        if tools:
            request_kwargs["tools"] = [t.to_openai() for t in tools]

        # DEBUG: Print final kwargs
        print(f"XXX DEBUG: stream kwargs={list(request_kwargs.keys())}")
        sys.stdout.flush()

        logger.info(f"DEBUG: OpenAI stream final kwargs keys: {list(request_kwargs.keys())}")

        try:
            stream = await self.client.chat.completions.create(**request_kwargs)

            current_tool_calls: dict[int, dict[str, Any]] = {}
            input_tokens = 0
            output_tokens = 0
            finish_reason = None

            async for chunk in stream:
                if not chunk.choices:
                    # Usage info comes in final chunk
                    if chunk.usage:
                        input_tokens = chunk.usage.prompt_tokens
                        output_tokens = chunk.usage.completion_tokens
                    continue

                choice = chunk.choices[0]
                delta = choice.delta

                if choice.finish_reason:
                    finish_reason = choice.finish_reason

                # Handle content
                if delta.content:
                    yield LLMStreamChunk(content=delta.content)

                # Handle reasoning content (for o1 models)
                reasoning_content = getattr(delta, "reasoning_content", None) or getattr(delta, "reasoning", None)
                if reasoning_content:
                    yield LLMStreamChunk(reasoning_content=reasoning_content)

                # Handle tool calls
                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        idx = tc.index
                        if idx not in current_tool_calls:
                            current_tool_calls[idx] = {
                                "id": tc.id or "",
                                "name": tc.function.name if tc.function else "",
                                "arguments": "",
                            }
                        if tc.id:
                            current_tool_calls[idx]["id"] = tc.id
                        if tc.function:
                            if tc.function.name:
                                current_tool_calls[idx]["name"] = tc.function.name
                            if tc.function.arguments:
                                current_tool_calls[idx][
                                    "arguments"
                                ] += tc.function.arguments

                # Handle finish (but wait for usage chunks to arrive before yielding is_final)
                if choice.finish_reason:
                    # Emit any pending tool calls immediately
                    if current_tool_calls:
                        tool_calls = []
                        for tc_data in current_tool_calls.values():
                            try:
                                args = json.loads(tc_data["arguments"])
                            except json.JSONDecodeError:
                                args = {}
                            tool_calls.append(
                                ToolCall(
                                    id=tc_data["id"],
                                    name=tc_data["name"],
                                    arguments=args,
                                )
                            )
                        yield LLMStreamChunk(tool_calls=tool_calls)
                        # Clear to avoid double yielding
                        current_tool_calls = {}

            # Yield one final chunk with all accumulated metadata
            yield LLMStreamChunk(
                is_final=True,
                finish_reason=finish_reason,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )

        except openai.APIError as e:
            logger.error(
                "OpenAI streaming error",
                error=str(e),
                model=model,
            )
            raise LLMError(
                provider="openai",
                message=f"OpenAI streaming error: {e}",
                details={"error_type": type(e).__name__},
            )
