"""Admin endpoints for platform owner (tenant and API key management)."""

from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from libs.common import get_logger
from libs.common.auth import generate_api_key
from libs.common.exceptions import ValidationError
from libs.db import get_session_context
from libs.db.models import ApiKey, Tenant, TenantStatus

logger = get_logger(__name__)

router = APIRouter(prefix="/admin", tags=["Admin"])


# ============================================================================
# Dependencies
# ============================================================================


def require_platform_owner(request: Request) -> None:
    """Verify that the request is from the platform owner.

    Raises:
        HTTPException: If not authenticated as platform owner
    """
    is_platform_owner = getattr(request.state, "is_platform_owner", False)

    if not is_platform_owner:
        raise HTTPException(
            status_code=403,
            detail="Only platform owners can access admin endpoints",
        )


# ============================================================================
# Request/Response Models
# ============================================================================


class CreateTenantRequest(BaseModel):
    """Request to create a new tenant."""

    name: str = Field(..., min_length=1, max_length=255)
    slug: str = Field(..., pattern=r"^[a-z0-9-]+$", min_length=1, max_length=63)
    rate_limit_rpm: int | None = Field(None, ge=1)
    rate_limit_tpm: int | None = Field(None, ge=1)
    settings: dict[str, Any] | None = None


class TenantResponse(BaseModel):
    """Tenant information response."""

    id: str
    name: str
    slug: str
    status: str
    rate_limit_rpm: int | None
    rate_limit_tpm: int | None
    settings: dict[str, Any]
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, tenant: Tenant) -> "TenantResponse":
        """Create response from database model."""
        return cls(
            id=str(tenant.id),
            name=tenant.name,
            slug=tenant.slug,
            status=tenant.status.value,
            rate_limit_rpm=tenant.rate_limit_rpm,
            rate_limit_tpm=tenant.rate_limit_tpm,
            settings=tenant.settings or {},
            created_at=tenant.created_at,
            updated_at=tenant.updated_at,
        )


class UpdateTenantRequest(BaseModel):
    """Request to update tenant settings."""

    name: str | None = Field(None, min_length=1, max_length=255)
    status: str | None = Field(None, pattern="^(active|suspended|deleted)$")
    rate_limit_rpm: int | None = Field(None, ge=1)
    rate_limit_tpm: int | None = Field(None, ge=1)
    settings: dict[str, Any] | None = None


class CreateApiKeyRequest(BaseModel):
    """Request to create an API key for a tenant."""

    name: str = Field(..., min_length=1, max_length=255)
    scopes: list[str] | None = None
    expires_at: datetime | None = None


class ApiKeyResponse(BaseModel):
    """API key response."""

    id: str
    tenant_id: str
    name: str
    key_prefix: str
    scopes: list[Any]
    is_active: bool
    expires_at: datetime | None
    created_at: datetime
    last_used_at: datetime | None

    @classmethod
    def from_model(cls, api_key: ApiKey) -> "ApiKeyResponse":
        """Create response from database model."""
        return cls(
            id=str(api_key.id),
            tenant_id=str(api_key.tenant_id),
            name=api_key.name,
            key_prefix=api_key.key_prefix,
            scopes=api_key.scopes or [],
            is_active=api_key.is_active,
            expires_at=api_key.expires_at,
            created_at=api_key.created_at,
            last_used_at=api_key.last_used_at,
        )


class CreateApiKeyResponse(BaseModel):
    """Response when creating a new API key (includes raw key)."""

    api_key: str  # Raw key - only shown once!
    key_info: ApiKeyResponse


# ============================================================================
# Tenant Management Endpoints
# ============================================================================


@router.post("/tenants", response_model=TenantResponse)
async def create_tenant(
    body: CreateTenantRequest,
    _: None = Depends(require_platform_owner),
) -> TenantResponse:
    """Create a new tenant (platform owner only).

    This endpoint allows the platform owner to onboard new tenants.
    After creating a tenant, use POST /admin/tenants/{tenant_id}/api-keys
    to generate an API key for the tenant.
    """
    async with get_session_context() as session:
        # Check if slug already exists
        result = await session.execute(
            select(Tenant).where(Tenant.slug == body.slug)
        )
        if result.scalar_one_or_none():
            raise ValidationError(
                message="Tenant slug already exists",
                errors=[{"field": "slug", "message": f"Slug '{body.slug}' is already taken"}],
            )

        # Create tenant
        tenant = Tenant(
            name=body.name,
            slug=body.slug,
            status=TenantStatus.ACTIVE,
            rate_limit_rpm=body.rate_limit_rpm,
            rate_limit_tpm=body.rate_limit_tpm,
            settings=body.settings or {},
        )

        session.add(tenant)
        await session.commit()
        await session.refresh(tenant)

        logger.info(
            "Tenant created",
            tenant_id=str(tenant.id),
            tenant_slug=tenant.slug,
            tenant_name=tenant.name,
        )

        return TenantResponse.from_model(tenant)


@router.get("/tenants", response_model=list[TenantResponse])
async def list_tenants(
    status: str | None = None,
    skip: int = 0,
    limit: int = 100,
    _: None = Depends(require_platform_owner),
) -> list[TenantResponse]:
    """List all tenants with optional filtering (platform owner only)."""
    async with get_session_context() as session:
        query = select(Tenant)

        # Filter by status if provided
        if status:
            try:
                status_enum = TenantStatus(status)
                query = query.where(Tenant.status == status_enum)
            except ValueError:
                raise ValidationError(
                    message="Invalid status value",
                    errors=[{"field": "status", "message": "Must be one of: active, suspended, deleted"}],
                )

        query = query.order_by(Tenant.created_at.desc()).offset(skip).limit(limit)

        result = await session.execute(query)
        tenants = result.scalars().all()

        return [TenantResponse.from_model(t) for t in tenants]


@router.get("/tenants/{tenant_id}", response_model=TenantResponse)
async def get_tenant(
    tenant_id: UUID,
    _: None = Depends(require_platform_owner),
) -> TenantResponse:
    """Get tenant details by ID (platform owner only)."""
    async with get_session_context() as session:
        result = await session.execute(
            select(Tenant).where(Tenant.id == tenant_id)
        )
        tenant = result.scalar_one_or_none()

        if not tenant:
            raise HTTPException(status_code=404, detail="Tenant not found")

        return TenantResponse.from_model(tenant)


@router.put("/tenants/{tenant_id}", response_model=TenantResponse)
async def update_tenant(
    tenant_id: UUID,
    body: UpdateTenantRequest,
    _: None = Depends(require_platform_owner),
) -> TenantResponse:
    """Update tenant settings (platform owner only).

    Allows updating tenant name, status, rate limits, and custom settings.
    """
    async with get_session_context() as session:
        result = await session.execute(
            select(Tenant).where(Tenant.id == tenant_id)
        )
        tenant = result.scalar_one_or_none()

        if not tenant:
            raise HTTPException(status_code=404, detail="Tenant not found")

        # Update fields
        if body.name is not None:
            tenant.name = body.name

        if body.status is not None:
            tenant.status = TenantStatus(body.status)

        if body.rate_limit_rpm is not None:
            tenant.rate_limit_rpm = body.rate_limit_rpm

        if body.rate_limit_tpm is not None:
            tenant.rate_limit_tpm = body.rate_limit_tpm

        if body.settings is not None:
            tenant.settings = body.settings

        await session.commit()
        await session.refresh(tenant)

        logger.info(
            "Tenant updated",
            tenant_id=str(tenant.id),
            tenant_slug=tenant.slug,
        )

        return TenantResponse.from_model(tenant)


# ============================================================================
# API Key Management Endpoints
# ============================================================================


@router.post("/tenants/{tenant_id}/api-keys", response_model=CreateApiKeyResponse)
async def create_api_key_for_tenant(
    tenant_id: UUID,
    body: CreateApiKeyRequest,
    _: None = Depends(require_platform_owner),
) -> CreateApiKeyResponse:
    """Generate a new API key for a tenant (platform owner only).

    IMPORTANT: The raw API key is only returned once in this response.
    Store it securely - it cannot be retrieved again.
    """
    async with get_session_context() as session:
        # Verify tenant exists
        result = await session.execute(
            select(Tenant).where(Tenant.id == tenant_id)
        )
        tenant = result.scalar_one_or_none()

        if not tenant:
            raise HTTPException(status_code=404, detail="Tenant not found")

        # Generate API key
        raw_key, key_hash = generate_api_key()

        # Extract prefix for display (sk-agent)
        key_prefix = raw_key.split("-")[0] + "-" + raw_key.split("-")[1]

        # Create API key record
        api_key = ApiKey(
            tenant_id=tenant_id,
            name=body.name,
            key_hash=key_hash,
            key_prefix=key_prefix,
            scopes=body.scopes or ["*"],  # Full access by default
            is_active=True,
            expires_at=body.expires_at,
        )

        session.add(api_key)
        await session.commit()
        await session.refresh(api_key)

        logger.info(
            "API key created",
            api_key_id=str(api_key.id),
            tenant_id=str(tenant_id),
            tenant_slug=tenant.slug,
            key_name=body.name,
        )

        return CreateApiKeyResponse(
            api_key=raw_key,
            key_info=ApiKeyResponse.from_model(api_key),
        )


@router.get("/tenants/{tenant_id}/api-keys", response_model=list[ApiKeyResponse])
async def list_api_keys_for_tenant(
    tenant_id: UUID,
    include_inactive: bool = False,
    _: None = Depends(require_platform_owner),
) -> list[ApiKeyResponse]:
    """List all API keys for a tenant (platform owner only)."""
    async with get_session_context() as session:
        query = select(ApiKey).where(ApiKey.tenant_id == tenant_id)

        if not include_inactive:
            query = query.where(ApiKey.is_active == True)

        query = query.order_by(ApiKey.created_at.desc())

        result = await session.execute(query)
        api_keys = result.scalars().all()

        return [ApiKeyResponse.from_model(key) for key in api_keys]


@router.delete("/api-keys/{key_id}")
async def revoke_api_key(
    key_id: UUID,
    _: None = Depends(require_platform_owner),
) -> dict[str, str]:
    """Revoke an API key (platform owner only).

    This marks the key as inactive, preventing further use.
    """
    async with get_session_context() as session:
        result = await session.execute(
            select(ApiKey).where(ApiKey.id == key_id)
        )
        api_key = result.scalar_one_or_none()

        if not api_key:
            raise HTTPException(status_code=404, detail="API key not found")

        api_key.is_active = False
        await session.commit()

        logger.info(
            "API key revoked",
            api_key_id=str(api_key.id),
            tenant_id=str(api_key.tenant_id),
            key_name=api_key.name,
        )

        return {"status": "revoked", "key_id": str(key_id)}
