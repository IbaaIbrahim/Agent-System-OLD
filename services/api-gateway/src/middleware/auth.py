"""Authentication middleware for API key and JWT validation."""

import re
from datetime import UTC, datetime
from uuid import UUID

from fastapi import Request
from sqlalchemy import select
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from libs.common import get_logger
from libs.common.auth import (
    decode_access_token,
    extract_api_key,
    hash_api_key,
)
from libs.common.config import get_settings
from libs.common.exceptions import AuthenticationError
from libs.db import get_session_context
from libs.db.models import Tenant, User

from ..services.api_key_cache import ApiKeyCache
from ..services.partner_api_key_cache import PartnerApiKeyCache

logger = get_logger(__name__)

# Paths that don't require authentication
PUBLIC_PATHS = {"/health", "/docs", "/redoc", "/openapi.json"}

# File download with one-time token in query: auth is validated in the route
FILE_DOWNLOAD_WITH_TOKEN_PATTERN = re.compile(
    r"^/api/v1/files/[a-f0-9-]{36}/download$",
    re.IGNORECASE,
)


class AuthMiddleware(BaseHTTPMiddleware):
    """Middleware for authenticating requests via API key or JWT."""

    async def dispatch(self, request: Request, call_next):
        # Skip auth for public paths
        if request.url.path in PUBLIC_PATHS:
            return await call_next(request)

        # Skip auth for OPTIONS requests (CORS preflight)
        if request.method == "OPTIONS":
            return await call_next(request)

        # Skip auth for file download when token is in query (route validates the OTT)
        if (
            request.method == "GET"
            and FILE_DOWNLOAD_WITH_TOKEN_PATTERN.match(request.url.path)
            and request.query_params.get("token")
        ):
            return await call_next(request)

        try:
            auth_header = request.headers.get("Authorization")

            if not auth_header:
                raise AuthenticationError("Missing authorization header")

            # Check for master admin key first (platform owner)
            if auth_header.startswith("Bearer "):
                token = auth_header[7:]
                settings = get_settings()

                if token == settings.master_admin_key:
                    # Platform owner authentication
                    await self._authenticate_master_admin(request)
                    return await call_next(request)

            # Determine auth type
            if auth_header.startswith("Bearer pk-agent-") or auth_header.startswith(
                "pk-agent-"
            ):
                # Partner API key
                await self._authenticate_partner_api_key(request, auth_header)
            elif auth_header.startswith("Bearer ") and not auth_header.startswith(
                "Bearer sk-agent-"
            ):
                # JWT token
                await self._authenticate_jwt(request, auth_header[7:])
            else:
                # Tenant API key
                await self._authenticate_api_key(request, auth_header)

            return await call_next(request)

        except AuthenticationError as e:
            logger.warning(
                "Authentication failed",
                path=request.url.path,
                error=e.message,
            )
            return JSONResponse(
                status_code=e.status_code,
                content=e.to_dict(),
            )

    async def _authenticate_jwt(self, request: Request, token: str) -> None:
        """Authenticate using JWT token."""
        payload = decode_access_token(token)

        user_id = UUID(payload.sub)
        tenant_id = UUID(payload.tenant_id)

        # Fetch user and tenant for rate limiting
        async with get_session_context() as session:
            user_result = await session.execute(
                select(User).where(User.id == user_id)
            )
            user = user_result.scalar_one_or_none()

            tenant_result = await session.execute(
                select(Tenant).where(Tenant.id == tenant_id)
            )
            tenant = tenant_result.scalar_one_or_none()

        # Store auth info in request state
        request.state.is_platform_owner = False
        request.state.is_partner = False
        request.state.user_id = user_id
        request.state.tenant_id = tenant_id
        request.state.partner_id = UUID(payload.partner_id) if payload.partner_id else None
        request.state.scopes = payload.scopes
        request.state.auth_method = "jwt"
        request.state.user = user  # For user-level rate limiting
        request.state.tenant = tenant  # For tenant-level rate limiting

    async def _authenticate_api_key(self, request: Request, auth_header: str) -> None:
        """Authenticate using API key with Redis caching."""
        api_key = extract_api_key(auth_header)
        key_hash = hash_api_key(api_key)

        # Try cache first, fallback to database
        row = await ApiKeyCache.get_or_fetch(key_hash)

        if not row:
            raise AuthenticationError("Invalid API key")

        api_key_record, tenant = row

        # Check expiration (handle both cache entry and database model)
        expires_at = api_key_record.key_expires_at if hasattr(api_key_record, "key_expires_at") else api_key_record.expires_at
        if expires_at:
            # Handle both datetime objects and ISO strings (from cache)
            if isinstance(expires_at, str):
                from datetime import datetime as dt
                expires_at = dt.fromisoformat(expires_at)

            if expires_at < datetime.now(UTC):
                raise AuthenticationError("API key has expired")

        # Check if key is active (handle both cache entry and database model)
        is_active = api_key_record.key_is_active if hasattr(api_key_record, "key_is_active") else api_key_record.is_active
        if not is_active:
            raise AuthenticationError("API key has been revoked")

        # Check tenant status (from cache entry)
        tenant_status = tenant.tenant_status if hasattr(tenant, "tenant_status") else getattr(tenant, "status", None)
        if tenant_status and tenant_status != "active":
            raise AuthenticationError(
                f"Tenant is {tenant_status}",
                details={"tenant_status": tenant_status},
            )

        # Store auth info in request state
        request.state.is_platform_owner = False
        request.state.is_partner = False
        request.state.user_id = None  # API keys don't have users
        request.state.tenant_id = tenant.tenant_id if hasattr(tenant, "tenant_id") else tenant.id
        request.state.tenant_slug = tenant.tenant_slug if hasattr(tenant, "tenant_slug") else tenant.slug
        request.state.partner_id = getattr(tenant, "partner_id", None)
        request.state.scopes = api_key_record.key_scopes if hasattr(api_key_record, "key_scopes") else api_key_record.scopes
        request.state.auth_method = "api_key"
        request.state.api_key_id = api_key_record.api_key_id if hasattr(api_key_record, "api_key_id") else api_key_record.id
        request.state.tenant = tenant  # Store tenant object for rate limiting

    async def _authenticate_partner_api_key(
        self, request: Request, auth_header: str
    ) -> None:
        """Authenticate using partner API key (pk-agent-* prefix)."""
        api_key = extract_api_key(auth_header)
        key_hash = hash_api_key(api_key)

        # Try cache first, fallback to database
        row = await PartnerApiKeyCache.get_or_fetch(key_hash)

        if not row:
            raise AuthenticationError("Invalid partner API key")

        partner_key_record, partner = row

        # Check expiration
        expires_at = partner_key_record.key_expires_at
        if expires_at:
            if isinstance(expires_at, str):
                from datetime import datetime as dt

                expires_at = dt.fromisoformat(expires_at)

            if expires_at < datetime.now(UTC):
                raise AuthenticationError("Partner API key has expired")

        # Check if key is active
        is_active = partner_key_record.key_is_active
        if not is_active:
            raise AuthenticationError("Partner API key has been revoked")

        # Check partner status
        partner_status = partner.partner_status
        if partner_status and partner_status != "active":
            raise AuthenticationError(
                f"Partner is {partner_status}",
                details={"partner_status": partner_status},
            )

        # Store auth info in request state
        request.state.is_platform_owner = False
        request.state.is_partner = True
        request.state.user_id = None
        request.state.tenant_id = None  # Partners are above tenants
        request.state.partner_id = partner.partner_id
        request.state.partner_slug = partner.partner_slug
        request.state.scopes = partner_key_record.key_scopes
        request.state.auth_method = "partner_api_key"
        request.state.api_key_id = partner_key_record.api_key_id

        logger.info(
            "Partner authenticated",
            partner_id=str(partner.partner_id),
            partner_slug=partner.partner_slug,
            path=request.url.path,
            method=request.method,
        )

    async def _authenticate_master_admin(self, request: Request) -> None:
        """Authenticate using master admin key (platform owner)."""
        # Store auth info in request state
        request.state.is_platform_owner = True
        request.state.is_partner = False
        request.state.user_id = None
        request.state.tenant_id = None
        request.state.partner_id = None
        request.state.scopes = ["*"]  # Full access to all operations
        request.state.auth_method = "master_admin"

        logger.info(
            "Platform owner authenticated",
            path=request.url.path,
            method=request.method,
        )
