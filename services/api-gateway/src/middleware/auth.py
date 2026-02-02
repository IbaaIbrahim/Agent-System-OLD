"""Authentication middleware for API key and JWT validation."""

from datetime import UTC, datetime
from uuid import UUID

from fastapi import Request
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

from ..services.api_key_cache import ApiKeyCache

logger = get_logger(__name__)

# Paths that don't require authentication
PUBLIC_PATHS = {"/health", "/docs", "/redoc", "/openapi.json"}


class AuthMiddleware(BaseHTTPMiddleware):
    """Middleware for authenticating requests via API key or JWT."""

    async def dispatch(self, request: Request, call_next):
        # Skip auth for public paths
        if request.url.path in PUBLIC_PATHS:
            return await call_next(request)

        # Skip auth for OPTIONS requests (CORS preflight)
        if request.method == "OPTIONS":
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
            if auth_header.startswith("Bearer ") and not auth_header.startswith(
                "Bearer sk-agent-"
            ):
                # JWT token
                await self._authenticate_jwt(request, auth_header[7:])
            else:
                # API key
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

        # Store auth info in request state
        request.state.is_platform_owner = False
        request.state.user_id = UUID(payload.sub)
        request.state.tenant_id = UUID(payload.tenant_id)
        request.state.scopes = payload.scopes
        request.state.auth_method = "jwt"

    async def _authenticate_api_key(self, request: Request, auth_header: str) -> None:
        """Authenticate using API key with Redis caching."""
        api_key = extract_api_key(auth_header)
        key_hash = hash_api_key(api_key)

        # Try cache first, fallback to database
        row = await ApiKeyCache.get_or_fetch(key_hash)

        if not row:
            raise AuthenticationError("Invalid API key")

        api_key_record, tenant = row

        # Check expiration
        if api_key_record.expires_at:
            # Handle both datetime objects and ISO strings (from cache)
            expires_at = api_key_record.expires_at
            if isinstance(expires_at, str):
                from datetime import datetime as dt
                expires_at = dt.fromisoformat(expires_at)

            if expires_at < datetime.now(UTC):
                raise AuthenticationError("API key has expired")

        # Check if key is active (from cache entry)
        if not api_key_record.key_is_active:
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
        request.state.user_id = None  # API keys don't have users
        request.state.tenant_id = tenant.tenant_id if hasattr(tenant, "tenant_id") else tenant.id
        request.state.tenant_slug = tenant.tenant_slug if hasattr(tenant, "tenant_slug") else tenant.slug
        request.state.scopes = api_key_record.key_scopes if hasattr(api_key_record, "key_scopes") else api_key_record.scopes
        request.state.auth_method = "api_key"
        request.state.api_key_id = api_key_record.api_key_id if hasattr(api_key_record, "api_key_id") else api_key_record.id

    async def _authenticate_master_admin(self, request: Request) -> None:
        """Authenticate using master admin key (platform owner)."""
        # Store auth info in request state
        request.state.is_platform_owner = True
        request.state.user_id = None
        request.state.tenant_id = None
        request.state.scopes = ["*"]  # Full access to all operations
        request.state.auth_method = "master_admin"

        logger.info(
            "Platform owner authenticated",
            path=request.url.path,
            method=request.method,
        )
