"""WebSocket authentication.

Authenticates the first message on a WebSocket connection.
Supports all 4 auth tiers: master admin, partner API key, tenant API key, JWT.
"""

import uuid
from dataclasses import dataclass

from libs.common import get_logger
from libs.common.auth import decode_access_token, extract_api_key, hash_api_key
from libs.common.config import get_settings
from libs.common.exceptions import AuthenticationError
from libs.db.models import ApiKey, PartnerApiKey, Tenant, User
from libs.db.session import get_session_context

from sqlalchemy import select

logger = get_logger(__name__)


@dataclass
class AuthContext:
    """Authenticated context for a WebSocket connection."""

    auth_tier: str  # "platform", "partner", "tenant", "user"
    tenant_id: uuid.UUID | None = None
    user_id: uuid.UUID | None = None
    partner_id: uuid.UUID | None = None


async def authenticate_ws_token(token: str) -> AuthContext:
    """Authenticate a token from the WebSocket auth message.

    Args:
        token: Bearer token string (with or without "Bearer " prefix)

    Returns:
        AuthContext with authenticated identity

    Raises:
        AuthenticationError: If authentication fails
    """
    # Strip "Bearer " prefix if present
    if token.startswith("Bearer "):
        token = token[7:]

    settings = get_settings()

    # 1. Check master admin key
    if token == settings.master_admin_key:
        return AuthContext(auth_tier="platform")

    # 2. Check partner API key (pk-agent-*)
    if token.startswith("pk-agent-"):
        return await _authenticate_partner_key(token)

    # 3. Check tenant API key (sk-agent-*)
    if token.startswith("sk-agent-"):
        return await _authenticate_tenant_key(token)

    # 4. Try JWT
    return await _authenticate_jwt(token)


async def _authenticate_partner_key(raw_key: str) -> AuthContext:
    """Authenticate a partner API key."""
    key_hash = hash_api_key(raw_key)

    async with get_session_context() as session:
        stmt = select(PartnerApiKey).where(
            PartnerApiKey.key_hash == key_hash,
            PartnerApiKey.is_active.is_(True),
        )
        api_key = (await session.execute(stmt)).scalar_one_or_none()

        if not api_key:
            raise AuthenticationError("Invalid partner API key")

        return AuthContext(
            auth_tier="partner",
            partner_id=api_key.partner_id,
        )


async def _authenticate_tenant_key(raw_key: str) -> AuthContext:
    """Authenticate a tenant API key."""
    key_hash = hash_api_key(raw_key)

    async with get_session_context() as session:
        stmt = select(ApiKey).where(
            ApiKey.key_hash == key_hash,
            ApiKey.is_active.is_(True),
        )
        api_key = (await session.execute(stmt)).scalar_one_or_none()

        if not api_key:
            raise AuthenticationError("Invalid tenant API key")

        # Get tenant to check partner_id
        tenant_stmt = select(Tenant).where(Tenant.id == api_key.tenant_id)
        tenant = (await session.execute(tenant_stmt)).scalar_one_or_none()

        return AuthContext(
            auth_tier="tenant",
            tenant_id=api_key.tenant_id,
            partner_id=tenant.partner_id if tenant else None,
        )


async def _authenticate_jwt(token: str) -> AuthContext:
    """Authenticate a JWT token."""
    try:
        payload = decode_access_token(token)
    except Exception as e:
        raise AuthenticationError(f"Invalid JWT: {e}")

    tenant_id = uuid.UUID(payload.tenant_id)
    user_id = uuid.UUID(payload.sub) if payload.sub else None

    # Look up tenant for partner_id
    async with get_session_context() as session:
        tenant_stmt = select(Tenant).where(Tenant.id == tenant_id)
        tenant = (await session.execute(tenant_stmt)).scalar_one_or_none()

        if not tenant:
            raise AuthenticationError("Tenant not found")

    return AuthContext(
        auth_tier="user",
        tenant_id=tenant_id,
        user_id=user_id,
        partner_id=tenant.partner_id if tenant else None,
    )
