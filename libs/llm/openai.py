"""OpenAI LLM provider implementation."""

import inspect
import json
from collections.abc import AsyncIterator
from typing import Any
import sys
import time

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

# Cache whether the installed OpenAI SDK supports the 'reasoning' parameter
_sdk_supports_reasoning: bool | None = None


def _openai_sdk_supports_reasoning() -> bool:
    """Return True if the installed OpenAI SDK accepts a 'reasoning' kwarg on chat.completions.create."""
    global _sdk_supports_reasoning
    if _sdk_supports_reasoning is not None:
        return _sdk_supports_reasoning
    try:
        sig = inspect.signature(openai.AsyncOpenAI().chat.completions.create)
        _sdk_supports_reasoning = "reasoning" in sig.parameters
    except Exception:
        _sdk_supports_reasoning = False
    return _sdk_supports_reasoning


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



        # Build request
        request_kwargs: dict[str, Any] = {
            "model": model,
            "messages": openai_messages,
        }

        # Handle params for different models
        is_reasoning_model = (
            model.startswith("o1") or
            model.startswith("o3") or
            model.startswith("gpt-5-mini")
        )

        if is_reasoning_model:
            request_kwargs["max_completion_tokens"] = max_tokens
            # Only o3 supports temperature, o1 and gpt-5-mini have fixed temperature
            if model.startswith("o3"):
                request_kwargs["temperature"] = temperature

            # Enable reasoning output for reasoning models (only if SDK supports it)
            if _openai_sdk_supports_reasoning():
                reasoning_effort = kwargs.get("reasoning_effort", "medium")
                request_kwargs["reasoning"] = {"effort": reasoning_effort}
        else:
            request_kwargs["max_tokens"] = max_tokens
            request_kwargs["temperature"] = temperature

        if tools:
            request_kwargs["tools"] = [t.to_openai() for t in tools]

        logger.info(f"DEBUG: OpenAI complete final kwargs keys: {list(request_kwargs.keys())}")

        try:
            response = await self.client.chat.completions.create(**request_kwargs)
        except TypeError as e:
            # Retry without 'reasoning' if the SDK doesn't accept it
            try:
                msg = str(e)
            except Exception:
                msg = ""
            if "reasoning" in msg and "unexpected" in msg.lower():
                logger.warning("OpenAI SDK rejected 'reasoning' kwarg, retrying without it", error=msg)
                request_kwargs.pop("reasoning", None)
                response = await self.client.chat.completions.create(**request_kwargs)
            else:
                raise

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

            # Extract reasoning content robustly
            reasoning_content = (
                getattr(message, "reasoning_content", None) or 
                getattr(message, "reasoning", None)
            )
            if reasoning_content is None and hasattr(message, "model_extra") and message.model_extra:
                reasoning_content = message.model_extra.get("reasoning_content") or message.model_extra.get("reasoning")

            return LLMResponse(
                content=content,
                reasoning_content=reasoning_content,
                tool_calls=tool_calls,
                input_tokens=response.usage.prompt_tokens if response.usage else 0,
                output_tokens=response.usage.completion_tokens if response.usage else 0,
                finish_reason=choice.finish_reason,
                model=response.model,
                raw_response=response,
            )

        except Exception as e:
            # Also log exception via structured logger so it appears in container logs
            logger.exception("Exception while calling OpenAI create", error=str(e), model=model)
            if isinstance(e, openai.APIError):
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
            else:
                raise
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



        # Build request
        request_kwargs: dict[str, Any] = {
            "model": model,
            "messages": openai_messages,
            "stream": True,
            "stream_options": {"include_usage": True},
        }


        # Handle params for different models
        is_reasoning_model = (
            model.startswith("o1") or
            model.startswith("o3") or
            model.startswith("gpt-5-mini")
        )

        if is_reasoning_model:
            request_kwargs["max_completion_tokens"] = max_tokens
            # Only o3 supports temperature, o1 and gpt-5-mini have fixed temperature
            if model.startswith("o3"):
                request_kwargs["temperature"] = temperature

            # Enable reasoning output for reasoning models (only if SDK supports it)
            if _openai_sdk_supports_reasoning():
                reasoning_effort = kwargs.get("reasoning_effort", "medium")
                request_kwargs["reasoning"] = {"effort": reasoning_effort}
        else:
            request_kwargs["max_tokens"] = max_tokens
            request_kwargs["temperature"] = temperature

        if tools:
            request_kwargs["tools"] = [t.to_openai() for t in tools]

        logger.debug("OpenAI stream request kwargs keys: %s", list(request_kwargs.keys()))

        try:
            stream = await self.client.chat.completions.create(**request_kwargs)
        except TypeError as e:
            # Retry without 'reasoning' if the SDK doesn't accept it
            try:
                msg = str(e)
            except Exception:
                msg = ""
            if "reasoning" in msg and "unexpected" in msg.lower():
                logger.warning("OpenAI SDK rejected 'reasoning' kwarg for stream, retrying without it", error=msg)
                request_kwargs.pop("reasoning", None)
                stream = await self.client.chat.completions.create(**request_kwargs)
            else:
                raise

        logger.info("OpenAI stream created, consuming chunks")
        try:
            current_tool_calls: dict[int, dict[str, Any]] = {}
            input_tokens = 0
            output_tokens = 0
            finish_reason = None
            first_chunk = True

            async for chunk in stream:
                if first_chunk:
                    logger.info("First stream chunk received")
                    first_chunk = False
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

                # Handle reasoning content (for reasoning models like o1, o3, gpt-5-mini)
                reasoning_content = None
                try:
                    # Try direct attributes first
                    reasoning_content = getattr(delta, "reasoning_content", None) or getattr(delta, "reasoning", None)

                    # Fallback to model_extra if SDK hasn't mapped the fields yet
                    if reasoning_content is None and hasattr(delta, "model_extra") and delta.model_extra:
                        extra = delta.model_extra
                        if extra:
                            reasoning_content = extra.get("reasoning_content") or extra.get("reasoning")

                    # Debug log to see what fields are available
                    if hasattr(delta, "model_extra") and delta.model_extra:
                        logger.debug(f"Delta model_extra keys: {list(delta.model_extra.keys())}")
                except Exception as e:
                    logger.warning(f"Error extracting reasoning content: {e}")

                if reasoning_content:
                    logger.debug(f"Yielding reasoning content chunk: {len(reasoning_content)} chars")
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

        except Exception as e:
            # Also log exception via structured logger so it appears in container logs
            logger.exception("Exception while calling OpenAI stream create", error=str(e), model=model)
            if isinstance(e, openai.APIError):
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
            raise
