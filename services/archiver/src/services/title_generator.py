"""LLM-based conversation title generator."""

from libs.common import get_logger
from libs.llm.base import LLMMessage, MessageRole
from libs.llm.factory import create_provider

logger = get_logger(__name__)

SYSTEM_PROMPT = (
    "Generate a concise topic title (5-8 words) for a conversation based on "
    "the first user message and assistant response. Return ONLY the title text, "
    "no quotes, no punctuation at the end, no explanation."
)

# Cheapest models per provider
_MODEL_OVERRIDES: dict[str, str] = {
    "anthropic": "claude-haiku-4-5-20251001",
    "openai": "gpt-4o-mini",
}


class TitleGenerator:
    """Generates smart conversation titles using a cheap LLM call."""

    def __init__(self, provider_type: str = "anthropic") -> None:
        self._provider_type = provider_type
        model = _MODEL_OVERRIDES.get(provider_type)
        self._provider = create_provider(provider_type, default_model=model)  # type: ignore[arg-type]

    async def generate_title(
        self,
        user_message: str,
        assistant_response: str,
    ) -> str | None:
        """Generate a title from the first user-assistant exchange.

        Returns the generated title, or None on failure.
        """
        try:
            # Truncate assistant response to save tokens
            truncated_response = assistant_response[:500]
            if len(assistant_response) > 500:
                truncated_response += "..."

            prompt = (
                f"User message:\n{user_message}\n\n"
                f"Assistant response:\n{truncated_response}"
            )

            response = await self._provider.complete(
                messages=[LLMMessage(role=MessageRole.USER, content=prompt)],
                system=SYSTEM_PROMPT,
                max_tokens=30,
                temperature=0.3,
            )

            if not response.content:
                return None

            title = response.content.strip().strip('"').strip("'").strip(".")
            # Safety truncation
            if len(title) > 100:
                title = title[:100].rsplit(" ", 1)[0]

            return title or None

        except Exception:
            logger.warning(
                "Failed to generate conversation title",
                exc_info=True,
            )
            return None
