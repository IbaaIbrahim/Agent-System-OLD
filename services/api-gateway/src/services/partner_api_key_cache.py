"""Partner API key caching service using Redis for performance optimization."""

import json
from typing import Any
from uuid import UUID

from sqlalchemy import select

from libs.common import get_logger
from libs.db import get_session_context
from libs.db.models import Partner, PartnerApiKey
from libs.messaging.redis import get_redis_client

logger = get_logger(__name__)

# Cache TTL: 5 minutes (300 seconds)
CACHE_TTL_SECONDS = 300


class PartnerApiKeyCacheEntry:
    """Cached partner API key and partner data."""

    def __init__(
        self,
        api_key_id: UUID,
        partner_id: UUID,
        partner_name: str,
        partner_slug: str,
        partner_status: str,
        partner_rate_limit_rpm: int | None,
        partner_rate_limit_tpm: int | None,
        partner_credit_balance_micros: int | None,
        key_scopes: list[Any],
        key_is_active: bool,
        key_expires_at: str | None,
    ):
        self.api_key_id = api_key_id
        self.partner_id = partner_id
        self.partner_name = partner_name
        self.partner_slug = partner_slug
        self.partner_status = partner_status
        self.partner_rate_limit_rpm = partner_rate_limit_rpm
        self.partner_rate_limit_tpm = partner_rate_limit_tpm
        self.partner_credit_balance_micros = partner_credit_balance_micros
        self.key_scopes = key_scopes
        self.key_is_active = key_is_active
        self.key_expires_at = key_expires_at

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary for JSON storage."""
        return {
            "api_key_id": str(self.api_key_id),
            "partner_id": str(self.partner_id),
            "partner_name": self.partner_name,
            "partner_slug": self.partner_slug,
            "partner_status": self.partner_status,
            "partner_rate_limit_rpm": self.partner_rate_limit_rpm,
            "partner_rate_limit_tpm": self.partner_rate_limit_tpm,
            "partner_credit_balance_micros": self.partner_credit_balance_micros,
            "key_scopes": self.key_scopes,
            "key_is_active": self.key_is_active,
            "key_expires_at": self.key_expires_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PartnerApiKeyCacheEntry":
        """Deserialize from dictionary."""
        return cls(
            api_key_id=UUID(data["api_key_id"]),
            partner_id=UUID(data["partner_id"]),
            partner_name=data["partner_name"],
            partner_slug=data["partner_slug"],
            partner_status=data["partner_status"],
            partner_rate_limit_rpm=data.get("partner_rate_limit_rpm"),
            partner_rate_limit_tpm=data.get("partner_rate_limit_tpm"),
            partner_credit_balance_micros=data.get("partner_credit_balance_micros"),
            key_scopes=data.get("key_scopes", []),
            key_is_active=data["key_is_active"],
            key_expires_at=data.get("key_expires_at"),
        )

    @classmethod
    def from_models(
        cls, api_key: PartnerApiKey, partner: Partner
    ) -> "PartnerApiKeyCacheEntry":
        """Create from database models."""
        return cls(
            api_key_id=api_key.id,
            partner_id=partner.id,
            partner_name=partner.name,
            partner_slug=partner.slug,
            partner_status=partner.status.value if hasattr(partner.status, "value") else partner.status,
            partner_rate_limit_rpm=partner.rate_limit_rpm,
            partner_rate_limit_tpm=partner.rate_limit_tpm,
            partner_credit_balance_micros=partner.credit_balance_micros,
            key_scopes=api_key.scopes or [],
            key_is_active=api_key.is_active,
            key_expires_at=api_key.expires_at.isoformat() if api_key.expires_at else None,
        )


class PartnerApiKeyCache:
    """Cache for partner API key lookups with Redis backend.

    Cache key format: partner_api_key_cache:{key_hash}
    """

    @staticmethod
    async def get(key_hash: str) -> PartnerApiKeyCacheEntry | None:
        """Retrieve partner API key and partner from cache."""
        try:
            redis = await get_redis_client()
            cache_key = f"partner_api_key_cache:{key_hash}"

            cached_data = await redis.client.get(cache_key)

            if cached_data:
                data = json.loads(cached_data)
                entry = PartnerApiKeyCacheEntry.from_dict(data)

                logger.debug(
                    "Partner API key cache hit",
                    key_hash=key_hash[:12] + "...",
                    partner_id=str(entry.partner_id),
                    partner_slug=entry.partner_slug,
                )

                return entry

            logger.debug("Partner API key cache miss", key_hash=key_hash[:12] + "...")
            return None

        except Exception as e:
            logger.warning(
                "Partner API key cache read failed, falling back to database",
                error=str(e),
            )
            return None

    @staticmethod
    async def set(
        key_hash: str, api_key: PartnerApiKey, partner: Partner
    ) -> None:
        """Store partner API key and partner in cache."""
        try:
            redis = await get_redis_client()
            cache_key = f"partner_api_key_cache:{key_hash}"

            entry = PartnerApiKeyCacheEntry.from_models(api_key, partner)
            cache_data = json.dumps(entry.to_dict())

            await redis.client.set(cache_key, cache_data, ex=CACHE_TTL_SECONDS)

            logger.debug(
                "Partner API key cached",
                key_hash=key_hash[:12] + "...",
                partner_id=str(partner.id),
                partner_slug=partner.slug,
                ttl_seconds=CACHE_TTL_SECONDS,
            )

        except Exception as e:
            logger.warning(
                "Partner API key cache write failed",
                error=str(e),
            )

    @staticmethod
    async def invalidate(key_hash: str) -> None:
        """Invalidate a cached partner API key."""
        try:
            redis = await get_redis_client()
            cache_key = f"partner_api_key_cache:{key_hash}"
            await redis.client.delete(cache_key)

            logger.debug(
                "Partner API key cache invalidated",
                key_hash=key_hash[:12] + "...",
            )

        except Exception as e:
            logger.warning(
                "Partner API key cache invalidation failed",
                error=str(e),
            )

    @staticmethod
    async def get_or_fetch(
        key_hash: str,
    ) -> tuple[PartnerApiKeyCacheEntry, PartnerApiKeyCacheEntry] | None:
        """Get from cache or fetch from database.

        Returns:
            Tuple of (key_record, partner_record) as cache entries,
            or None if not found.
        """
        # Try cache first
        cache_entry = await PartnerApiKeyCache.get(key_hash)

        if cache_entry:
            return cache_entry, cache_entry

        # Cache miss - fetch from database
        async with get_session_context() as session:
            result = await session.execute(
                select(PartnerApiKey, Partner)
                .join(Partner, PartnerApiKey.partner_id == Partner.id)
                .where(PartnerApiKey.key_hash == key_hash)
                .where(PartnerApiKey.is_active == True)
            )
            row = result.first()

            if not row:
                logger.debug(
                    "Partner API key not found in database",
                    key_hash=key_hash[:12] + "...",
                )
                return None

            api_key, partner = row

            # Populate cache for next time
            await PartnerApiKeyCache.set(key_hash, api_key, partner)

            entry = PartnerApiKeyCacheEntry.from_models(api_key, partner)
            return entry, entry
