"""Authentication middleware for API key and JWT validation."""

from datetime import datetime, timezone
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
from libs.common.exceptions import AuthenticationError
from libs.db import get_session_context
from libs.db.models import ApiKey, Tenant

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
        request.state.user_id = UUID(payload.sub)
        request.state.tenant_id = UUID(payload.tenant_id)
        request.state.scopes = payload.scopes
        request.state.auth_method = "jwt"

    async def _authenticate_api_key(self, request: Request, auth_header: str) -> None:
        """Authenticate using API key."""
        api_key = extract_api_key(auth_header)
        key_hash = hash_api_key(api_key)

        async with get_session_context() as session:
            # Look up API key
            result = await session.execute(
                select(ApiKey, Tenant)
                .join(Tenant, ApiKey.tenant_id == Tenant.id)
                .where(ApiKey.key_hash == key_hash)
                .where(ApiKey.is_active == True)
            )
            row = result.first()

            if not row:
                raise AuthenticationError("Invalid API key")

            api_key_record, tenant = row

            # Check expiration
            if api_key_record.expires_at:
                if api_key_record.expires_at < datetime.now(timezone.utc):
                    raise AuthenticationError("API key has expired")

            # Check tenant status
            if tenant.status != "active":
                raise AuthenticationError(
                    f"Tenant is {tenant.status}",
                    details={"tenant_status": tenant.status},
                )

            # Update last used timestamp (fire and forget)
            api_key_record.last_used_at = datetime.now(timezone.utc)

            # Store auth info in request state
            request.state.user_id = None  # API keys don't have users
            request.state.tenant_id = tenant.id
            request.state.tenant = tenant
            request.state.scopes = api_key_record.scopes
            request.state.auth_method = "api_key"
            request.state.api_key_id = api_key_record.id
