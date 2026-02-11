"""Embeddings library for generating vector representations.

This library provides embedding generation services for vector search.
Uses OpenAI's text-embedding-3-small model (1536 dimensions).
"""

from .openai_embedder import OpenAIEmbedder, get_embedder

__all__ = ["OpenAIEmbedder", "get_embedder"]
