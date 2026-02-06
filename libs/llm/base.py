"""Base classes for LLM providers."""

import json
from abc import ABC, abstractmethod

from collections.abc import AsyncIterator
from dataclasses import dataclass
from enum import Enum
from typing import Any


class MessageRole(str, Enum):
    """Message role in conversation."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass
class ToolDefinition:
    """Definition of a tool that can be called by the LLM."""

    name: str
    description: str
    parameters: dict[str, Any]

    def to_anthropic(self) -> dict[str, Any]:
        """Convert to Anthropic tool format."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.parameters,
        }

    def to_openai(self) -> dict[str, Any]:
        """Convert to OpenAI tool format."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass
class ToolCall:
    """A tool call made by the LLM."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMMessage:
    """A message in the conversation."""

    role: MessageRole
    content: str | None = None
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None
    name: str | None = None

    def to_anthropic(self) -> dict[str, Any]:
        """Convert to Anthropic message format."""
        if self.role == MessageRole.TOOL:
            return {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": self.tool_call_id,
                        "content": self.content,
                    }
                ],
            }

        if self.role == MessageRole.ASSISTANT and self.tool_calls:
            content = []
            if self.content:
                content.append({"type": "text", "text": self.content})
            for tc in self.tool_calls:
                content.append({
                    "type": "tool_use",
                    "id": tc.id,
                    "name": tc.name,
                    "input": tc.arguments,
                })
            return {"role": "assistant", "content": content}

        return {
            "role": self.role.value,
            "content": self.content or "",
        }

    def to_openai(self) -> dict[str, Any]:
        """Convert to OpenAI message format."""
        msg: dict[str, Any] = {"role": self.role.value}

        if self.content is not None:
            msg["content"] = self.content

        # tool_calls only allowed/valid for assistant role
        if self.role == MessageRole.ASSISTANT and self.tool_calls:
            msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": json.dumps(tc.arguments),
                    },
                }
                for tc in self.tool_calls
            ]

        # tool_call_id only allowed/required for tool role
        if self.role == MessageRole.TOOL and self.tool_call_id:
            msg["tool_call_id"] = self.tool_call_id

        if self.name:
            msg["name"] = self.name

        return msg


@dataclass
class LLMResponse:
    """Response from an LLM provider."""

    content: str | None = None
    reasoning_content: str | None = None
    tool_calls: list[ToolCall] | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    finish_reason: str | None = None
    model: str | None = None
    raw_response: Any = None


@dataclass
class LLMStreamChunk:
    """A chunk from a streaming LLM response."""

    content: str | None = None
    reasoning_content: str | None = None
    tool_calls: list[ToolCall] | None = None
    is_final: bool = False
    finish_reason: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None


class LLMProvider(ABC):
    """Abstract base class for LLM providers."""

    provider_name: str = "base"

    def __init__(self, api_key: str, default_model: str | None = None) -> None:
        self.api_key = api_key
        self.default_model = default_model

    @abstractmethod
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
        """Generate a completion for the given messages.

        Args:
            messages: Conversation messages
            model: Model to use (default from config)
            system: System prompt
            tools: Available tools
            temperature: Sampling temperature
            max_tokens: Maximum tokens to generate
            **kwargs: Provider-specific options

        Returns:
            LLMResponse with generated content
        """
        pass

    @abstractmethod
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
        """Stream a completion for the given messages.

        Args:
            messages: Conversation messages
            model: Model to use
            system: System prompt
            tools: Available tools
            temperature: Sampling temperature
            max_tokens: Maximum tokens to generate
            **kwargs: Provider-specific options

        Yields:
            LLMStreamChunk objects as they arrive
        """
        pass

    def get_model(self, model: str | None = None) -> str:
        """Get the model to use, with fallback to default."""
        if model:
            return model
        if self.default_model:
            return self.default_model
        raise ValueError("No model specified and no default model configured")
