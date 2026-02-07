"""Factory for creating LLM provider instances."""

from typing import Literal

from libs.common.config import get_settings
from libs.common.exceptions import ValidationError
from libs.common.logging import get_logger
from libs.llm.anthropic import AnthropicProvider
from libs.llm.base import LLMProvider
from libs.llm.openai import OpenAIProvider

logger = get_logger(__name__)

# Global provider instances
_providers: dict[str, LLMProvider] = {}

ProviderType = Literal["anthropic", "openai"]


def create_provider(
    provider_type: ProviderType,
    api_key: str | None = None,
    default_model: str | None = None,
) -> LLMProvider:
    """Create an LLM provider instance.

    Args:
        provider_type: Provider type ('anthropic' or 'openai')
        api_key: API key (default from settings)
        default_model: Default model (default from settings)

    Returns:
        Configured LLMProvider instance

    Raises:
        ValidationError: If provider type is invalid or API key is missing
    """
    settings = get_settings()

    if provider_type == "anthropic":
        key = api_key or settings.anthropic_api_key
        if not key:
            raise ValidationError(
                message="Anthropic API key not configured",
                errors=[{"field": "api_key", "message": "Missing ANTHROPIC_API_KEY"}],
            )
        model = default_model or settings.anthropic_default_model
        return AnthropicProvider(api_key=key, default_model=model, timeout=settings.llm_timeout)

    elif provider_type == "openai":
        key = api_key or settings.openai_api_key
        if not key:
            raise ValidationError(
                message="OpenAI API key not configured",
                errors=[{"field": "api_key", "message": "Missing OPENAI_API_KEY"}],
            )
        model = default_model or settings.openai_default_model
        return OpenAIProvider(api_key=key, default_model=model, timeout=settings.llm_timeout)

    else:
        raise ValidationError(
            message=f"Unknown provider type: {provider_type}",
            errors=[
                {
                    "field": "provider_type",
                    "message": f"Must be 'anthropic' or 'openai', got '{provider_type}'",
                }
            ],
        )


def get_provider(provider_type: ProviderType | None = None) -> LLMProvider:
    """Get or create a cached LLM provider instance.

    Args:
        provider_type: Provider type (default from settings)

    Returns:
        LLMProvider instance
    """
    settings = get_settings()
    provider_type = provider_type or settings.default_llm_provider

    if provider_type not in _providers:
        _providers[provider_type] = create_provider(provider_type)
        logger.info("LLM provider created", provider=provider_type)

    return _providers[provider_type]


def clear_providers() -> None:
    """Clear cached provider instances (useful for testing)."""
    global _providers
    _providers = {}
