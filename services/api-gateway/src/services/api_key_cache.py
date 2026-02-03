"""API key caching service using Redis for performance optimization."""

import json
from typing import Any
from uuid import UUID

from sqlalchemy import select

from libs.common import get_logger
from libs.db import get_session_context
from libs.db.models import ApiKey, Partner, Tenant
from libs.messaging.redis import get_redis_client

logger = get_logger(__name__)

# Cache TTL: 5 minutes (300 seconds)
CACHE_TTL_SECONDS = 300


class ApiKeyCacheEntry:
    """Cached API key, tenant, and partner data."""

    def __init__(
        self,
        api_key_id: UUID,
        tenant_id: UUID,
        tenant_name: str,
        tenant_slug: str,
        tenant_status: str,
        tenant_rate_limit_rpm: int | None,
        tenant_rate_limit_tpm: int | None,
        key_scopes: list[Any],
        key_is_active: bool,
        key_expires_at: str | None,
        partner_id: UUID | None = None,
        partner_slug: str | None = None,
        partner_status: str | None = None,
        partner_rate_limit_rpm: int | None = None,
        partner_rate_limit_tpm: int | None = None,
    ):
        self.api_key_id = api_key_id
        self.tenant_id = tenant_id
        self.tenant_name = tenant_name
        self.tenant_slug = tenant_slug
        self.tenant_status = tenant_status
        self.tenant_rate_limit_rpm = tenant_rate_limit_rpm
        self.tenant_rate_limit_tpm = tenant_rate_limit_tpm
        self.key_scopes = key_scopes
        self.key_is_active = key_is_active
        self.key_expires_at = key_expires_at
        self.partner_id = partner_id
        self.partner_slug = partner_slug
        self.partner_status = partner_status
        self.partner_rate_limit_rpm = partner_rate_limit_rpm
        self.partner_rate_limit_tpm = partner_rate_limit_tpm

    # Property aliases for compatibility with rate limit middleware
    @property
    def rate_limit_rpm(self) -> int | None:
        """Alias for tenant_rate_limit_rpm for rate limit middleware compatibility."""
        return self.tenant_rate_limit_rpm

    @property
    def rate_limit_tpm(self) -> int | None:
        """Alias for tenant_rate_limit_tpm for rate limit middleware compatibility."""
        return self.tenant_rate_limit_tpm

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary for JSON storage."""
        return {
            "api_key_id": str(self.api_key_id),
            "tenant_id": str(self.tenant_id),
            "tenant_name": self.tenant_name,
            "tenant_slug": self.tenant_slug,
            "tenant_status": self.tenant_status,
            "tenant_rate_limit_rpm": self.tenant_rate_limit_rpm,
            "tenant_rate_limit_tpm": self.tenant_rate_limit_tpm,
            "key_scopes": self.key_scopes,
            "key_is_active": self.key_is_active,
            "key_expires_at": self.key_expires_at,
            "partner_id": str(self.partner_id) if self.partner_id else None,
            "partner_slug": self.partner_slug,
            "partner_status": self.partner_status,
            "partner_rate_limit_rpm": self.partner_rate_limit_rpm,
            "partner_rate_limit_tpm": self.partner_rate_limit_tpm,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ApiKeyCacheEntry":
        """Deserialize from dictionary."""
        partner_id_raw = data.get("partner_id")
        return cls(
            api_key_id=UUID(data["api_key_id"]),
            tenant_id=UUID(data["tenant_id"]),
            tenant_name=data["tenant_name"],
            tenant_slug=data["tenant_slug"],
            tenant_status=data["tenant_status"],
            tenant_rate_limit_rpm=data.get("tenant_rate_limit_rpm"),
            tenant_rate_limit_tpm=data.get("tenant_rate_limit_tpm"),
            key_scopes=data.get("key_scopes", []),
            key_is_active=data["key_is_active"],
            key_expires_at=data.get("key_expires_at"),
            partner_id=UUID(partner_id_raw) if partner_id_raw else None,
            partner_slug=data.get("partner_slug"),
            partner_status=data.get("partner_status"),
            partner_rate_limit_rpm=data.get("partner_rate_limit_rpm"),
            partner_rate_limit_tpm=data.get("partner_rate_limit_tpm"),
        )

    @classmethod
    def from_models(
        cls,
        api_key: ApiKey,
        tenant: Tenant,
        partner: Partner | None = None,
    ) -> "ApiKeyCacheEntry":
        """Create from database models."""
        return cls(
            api_key_id=api_key.id,
            tenant_id=tenant.id,
            tenant_name=tenant.name,
            tenant_slug=tenant.slug,
            tenant_status=tenant.status.value if hasattr(tenant.status, 'value') else tenant.status,
            tenant_rate_limit_rpm=tenant.rate_limit_rpm,
            tenant_rate_limit_tpm=tenant.rate_limit_tpm,
            key_scopes=api_key.scopes or [],
            key_is_active=api_key.is_active,
            key_expires_at=api_key.expires_at.isoformat() if api_key.expires_at else None,
            partner_id=partner.id if partner else None,
            partner_slug=partner.slug if partner else None,
            partner_status=(partner.status.value if hasattr(partner.status, 'value') else partner.status) if partner else None,
            partner_rate_limit_rpm=partner.rate_limit_rpm if partner else None,
            partner_rate_limit_tpm=partner.rate_limit_tpm if partner else None,
        )


class ApiKeyCache:
    """LRU cache for API key lookups with Redis backend.

    This cache significantly reduces database load by storing
    API key and tenant information in Redis with a 5-minute TTL.

    Cache key format: api_key_cache:{key_hash}
    """

    @staticmethod
    async def get(key_hash: str) -> ApiKeyCacheEntry | None:
        """Retrieve API key and tenant from cache.

        Args:
            key_hash: SHA-256 hash of the API key

        Returns:
            Cached entry if found and valid, None otherwise
        """
        try:
            redis = await get_redis_client()
            cache_key = f"api_key_cache:{key_hash}"

            # Try to get from cache
            cached_data = await redis.client.get(cache_key)

            if cached_data:
                # Parse JSON and reconstruct entry
                data = json.loads(cached_data)
                entry = ApiKeyCacheEntry.from_dict(data)

                logger.debug(
                    "API key cache hit",
                    key_hash=key_hash[:12] + "...",
                    tenant_id=str(entry.tenant_id),
                    tenant_slug=entry.tenant_slug,
                )

                return entry

            logger.debug("API key cache miss", key_hash=key_hash[:12] + "...")
            return None

        except Exception as e:
            # Log error but don't fail - fallback to database
            logger.warning(
                "API key cache read failed, falling back to database",
                error=str(e),
            )
            return None

    @staticmethod
    async def set(
        key_hash: str,
        api_key: ApiKey,
        tenant: Tenant,
        partner: Partner | None = None,
    ) -> None:
        """Store API key, tenant, and partner in cache.

        Args:
            key_hash: SHA-256 hash of the API key
            api_key: ApiKey database model
            tenant: Tenant database model
            partner: Partner database model (if tenant belongs to a partner)
        """
        try:
            redis = await get_redis_client()
            cache_key = f"api_key_cache:{key_hash}"

            # Create cache entry
            entry = ApiKeyCacheEntry.from_models(api_key, tenant, partner)

            # Serialize to JSON
            cache_data = json.dumps(entry.to_dict())

            # Store with TTL
            await redis.client.set(cache_key, cache_data, ex=CACHE_TTL_SECONDS)

            logger.debug(
                "API key cached",
                key_hash=key_hash[:12] + "...",
                tenant_id=str(tenant.id),
                tenant_slug=tenant.slug,
                ttl_seconds=CACHE_TTL_SECONDS,
            )

        except Exception as e:
            # Log error but don't fail - caching is optional
            logger.warning(
                "API key cache write failed",
                error=str(e),
            )

    @staticmethod
    async def invalidate(key_hash: str) -> None:
        """Invalidate a cached API key.

        This should be called when:
        - API key is revoked
        - Tenant status changes
        - Rate limits are updated

        Args:
            key_hash: SHA-256 hash of the API key
        """
        try:
            redis = await get_redis_client()
            cache_key = f"api_key_cache:{key_hash}"

            await redis.client.delete(cache_key)

            logger.debug(
                "API key cache invalidated",
                key_hash=key_hash[:12] + "...",
            )

        except Exception as e:
            logger.warning(
                "API key cache invalidation failed",
                error=str(e),
            )

    @staticmethod
    async def get_or_fetch(key_hash: str) -> tuple[ApiKey, Tenant] | None:
        """Get from cache or fetch from database.

        This is the primary method to use - it handles cache hits,
        misses, and automatic cache population.

        Args:
            key_hash: SHA-256 hash of the API key

        Returns:
            Tuple of (ApiKey, Tenant) if found, None otherwise
        """
        # Try cache first
        cache_entry = await ApiKeyCache.get(key_hash)

        if cache_entry:
            # Reconstruct models from cache (limited attributes)
            # Note: We only cache the essential data, not full models
            # The middleware will validate expiration and status
            return cache_entry, cache_entry

        # Cache miss - fetch from database (left-join Partner for B2B2B)
        async with get_session_context() as session:
            result = await session.execute(
                select(ApiKey, Tenant, Partner)
                .join(Tenant, ApiKey.tenant_id == Tenant.id)
                .outerjoin(Partner, Tenant.partner_id == Partner.id)
                .where(ApiKey.key_hash == key_hash)
                .where(ApiKey.is_active == True)
            )
            row = result.first()

            if not row:
                logger.debug(
                    "API key not found in database",
                    key_hash=key_hash[:12] + "...",
                )
                return None

            api_key, tenant, partner = row

            # Populate cache for next time
            await ApiKeyCache.set(key_hash, api_key, tenant, partner)

            return api_key, tenant
