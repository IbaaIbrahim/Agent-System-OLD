"""Milvus client wrapper with multi-tenant partition support."""

import uuid
from functools import lru_cache
from typing import Any

from pymilvus import (
    Collection,
    CollectionSchema,
    DataType,
    FieldSchema,
    connections,
    utility,
)

from libs.common import get_logger

logger = get_logger(__name__)


class MilvusClient:
    """Async wrapper for Milvus with multi-tenant partition support.

    Uses partition-per-tenant strategy for isolation and performance.
    Each tenant gets their own partition named "tenant_{uuid}".
    """

    COLLECTION_NAME = "knowledge_base"
    VECTOR_DIM = 1536

    def __init__(
        self,
        host: str = "localhost",
        port: int = 19530,
    ) -> None:
        """Initialize Milvus connection.

        Args:
            host: Milvus server host
            port: Milvus server port
        """
        self.host = host
        self.port = port
        self.alias = "default"

        # Connect to Milvus
        connections.connect(
            alias=self.alias,
            host=host,
            port=port,
        )
        logger.info("Connected to Milvus", host=host, port=port)

        # Ensure collection exists
        self._ensure_collection()

    def _ensure_collection(self) -> None:
        """Create knowledge base collection if it doesn't exist."""
        if utility.has_collection(self.COLLECTION_NAME, using=self.alias):
            logger.debug("Collection already exists", name=self.COLLECTION_NAME)
            self.collection = Collection(self.COLLECTION_NAME, using=self.alias)
            return

        # Define schema
        fields = [
            FieldSchema(name="id", dtype=DataType.VARCHAR, max_length=36, is_primary=True),
            FieldSchema(name="tenant_id", dtype=DataType.VARCHAR, max_length=36),
            FieldSchema(name="entry_id", dtype=DataType.VARCHAR, max_length=36),
            FieldSchema(name="category", dtype=DataType.VARCHAR, max_length=100),
            FieldSchema(name="tags", dtype=DataType.JSON),  # List of tag strings
            FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=self.VECTOR_DIM),
        ]
        schema = CollectionSchema(fields, description="Knowledge Base entries with embeddings")

        # Create collection
        self.collection = Collection(
            name=self.COLLECTION_NAME,
            schema=schema,
            using=self.alias,
        )

        # Create index on embedding field for ANN search
        index_params = {
            "index_type": "IVF_FLAT",
            "metric_type": "COSINE",
            "params": {"nlist": 128},
        }
        self.collection.create_index(field_name="embedding", index_params=index_params)
        logger.info("Created Milvus collection with index", name=self.COLLECTION_NAME)

    def _get_partition_name(self, tenant_id: uuid.UUID) -> str:
        """Get partition name for a tenant.

        Args:
            tenant_id: Tenant UUID

        Returns:
            Partition name string
        """
        return f"tenant_{str(tenant_id).replace('-', '_')}"

    def _ensure_partition(self, tenant_id: uuid.UUID) -> None:
        """Create partition for tenant if it doesn't exist.

        Args:
            tenant_id: Tenant UUID
        """
        partition_name = self._get_partition_name(tenant_id)
        if not self.collection.has_partition(partition_name):
            self.collection.create_partition(partition_name)
            logger.debug("Created partition", tenant_id=str(tenant_id), partition=partition_name)

    async def insert(
        self,
        tenant_id: uuid.UUID,
        entry_id: uuid.UUID,
        embedding: list[float],
        category: str | None = None,
        tags: list[str] | None = None,
    ) -> str:
        """Insert or update a knowledge base entry's vector.

        Args:
            tenant_id: Tenant UUID
            entry_id: KB entry UUID
            embedding: 1536-dimensional vector
            category: Optional category for filtering
            tags: Optional tags for filtering

        Returns:
            Vector ID (same as entry_id)
        """
        self._ensure_partition(tenant_id)
        partition_name = self._get_partition_name(tenant_id)

        vector_id = str(entry_id)
        data = [
            [vector_id],
            [str(tenant_id)],
            [str(entry_id)],
            [category or ""],
            [tags or []],
            [embedding],
        ]

        self.collection.insert(data, partition_name=partition_name)
        self.collection.flush()
        logger.debug(
            "Inserted vector",
            tenant_id=str(tenant_id),
            entry_id=str(entry_id),
            partition=partition_name,
        )
        return vector_id

    async def search(
        self,
        tenant_id: uuid.UUID,
        query_embedding: list[float],
        top_k: int = 5,
        category: str | None = None,
        tags: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Semantic search with optional metadata filtering.

        Args:
            tenant_id: Tenant UUID for partition filtering
            query_embedding: Query vector
            top_k: Number of results to return
            category: Optional category filter
            tags: Optional tag filter (matches if any tag overlaps)

        Returns:
            List of results with id, entry_id, distance, category, tags
        """
        partition_name = self._get_partition_name(tenant_id)
        if not self.collection.has_partition(partition_name):
            return []

        # Build filter expression
        expr_parts = [f'tenant_id == "{str(tenant_id)}"']
        if category:
            expr_parts.append(f'category == "{category}"')
        # Note: JSON field filtering for tags requires array_contains (Milvus 2.3+)
        # For simplicity, we'll filter tags in post-processing

        expr = " && ".join(expr_parts)

        self.collection.load()
        search_params = {"metric_type": "COSINE", "params": {"nprobe": 10}}

        results = self.collection.search(
            data=[query_embedding],
            anns_field="embedding",
            param=search_params,
            limit=top_k * 2 if tags else top_k,  # Over-fetch if tag filtering
            expr=expr,
            partition_names=[partition_name],
            output_fields=["entry_id", "category", "tags"],
        )

        hits = []
        for result in results[0]:
            hit_tags = result.entity.get("tags", [])
            # Filter by tags if provided
            if tags and not any(tag in hit_tags for tag in tags):
                continue

            hits.append({
                "id": result.id,
                "entry_id": result.entity.get("entry_id"),
                "distance": result.distance,  # COSINE similarity (0-1, higher = more similar)
                "category": result.entity.get("category"),
                "tags": hit_tags,
            })

            if len(hits) >= top_k:
                break

        logger.debug(
            "Vector search completed",
            tenant_id=str(tenant_id),
            hits=len(hits),
            category=category,
        )
        return hits

    async def delete(self, tenant_id: uuid.UUID, entry_id: uuid.UUID) -> None:
        """Delete a knowledge base entry's vector.

        Args:
            tenant_id: Tenant UUID
            entry_id: KB entry UUID
        """
        partition_name = self._get_partition_name(tenant_id)
        if not self.collection.has_partition(partition_name):
            return

        expr = f'entry_id == "{str(entry_id)}"'
        self.collection.delete(expr, partition_name=partition_name)
        self.collection.flush()
        logger.debug("Deleted vector", tenant_id=str(tenant_id), entry_id=str(entry_id))

    def close(self) -> None:
        """Disconnect from Milvus."""
        connections.disconnect(alias=self.alias)
        logger.info("Disconnected from Milvus")


@lru_cache
def get_milvus_client() -> MilvusClient:
    """Get singleton Milvus client.

    Returns:
        Cached MilvusClient instance
    """
    return MilvusClient()
