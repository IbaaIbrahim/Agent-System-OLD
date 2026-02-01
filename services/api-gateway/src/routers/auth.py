"""Authentication endpoints for token exchange."""

from datetime import timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from libs.common import get_logger
from libs.common.auth import create_access_token
from libs.common.config import get_settings
from libs.db import get_session_context
from libs.db.models import User

logger = get_logger(__name__)

router = APIRouter(prefix="/v1/auth", tags=["auth"])


# ============================================================================
# Dependencies
# ============================================================================


def get_tenant_id(request: Request) -> UUID:
    """Extract tenant ID from request state.

    Raises:
        HTTPException: If tenant ID is not present (authentication failed)
    """
    tenant_id = getattr(request.state, "tenant_id", None)
    auth_method = getattr(request.state, "auth_method", None)

    if not tenant_id or auth_method != "api_key":
        raise HTTPException(
            status_code=401,
            detail="Tenant API key authentication required",
        )

    return tenant_id


# ============================================================================
# Request/Response Models
# ============================================================================


class TokenExchangeRequest(BaseModel):
    """Request to exchange API key for a user JWT token.

    The user_id can be either:
    - A UUID (the internal user.id)
    - An external_id (the tenant's own user identifier)

    The endpoint will attempt to resolve the user by UUID first,
    then fall back to external_id if the UUID lookup fails.
    """

    user_id: str = Field(
        ...,
        min_length=1,
        description="User ID (UUID) or external_id from tenant's system",
    )


class TokenExchangeResponse(BaseModel):
    """Response from token exchange."""

    access_token: str = Field(..., description="JWT access token")
    token_type: str = Field(default="Bearer")
    expires_in: int = Field(..., description="Token expiration in seconds")
    user_id: str = Field(..., description="Internal user UUID")
    tenant_id: str = Field(..., description="Tenant UUID")
    scopes: list[str] = Field(..., description="Granted permission scopes")


# ============================================================================
# Token Exchange Endpoint
# ============================================================================


@router.post("/token", response_model=TokenExchangeResponse)
async def exchange_token(
    body: TokenExchangeRequest,
    request: Request,
    tenant_id: UUID = Depends(get_tenant_id),
) -> TokenExchangeResponse:
    """Exchange tenant API key + user_id for a short-lived JWT token.

    This endpoint enables the token exchange flow:
    1. Frontend app authenticates with tenant's API key
    2. Provides a user_id (UUID or external_id)
    3. Receives a short-lived JWT token for that user
    4. Uses the JWT to create jobs and connect to SSE streams

    The JWT is scoped to the specific user and tenant, with a default
    expiration of 1 hour (configurable via jwt_expiration setting).

    Requires tenant API key authentication.

    Returns:
        JWT token with user and tenant context
    """
    settings = get_settings()

    async with get_session_context() as session:
        user = None

        # Try to parse as UUID first
        try:
            user_uuid = UUID(body.user_id)
            result = await session.execute(
                select(User).where(
                    User.id == user_uuid,
                    User.tenant_id == tenant_id,  # Enforce tenant isolation
                )
            )
            user = result.scalar_one_or_none()
        except ValueError:
            # Not a UUID, will try external_id
            pass

        # If UUID lookup failed or wasn't a UUID, try external_id
        if not user:
            result = await session.execute(
                select(User).where(
                    User.external_id == body.user_id,
                    User.tenant_id == tenant_id,  # Enforce tenant isolation
                )
            )
            user = result.scalar_one_or_none()

        # User not found in either case
        if not user:
            logger.warning(
                "Token exchange failed - user not found",
                tenant_id=str(tenant_id),
                user_id=body.user_id,
            )
            raise HTTPException(
                status_code=404,
                detail="User not found or does not belong to your tenant",
            )

        # Check if user is active
        if not user.is_active:
            logger.warning(
                "Token exchange failed - user is inactive",
                tenant_id=str(tenant_id),
                user_id=str(user.id),
                external_id=user.external_id,
            )
            raise HTTPException(
                status_code=403,
                detail="User account is inactive",
            )

        # Define scopes based on user role
        scopes = ["job:create", "stream:read"]
        if user.role.value in ("admin", "owner"):
            scopes.append("admin")

        # Create JWT token
        access_token = create_access_token(
            user_id=str(user.id),
            tenant_id=str(tenant_id),
            scopes=scopes,
            expires_delta=timedelta(seconds=settings.jwt_expiration),
        )

        logger.info(
            "Token exchanged successfully",
            tenant_id=str(tenant_id),
            user_id=str(user.id),
            external_id=user.external_id,
            scopes=scopes,
        )

        return TokenExchangeResponse(
            access_token=access_token,
            token_type="Bearer",
            expires_in=settings.jwt_expiration,
            user_id=str(user.id),
            tenant_id=str(tenant_id),
            scopes=scopes,
        )


@router.post("/refresh")
async def refresh_token(
    request: Request,
) -> TokenExchangeResponse:
    """Refresh an existing JWT token.

    This endpoint allows clients to refresh their JWT token before expiration.
    The existing token must still be valid (not expired).

    Requires valid JWT authentication.

    Returns:
        New JWT token with extended expiration
    """
    settings = get_settings()

    # Check if authenticated with JWT
    auth_method = getattr(request.state, "auth_method", None)
    if auth_method != "jwt":
        raise HTTPException(
            status_code=401,
            detail="JWT authentication required for token refresh",
        )

    user_id = request.state.user_id
    tenant_id = request.state.tenant_id
    scopes = getattr(request.state, "scopes", [])

    # Verify user still exists and is active
    async with get_session_context() as session:
        result = await session.execute(
            select(User).where(
                User.id == user_id,
                User.tenant_id == tenant_id,
            )
        )
        user = result.scalar_one_or_none()

        if not user or not user.is_active:
            raise HTTPException(
                status_code=403,
                detail="User account is inactive or no longer exists",
            )

    # Create new JWT token
    access_token = create_access_token(
        user_id=str(user_id),
        tenant_id=str(tenant_id),
        scopes=scopes,
        expires_delta=timedelta(seconds=settings.jwt_expiration),
    )

    logger.info(
        "Token refreshed",
        tenant_id=str(tenant_id),
        user_id=str(user_id),
    )

    return TokenExchangeResponse(
        access_token=access_token,
        token_type="Bearer",
        expires_in=settings.jwt_expiration,
        user_id=str(user_id),
        tenant_id=str(tenant_id),
        scopes=scopes,
    )
