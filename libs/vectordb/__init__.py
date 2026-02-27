"""Vector database abstraction for Milvus.

This library provides a wrapper around Milvus for vector storage and search.
Supports multi-tenant partitioning for data isolation.
"""

from .milvus_client import MilvusClient, get_milvus_client

__all__ = ["MilvusClient", "get_milvus_client"]
