"""User management endpoints (virtual user CRUD for tenants)."""

from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from libs.common import get_logger
from libs.common.exceptions import ValidationError
from libs.db import get_session_context
from libs.db.models import User, UserRole

logger = get_logger(__name__)

router = APIRouter(prefix="/v1/users", tags=["users"])


# ============================================================================
# Dependencies
# ============================================================================


def get_tenant_id(request: Request) -> UUID:
    """Extract tenant ID from request state.

    Raises:
        HTTPException: If tenant ID is not present (authentication failed)
    """
    tenant_id = getattr(request.state, "tenant_id", None)

    if not tenant_id:
        raise HTTPException(
            status_code=401,
            detail="Tenant authentication required",
        )

    return tenant_id


# ============================================================================
# Request/Response Models
# ============================================================================


class CreateUserRequest(BaseModel):
    """Request to create a virtual user.

    Virtual users allow tenants to manage their own user identities.
    The external_id should be the tenant's own user identifier.
    """

    external_id: str = Field(..., min_length=1, max_length=255, description="Tenant's own user identifier")
    email: str = Field(..., pattern=r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")
    name: str = Field(..., min_length=1, max_length=255)
    role: str = Field(default="member", pattern="^(owner|admin|member)$")
    custom_rpm_limit: int | None = Field(None, ge=1, description="Custom RPM limit (NULL = inherit from tenant)")
    custom_tpm_limit: int | None = Field(None, ge=1, description="Custom TPM limit (NULL = inherit from tenant)")
    metadata: dict[str, Any] | None = None


class UpdateUserRequest(BaseModel):
    """Request to update user details."""

    name: str | None = Field(None, min_length=1, max_length=255)
    email: str | None = Field(None, pattern=r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")
    role: str | None = Field(None, pattern="^(owner|admin|member)$")
    custom_rpm_limit: int | None = Field(None, ge=1)
    custom_tpm_limit: int | None = Field(None, ge=1)
    is_active: bool | None = None
    metadata: dict[str, Any] | None = None


class UserResponse(BaseModel):
    """User information response."""

    id: str
    tenant_id: str
    external_id: str
    email: str
    name: str
    role: str
    is_active: bool
    custom_rpm_limit: int | None
    custom_tpm_limit: int | None
    metadata: dict[str, Any]
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, user: User) -> "UserResponse":
        """Create response from database model."""
        return cls(
            id=str(user.id),
            tenant_id=str(user.tenant_id),
            external_id=user.external_id,
            email=user.email,
            name=user.name,
            role=user.role.value,
            is_active=user.is_active,
            custom_rpm_limit=user.custom_rpm_limit,
            custom_tpm_limit=user.custom_tpm_limit,
            metadata=user.metadata_ or {},
            created_at=user.created_at,
            updated_at=user.updated_at,
        )


# ============================================================================
# User Management Endpoints
# ============================================================================


@router.post("", response_model=UserResponse)
async def create_user(
    body: CreateUserRequest,
    request: Request,
    tenant_id: UUID = Depends(get_tenant_id),
) -> UserResponse:
    """Create a virtual user within the tenant.

    Supports upsert logic - if a user with the same external_id exists in the tenant,
    returns the existing user instead of creating a duplicate.

    The external_id is unique per tenant, allowing the same user identifier
    to exist across different tenants (multi-tenant B2B2B model).

    Requires tenant API key authentication.
    """
    async with get_session_context() as session:
        # Check if user already exists in THIS tenant (upsert logic)
        result = await session.execute(
            select(User).where(
                User.tenant_id == tenant_id,
                User.external_id == body.external_id,
            )
        )
        existing_user = result.scalar_one_or_none()

        if existing_user:
            logger.info(
                "User already exists in tenant, returning existing user",
                user_id=str(existing_user.id),
                tenant_id=str(tenant_id),
                external_id=body.external_id,
            )
            return UserResponse.from_model(existing_user)

        # Create new user
        user = User(
            tenant_id=tenant_id,
            external_id=body.external_id,
            email=body.email,
            name=body.name,
            role=UserRole(body.role),
            custom_rpm_limit=body.custom_rpm_limit,
            custom_tpm_limit=body.custom_tpm_limit,
            is_active=True,
            metadata_=body.metadata or {},
        )

        session.add(user)
        await session.commit()
        await session.refresh(user)

        logger.info(
            "User created",
            user_id=str(user.id),
            tenant_id=str(tenant_id),
            external_id=user.external_id,
            email=user.email,
            role=user.role.value,
        )

        return UserResponse.from_model(user)


@router.get("", response_model=list[UserResponse])
async def list_users(
    request: Request,
    tenant_id: UUID = Depends(get_tenant_id),
    skip: int = 0,
    limit: int = 100,
    active_only: bool = True,
) -> list[UserResponse]:
    """List all users in the authenticated tenant.

    Only returns users belonging to the authenticated tenant (tenant isolation).

    Requires tenant API key authentication.
    """
    async with get_session_context() as session:
        query = select(User).where(User.tenant_id == tenant_id)

        if active_only:
            query = query.where(User.is_active == True)

        query = query.order_by(User.created_at.desc()).offset(skip).limit(limit)

        result = await session.execute(query)
        users = result.scalars().all()

        return [UserResponse.from_model(u) for u in users]


@router.get("/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: UUID,
    request: Request,
    tenant_id: UUID = Depends(get_tenant_id),
) -> UserResponse:
    """Get user details by ID.

    Requires tenant API key authentication.
    Validates that the user belongs to the authenticated tenant.
    """
    async with get_session_context() as session:
        result = await session.execute(
            select(User).where(
                User.id == user_id,
                User.tenant_id == tenant_id,  # Enforce tenant isolation
            )
        )
        user = result.scalar_one_or_none()

        if not user:
            raise HTTPException(
                status_code=404,
                detail="User not found or does not belong to your tenant",
            )

        return UserResponse.from_model(user)


@router.get("/by-external-id/{external_id}", response_model=UserResponse)
async def get_user_by_external_id(
    external_id: str,
    request: Request,
    tenant_id: UUID = Depends(get_tenant_id),
) -> UserResponse:
    """Get user details by tenant's external user ID.

    Useful for looking up users using the tenant's own identifier system.

    Requires tenant API key authentication.
    """
    async with get_session_context() as session:
        result = await session.execute(
            select(User).where(
                User.tenant_id == tenant_id,
                User.external_id == external_id,
            )
        )
        user = result.scalar_one_or_none()

        if not user:
            raise HTTPException(
                status_code=404,
                detail="User not found",
            )

        return UserResponse.from_model(user)


@router.put("/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: UUID,
    body: UpdateUserRequest,
    request: Request,
    tenant_id: UUID = Depends(get_tenant_id),
) -> UserResponse:
    """Update user settings.

    Allows updating user details including custom rate limit overrides.
    Setting custom_rpm_limit or custom_tpm_limit to null will inherit from tenant defaults.

    Requires tenant API key authentication.
    """
    async with get_session_context() as session:
        result = await session.execute(
            select(User).where(
                User.id == user_id,
                User.tenant_id == tenant_id,  # Enforce tenant isolation
            )
        )
        user = result.scalar_one_or_none()

        if not user:
            raise HTTPException(
                status_code=404,
                detail="User not found or does not belong to your tenant",
            )

        # Update fields
        if body.name is not None:
            user.name = body.name

        if body.email is not None:
            user.email = body.email

        if body.role is not None:
            user.role = UserRole(body.role)

        if body.custom_rpm_limit is not None:
            user.custom_rpm_limit = body.custom_rpm_limit

        if body.custom_tpm_limit is not None:
            user.custom_tpm_limit = body.custom_tpm_limit

        if body.is_active is not None:
            user.is_active = body.is_active

        if body.metadata is not None:
            user.metadata_ = body.metadata

        await session.commit()
        await session.refresh(user)

        logger.info(
            "User updated",
            user_id=str(user.id),
            tenant_id=str(tenant_id),
            external_id=user.external_id,
        )

        return UserResponse.from_model(user)


@router.delete("/{user_id}")
async def deactivate_user(
    user_id: UUID,
    request: Request,
    tenant_id: UUID = Depends(get_tenant_id),
) -> dict[str, str]:
    """Deactivate a user (soft delete).

    Marks the user as inactive without deleting the record.
    Inactive users cannot authenticate or create jobs.

    Requires tenant API key authentication.
    """
    async with get_session_context() as session:
        result = await session.execute(
            select(User).where(
                User.id == user_id,
                User.tenant_id == tenant_id,  # Enforce tenant isolation
            )
        )
        user = result.scalar_one_or_none()

        if not user:
            raise HTTPException(
                status_code=404,
                detail="User not found or does not belong to your tenant",
            )

        user.is_active = False
        await session.commit()

        logger.info(
            "User deactivated",
            user_id=str(user.id),
            tenant_id=str(tenant_id),
            external_id=user.external_id,
        )

        return {"status": "deactivated", "user_id": str(user_id)}
