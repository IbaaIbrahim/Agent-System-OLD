"""OpenAI embeddings service with batching support."""

from functools import lru_cache
from typing import Any

import httpx

from libs.common import get_logger
from libs.common.config import get_settings

logger = get_logger(__name__)


class OpenAIEmbedder:
    """OpenAI embeddings client with async support and batching.

    Usage:
        embedder = OpenAIEmbedder()
        embedding = await embedder.embed_text("Hello world")
        # Returns: [0.0012, -0.0034, ...]  # 1536-dimensional vector
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "text-embedding-3-small",
        dimensions: int = 1536,
        timeout: int = 30,
    ) -> None:
        """Initialize OpenAI embedder.

        Args:
            api_key: OpenAI API key (defaults to config)
            model: Embedding model ID
            dimensions: Output dimension (1536 for text-embedding-3-small)
            timeout: HTTP timeout in seconds
        """
        settings = get_settings()
        self.api_key = api_key or settings.openai_api_key
        if not self.api_key:
            raise ValueError("OpenAI API key is required for embeddings")

        self.model = model
        self.dimensions = dimensions
        self.timeout = timeout
        self.client = httpx.AsyncClient(
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )

    async def embed_text(self, text: str) -> list[float]:
        """Generate embedding for a single text.

        Args:
            text: Input text to embed

        Returns:
            1536-dimensional embedding vector

        Raises:
            httpx.HTTPStatusError: If API request fails
        """
        embeddings = await self.embed_batch([text])
        return embeddings[0]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for multiple texts in a single API call.

        Args:
            texts: List of texts to embed (max 2048 per batch)

        Returns:
            List of embedding vectors in the same order as input

        Raises:
            httpx.HTTPStatusError: If API request fails
        """
        if not texts:
            return []

        if len(texts) > 2048:
            raise ValueError("OpenAI embeddings API supports max 2048 texts per batch")

        logger.debug("Generating embeddings", count=len(texts), model=self.model)

        response = await self.client.post(
            "https://api.openai.com/v1/embeddings",
            json={
                "input": texts,
                "model": self.model,
                "dimensions": self.dimensions,
            },
        )
        response.raise_for_status()
        data = response.json()

        # OpenAI returns embeddings in order with index field
        embeddings = sorted(data["data"], key=lambda x: x["index"])
        return [item["embedding"] for item in embeddings]

    async def close(self) -> None:
        """Close HTTP client."""
        await self.client.aclose()


@lru_cache
def get_embedder() -> OpenAIEmbedder:
    """Get singleton embedder instance."""
    return OpenAIEmbedder()
