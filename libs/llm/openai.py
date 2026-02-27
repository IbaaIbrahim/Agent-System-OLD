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

# Cache whether the installed OpenAI SDK supports the 'reasoning_effort' parameter
_sdk_supports_reasoning: bool | None = None


def _openai_sdk_supports_reasoning() -> bool:
    """Return True if the installed OpenAI SDK accepts 'reasoning_effort' kwarg on chat.completions.create."""
    global _sdk_supports_reasoning
    if _sdk_supports_reasoning is not None:
        return _sdk_supports_reasoning
    try:
        sig = inspect.signature(openai.AsyncOpenAI().chat.completions.create)
        _sdk_supports_reasoning = "reasoning_effort" in sig.parameters
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
        timeout: int = 60,
    ) -> None:
        super().__init__(api_key, default_model, timeout)
        self.client = openai.AsyncOpenAI(api_key=api_key, timeout=timeout)

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
        # Reasoning models (o1, o3, o4, gpt-5-mini) require max_completion_tokens and may have temperature restrictions
        is_reasoning_model = (
            model.startswith("o1") or
            model.startswith("o3") or
            model.startswith("o4") or
            model.startswith("gpt-5-mini")
        )

        if is_reasoning_model:
            request_kwargs["max_completion_tokens"] = max_tokens
            # Only o3 supports temperature, o1, o4, and gpt-5-mini have fixed temperature
            if model.startswith("o3"):
                request_kwargs["temperature"] = temperature

            # Enable reasoning output for reasoning models (only if SDK supports it)
            if _openai_sdk_supports_reasoning():
                reasoning_effort = kwargs.get("reasoning_effort", "medium")
                request_kwargs["reasoning_effort"] = reasoning_effort
        else:
            request_kwargs["max_tokens"] = max_tokens
            request_kwargs["temperature"] = temperature

        if tools:
            request_kwargs["tools"] = [t.to_openai() for t in tools]

        try:
            response = await self.client.chat.completions.create(**request_kwargs)
        except TypeError as e:
            # Retry without 'reasoning' if the SDK doesn't accept it
            try:
                msg = str(e)
            except Exception:
                msg = ""
            if "reasoning_effort" in msg and "unexpected" in msg.lower():
                logger.warning("💀 OpenAI SDK rejected 'reasoning_effort' kwarg, retrying without it", error=msg)
                request_kwargs.pop("reasoning_effort", None)
                response = await self.client.chat.completions.create(**request_kwargs)
            else:
                raise
        except openai.BadRequestError as e:
            # Handle models that require max_completion_tokens instead of max_tokens
            # or models that don't support custom temperature
            # Extract error message from exception - try multiple sources
            error_msg = str(e)
            error_message_attr = getattr(e, "message", None)
            error_body = getattr(e, "body", None)
            
            # Try to extract the actual error message from the body
            if error_body:
                try:
                    if isinstance(error_body, dict):
                        error_detail = error_body.get("error", {})
                        if isinstance(error_detail, dict):
                            extracted_msg = error_detail.get("message", None)
                            if extracted_msg:
                                error_msg = extracted_msg
                    elif isinstance(error_body, str):
                        # Try to parse JSON string
                        try:
                            parsed = json.loads(error_body)
                            if isinstance(parsed, dict):
                                error_detail = parsed.get("error", {})
                                if isinstance(error_detail, dict):
                                    extracted_msg = error_detail.get("message", None)
                                    if extracted_msg:
                                        error_msg = extracted_msg
                        except (json.JSONDecodeError, TypeError):
                            error_msg = error_body
                except Exception:
                    pass
            
            # Also try to extract from str(e) if it contains JSON-like structure
            # Format: "Error code: 400 - {'error': {'message': '...'}}"
            if error_msg == str(e) and ("'error'" in error_msg or '"error"' in error_msg):
                import re
                try:
                    # Try to extract JSON part after "Error code: 400 - "
                    match = re.search(r'-\s*({.*})', error_msg, re.DOTALL)
                    if match:
                        json_str = match.group(1)
                        # Replace single quotes with double quotes for JSON parsing
                        json_str = json_str.replace("'", '"')
                        parsed = json.loads(json_str)
                        if isinstance(parsed, dict):
                            error_detail = parsed.get("error", {})
                            if isinstance(error_detail, dict):
                                extracted_msg = error_detail.get("message", None)
                                if extracted_msg:
                                    error_msg = extracted_msg
                except (json.JSONDecodeError, TypeError, AttributeError):
                    pass
            
            # Fallback to message attribute if body extraction didn't work
            if error_message_attr and error_msg == str(e):
                error_msg = str(error_message_attr)
            retry_needed = False
            
            if "max_tokens" in error_msg and "max_completion_tokens" in error_msg.lower():
                logger.warning(
                    "Model requires 'max_completion_tokens' instead of 'max_tokens', retrying",
                    model=model,
                    error=error_msg,
                )
                # Remove max_tokens and use max_completion_tokens instead
                request_kwargs.pop("max_tokens", None)
                request_kwargs["max_completion_tokens"] = max_tokens
                retry_needed = True
            
            # Check for temperature errors - handle various error message formats
            error_lower = error_msg.lower()
            is_temperature_error = (
                "temperature" in error_lower and 
                ("unsupported" in error_lower or "does not support" in error_lower or "only the default" in error_lower)
            )
            if is_temperature_error:
                logger.warning(
                    "Model does not support custom temperature, removing temperature parameter and retrying",
                    model=model,
                    error=error_msg,
                )
                # Remove temperature parameter - model will use default
                request_kwargs.pop("temperature", None)
                retry_needed = True
            
            if retry_needed:
                try:
                    response = await self.client.chat.completions.create(**request_kwargs)
                except openai.BadRequestError as retry_error:
                    # Handle nested BadRequestError - might be another parameter issue
                    # Extract error message from retry error
                    retry_error_msg = str(retry_error)
                    retry_error_body = getattr(retry_error, "body", None)
                    if retry_error_body:
                        try:
                            if isinstance(retry_error_body, dict):
                                error_detail = retry_error_body.get("error", {})
                                if isinstance(error_detail, dict):
                                    extracted_msg = error_detail.get("message", None)
                                    if extracted_msg:
                                        retry_error_msg = extracted_msg
                        except Exception:
                            pass
                    
                    # Check if this is a temperature error we can fix
                    retry_error_lower = retry_error_msg.lower()
                    is_retry_temperature_error = (
                        "temperature" in retry_error_lower and 
                        ("unsupported" in retry_error_lower or "does not support" in retry_error_lower or "only the default" in retry_error_lower)
                    )
                    
                    if is_retry_temperature_error:
                        logger.warning(
                            "Nested temperature error detected in retry, removing temperature and retrying again",
                            model=model,
                            error=retry_error_msg,
                        )
                        request_kwargs.pop("temperature", None)
                        # Retry once more without temperature
                        response = await self.client.chat.completions.create(**request_kwargs)
                    else:
                        # If it's a different error, raise the retry error
                        raise retry_error
                except Exception as retry_error:
                    # If retry fails with a different error, raise it
                    raise retry_error
            else:
                raise
        except openai.APIConnectionError as e:
            logger.error(
                "💀 OpenAI API connection error (network/DNS issue)",
                error=str(e),
                model=model,
                error_type=type(e).__name__,
            )
            raise LLMError(
                provider="openai",
                message=f"OpenAI API connection error: {e}. Check network connectivity and DNS configuration.",
                details={"error_type": type(e).__name__, "is_network_error": True},
            )
        except openai.APITimeoutError as e:
            logger.error(
                "💀 OpenAI API timeout error",
                error=str(e),
                model=model,
                error_type=type(e).__name__,
            )
            raise LLMError(
                provider="openai",
                message=f"OpenAI API timeout: {e}. Check network connectivity.",
                details={"error_type": type(e).__name__, "is_network_error": True},
            )
        except openai.APIError as e:
            logger.error(
                "💀 OpenAI API error",
                error=str(e),
                model=model,
            )
            raise LLMError(
                provider="openai",
                message=f"💀 OpenAI API error: {e}",
                details={"error_type": type(e).__name__},
            )

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
        # Reasoning models (o1, o3, o4, gpt-5-mini) require max_completion_tokens and may have temperature restrictions
        is_reasoning_model = (
            model.startswith("o1") or
            model.startswith("o3") or
            model.startswith("o4") or
            model.startswith("gpt-5-mini")
        )

        if is_reasoning_model:
            # request_kwargs["max_completion_tokens"] = max_tokens
            # Only o3 supports temperature, o1, o4, and gpt-5-mini have fixed temperature
            if model.startswith("o3"):
                request_kwargs["temperature"] = temperature

            # Enable reasoning output for reasoning models (only if SDK supports it)
            if _openai_sdk_supports_reasoning():
                reasoning_effort = kwargs.get("reasoning_effort", "medium")
                request_kwargs["reasoning_effort"] = reasoning_effort
        else:
            # request_kwargs["max_tokens"] = max_tokens
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
                logger.warning("OpenAI SDK rejected 'reasoning_effort' kwarg for stream, retrying without it", error=msg)
                request_kwargs.pop("reasoning", None)
                stream = await self.client.chat.completions.create(**request_kwargs)
            else:
                raise
        except openai.BadRequestError as e:
            # Handle models that require max_completion_tokens instead of max_tokens
            # or models that don't support custom temperature
            # Extract error message from exception - try multiple sources
            error_msg = str(e)
            error_message_attr = getattr(e, "message", None)
            error_body = getattr(e, "body", None)
            
            # Try to extract the actual error message from the body
            if error_body:
                try:
                    if isinstance(error_body, dict):
                        error_detail = error_body.get("error", {})
                        if isinstance(error_detail, dict):
                            extracted_msg = error_detail.get("message", None)
                            if extracted_msg:
                                error_msg = extracted_msg
                    elif isinstance(error_body, str):
                        # Try to parse JSON string
                        try:
                            parsed = json.loads(error_body)
                            if isinstance(parsed, dict):
                                error_detail = parsed.get("error", {})
                                if isinstance(error_detail, dict):
                                    extracted_msg = error_detail.get("message", None)
                                    if extracted_msg:
                                        error_msg = extracted_msg
                        except (json.JSONDecodeError, TypeError):
                            error_msg = error_body
                except Exception:
                    pass
            
            # Also try to extract from str(e) if it contains JSON-like structure
            # Format: "Error code: 400 - {'error': {'message': '...'}}"
            if error_msg == str(e) and ("'error'" in error_msg or '"error"' in error_msg):
                import re
                try:
                    # Try to extract JSON part after "Error code: 400 - "
                    match = re.search(r'-\s*({.*})', error_msg, re.DOTALL)
                    if match:
                        json_str = match.group(1)
                        # Replace single quotes with double quotes for JSON parsing
                        json_str = json_str.replace("'", '"')
                        parsed = json.loads(json_str)
                        if isinstance(parsed, dict):
                            error_detail = parsed.get("error", {})
                            if isinstance(error_detail, dict):
                                extracted_msg = error_detail.get("message", None)
                                if extracted_msg:
                                    error_msg = extracted_msg
                except (json.JSONDecodeError, TypeError, AttributeError):
                    pass
            
            # Fallback to message attribute if body extraction didn't work
            if error_message_attr and error_msg == str(e):
                error_msg = str(error_message_attr)
            retry_needed = False
            
            if "max_tokens" in error_msg and "max_completion_tokens" in error_msg.lower():
                logger.warning(
                    "Model requires 'max_completion_tokens' instead of 'max_tokens' for stream, retrying",
                    model=model,
                    error=error_msg,
                )
                # Remove max_tokens and use max_completion_tokens instead
                request_kwargs.pop("max_tokens", None)
                request_kwargs["max_completion_tokens"] = max_tokens
                retry_needed = True
            
            # Check for temperature errors - handle various error message formats
            error_lower = error_msg.lower()
            is_temperature_error = (
                "temperature" in error_lower and 
                ("unsupported" in error_lower or "does not support" in error_lower or "only the default" in error_lower)
            )
            if is_temperature_error:
                logger.warning(
                    "Model does not support custom temperature for stream, removing temperature parameter and retrying",
                    model=model,
                    error=error_msg,
                )
                # Remove temperature parameter - model will use default
                request_kwargs.pop("temperature", None)
                retry_needed = True
            
            if retry_needed:
                try:
                    stream = await self.client.chat.completions.create(**request_kwargs)
                except openai.BadRequestError as retry_error:
                    # Handle nested BadRequestError - might be another parameter issue
                    # Extract error message from retry error
                    retry_error_msg = str(retry_error)
                    retry_error_body = getattr(retry_error, "body", None)
                    if retry_error_body:
                        try:
                            if isinstance(retry_error_body, dict):
                                error_detail = retry_error_body.get("error", {})
                                if isinstance(error_detail, dict):
                                    extracted_msg = error_detail.get("message", None)
                                    if extracted_msg:
                                        retry_error_msg = extracted_msg
                        except Exception:
                            pass
                    
                    # Check if this is a temperature error we can fix
                    retry_error_lower = retry_error_msg.lower()
                    is_retry_temperature_error = (
                        "temperature" in retry_error_lower and 
                        ("unsupported" in retry_error_lower or "does not support" in retry_error_lower or "only the default" in retry_error_lower)
                    )
                    
                    if is_retry_temperature_error:
                        logger.warning(
                            "Nested temperature error detected in retry, removing temperature and retrying again",
                            model=model,
                            error=retry_error_msg,
                        )
                        request_kwargs.pop("temperature", None)
                        # Retry once more without temperature
                        stream = await self.client.chat.completions.create(**request_kwargs)
                    else:
                        # If it's a different error, raise the retry error
                        raise retry_error
                except Exception as retry_error:
                    # If retry fails with a different error, raise it
                    raise retry_error
            else:
                raise

        try:
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

                # Handle reasoning content (for reasoning models like o1, o3, gpt-5-mini)
                reasoning_content = None
                try:
                    # Log all delta attributes for debugging
                    delta_attrs = {k: v for k, v in vars(delta).items() if not k.startswith('_') and v is not None}
                    if delta_attrs and not delta.content:
                        logger.info(f"Delta non-null attrs (no content): {delta_attrs}")

                    # Try direct attributes first
                    reasoning_content = getattr(delta, "reasoning_content", None) or getattr(delta, "reasoning", None)

                    # Fallback to model_extra if SDK hasn't mapped the fields yet
                    if reasoning_content is None and hasattr(delta, "model_extra") and delta.model_extra:
                        extra = delta.model_extra
                        if extra:
                            logger.info(f"Delta model_extra: {extra}")
                            reasoning_content = extra.get("reasoning_content") or extra.get("reasoning")
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

        except openai.APIConnectionError as e:
            logger.error(
                "💀 OpenAI API connection error during streaming (network/DNS issue)",
                error=str(e),
                model=model,
                error_type=type(e).__name__,
            )
            raise LLMError(
                provider="openai",
                message=f"OpenAI API connection error: {e}. Check network connectivity and DNS configuration.",
                details={"error_type": type(e).__name__, "is_network_error": True},
            )
        except openai.APITimeoutError as e:
            logger.error(
                "💀 OpenAI API timeout error during streaming",
                error=str(e),
                model=model,
                error_type=type(e).__name__,
            )
            raise LLMError(
                provider="openai",
                message=f"OpenAI API timeout: {e}. Check network connectivity.",
                details={"error_type": type(e).__name__, "is_network_error": True},
            )
        except Exception as e:
            # Also log exception via structured logger so it appears in container logs
            logger.exception("Exception while calling OpenAI stream create", error=str(e), model=model)
            if isinstance(e, openai.APIError):
                logger.error(
                    "💀 OpenAI streaming error",
                    error=str(e),
                    model=model,
                )
                raise LLMError(
                    provider="openai",
                    message=f"OpenAI streaming error: {e}",
                    details={"error_type": type(e).__name__},
                )
            raise

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

        Uses OpenAI's Responses API with strict JSON schema validation.
        Includes retry logic with exponential backoff for transient failures.
        SDK-level retries are disabled to prevent cascading with our custom
        retry loop.

        Args:
            system: System prompt
            user_message: User message/prompt
            json_schema: JSON schema to validate output against
            schema_name: Name for the schema (default: "StructuredOutput")
            model: Model to use (default: gpt-4o-mini)
            max_retries: Maximum number of retry attempts (default: 3)
            base_delay: Base delay in seconds for exponential backoff (default: 2.0)
            timeout: Per-request timeout in seconds (default: self.timeout * 2.5)
            max_output_tokens: Maximum output tokens (default: None = model default)

        Returns:
            Parsed JSON object matching the schema

        Raises:
            LLMError: If generation or parsing fails after all retries
        """
        import asyncio
        import random

        model = model or "gpt-4o-mini"
        request_timeout = timeout if timeout is not None else self.timeout * 2.5
        last_error: Exception | None = None

        # Disable SDK-level retries — our custom retry loop handles retries
        # with exponential backoff. Keeping SDK retries causes cascading:
        # (SDK retries × custom retries) API calls per generation attempt.
        client = self.client.with_options(max_retries=0)

        for attempt in range(max_retries):
            try:
                logger.info(
                    "OpenAI structured output request",
                    model=model,
                    schema_name=schema_name,
                    attempt=attempt + 1,
                    max_retries=max_retries,
                    request_timeout_seconds=request_timeout,
                )

                create_kwargs: dict[str, Any] = {
                    "model": model,
                    "input": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user_message},
                    ],
                    "text": {
                        "format": {
                            "type": "json_schema",
                            "name": schema_name,
                            "schema": json_schema,
                            "strict": True,
                        }
                    },
                    "timeout": request_timeout,
                }
                if max_output_tokens is not None:
                    create_kwargs["max_output_tokens"] = max_output_tokens

                response = await client.responses.create(**create_kwargs)

                # Check for truncation via response.status
                response_status = getattr(response, "status", None)
                incomplete_details = getattr(response, "incomplete_details", None)

                logger.info(
                    "OpenAI structured output received",
                    output_count=len(response.output or []),
                    status=response_status,
                    attempt=attempt + 1,
                )

                if response_status == "incomplete":
                    reason = (
                        getattr(incomplete_details, "reason", "unknown")
                        if incomplete_details
                        else "unknown"
                    )
                    logger.warning(
                        "OpenAI response incomplete (truncated), retrying",
                        reason=reason,
                        model=model,
                        attempt=attempt + 1,
                        max_retries=max_retries,
                    )
                    if attempt < max_retries - 1:
                        delay = base_delay * (2 ** attempt) + random.uniform(0, 1)
                        await asyncio.sleep(delay)
                        continue
                    else:
                        raise LLMError(
                            provider="openai",
                            message=f"Response incomplete (reason: {reason}) after {max_retries} attempts",
                            details={"reason": reason, "attempts": max_retries},
                        )

                # Extract JSON from response
                if not response.output:
                    raise LLMError(
                        provider="openai",
                        message="Empty response from OpenAI Responses API",
                        details={"model": model},
                    )

                first_block = response.output[0]
                payload = None

                # Try different ways to extract text content
                if getattr(first_block, "type", None) == "output_text":
                    payload = first_block.text
                else:
                    content = getattr(first_block, "content", None)
                    if content and len(content) > 0 and hasattr(content[0], "text"):
                        payload = content[0].text

                if payload is None:
                    raise LLMError(
                        provider="openai",
                        message="Unexpected response format from OpenAI Responses API",
                        details={"model": model, "first_block_type": type(first_block).__name__},
                    )

                return json.loads(payload)

            except json.JSONDecodeError as e:
                last_error = e
                if attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt) + random.uniform(0, 1)
                    logger.warning(
                        "Truncated/malformed structured output, retrying",
                        error=str(e),
                        model=model,
                        attempt=attempt + 1,
                        max_retries=max_retries,
                        retry_delay_seconds=round(delay, 2),
                        payload_length=len(payload) if payload else 0,
                    )
                    await asyncio.sleep(delay)
                    continue
                else:
                    logger.error(
                        "Failed to parse structured output JSON after all retries",
                        error=str(e),
                        model=model,
                        attempt=attempt + 1,
                    )
                    raise LLMError(
                        provider="openai",
                        message=f"Failed to parse structured output: {e}",
                        details={"error_type": "JSONDecodeError", "attempts": attempt + 1},
                    )
            except openai.APIError as e:
                last_error = e
                error_type = type(e).__name__

                # Check if this is a retryable error (timeout, rate limit, server error)
                is_retryable = isinstance(e, (
                    openai.APITimeoutError,
                    openai.RateLimitError,
                    openai.InternalServerError,
                    openai.APIConnectionError,
                ))

                if is_retryable and attempt < max_retries - 1:
                    # Exponential backoff with jitter
                    delay = base_delay * (2 ** attempt) + random.uniform(0, 1)
                    logger.warning(
                        "OpenAI API error, retrying",
                        error=str(e),
                        error_type=error_type,
                        model=model,
                        attempt=attempt + 1,
                        max_retries=max_retries,
                        retry_delay_seconds=round(delay, 2),
                        request_timeout_seconds=request_timeout,
                    )
                    await asyncio.sleep(delay)
                    continue
                else:
                    logger.error(
                        "OpenAI API error during structured output (no more retries)",
                        error=str(e),
                        error_type=error_type,
                        model=model,
                        attempt=attempt + 1,
                        max_retries=max_retries,
                    )
                    raise LLMError(
                        provider="openai",
                        message=f"OpenAI API error: {e}",
                        details={"error_type": error_type, "attempts": attempt + 1},
                    )

        # Should not reach here, but just in case
        raise LLMError(
            provider="openai",
            message=f"OpenAI API error after {max_retries} attempts: {last_error}",
            details={"error_type": type(last_error).__name__ if last_error else "Unknown"},
        )
