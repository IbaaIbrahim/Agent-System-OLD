"""LLM provider abstraction and implementations."""

from libs.llm.anthropic import AnthropicProvider
from libs.llm.base import (
    LLMMessage,
    LLMProvider,
    LLMResponse,
    LLMStreamChunk,
    MessageRole,
    ToolCall,
    ToolDefinition,
)
from libs.llm.factory import create_provider, get_provider
from libs.llm.openai import OpenAIProvider

# Vision-capable models by provider
VISION_MODELS = {
    "anthropic": [
        "claude-haiku-4",
        "claude-sonnet-4",
        "claude-opus-4",
        "claude-haiku-4-5",
        "claude-sonnet-4-5",
        "claude-opus-4-5",
    ],
    "openai": [
        "gpt-4-vision-preview",
        "gpt-4-turbo",
        "gpt-4-turbo-2024-04-09",
        "gpt-4o",
        "gpt-4o-mini",
        "o4-mini"
    ],
}


def supports_vision(provider: str, model: str) -> bool:
    """Check if a provider/model combination supports vision (image inputs).

    Args:
        provider: Provider name ("anthropic" or "openai")
        model: Model name

    Returns:
        True if the model supports vision/multimodal inputs
    """
    provider_models = VISION_MODELS.get(provider, [])
    return model in provider_models


__all__ = [
    # Base classes
    "LLMProvider",
    "LLMMessage",
    "LLMResponse",
    "LLMStreamChunk",
    "ToolDefinition",
    "ToolCall",
    "MessageRole",
    # Providers
    "AnthropicProvider",
    "OpenAIProvider",
    # Factory
    "create_provider",
    "get_provider",
    # Vision utilities
    "VISION_MODELS",
    "supports_vision",
]
