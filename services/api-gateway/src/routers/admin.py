"""Admin endpoints for tenant and API key management.

Supports both platform owner (super admin) and partner-scoped access.
"""

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
from libs.db.models import ApiKey, Partner, Tenant, TenantStatus

logger = get_logger(__name__)

router = APIRouter(prefix="/admin", tags=["Tenant"])


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
            detail="Only platform owners can access this endpoint",
        )


def require_partner_or_owner(request: Request) -> dict[str, Any]:
    """Verify request is from platform owner or authenticated partner.

    Returns:
        Context dict with role and partner_id.
    """
    if getattr(request.state, "is_platform_owner", False):
        return {"role": "platform_owner", "partner_id": None}

    if getattr(request.state, "is_partner", False):
        return {
            "role": "partner",
            "partner_id": request.state.partner_id,
        }

    raise HTTPException(
        status_code=403,
        detail="Partner or platform owner access required",
    )


# ============================================================================
# Request/Response Models
# ============================================================================


class CreateTenantRequest(BaseModel):
    """Request to create a new tenant."""

    name: str = Field(..., min_length=1, max_length=255)
    slug: str = Field(..., pattern=r"^[a-z0-9-]+$", min_length=1, max_length=63)
    partner_id: UUID | None = Field(
        None,
        description="Partner ID (only platform owner can set this explicitly)",
    )
    rate_limit_rpm: int | None = Field(None, ge=1)
    rate_limit_tpm: int | None = Field(None, ge=1)
    settings: dict[str, Any] | None = None


class TenantResponse(BaseModel):
    """Tenant information response."""

    id: str
    name: str
    slug: str
    status: str
    partner_id: str | None
    rate_limit_rpm: int | None
    rate_limit_tpm: int | None
    settings: dict[str, Any]
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, tenant: Tenant) -> "TenantResponse":
        """Create response from database model."""
        status = tenant.status.value if hasattr(tenant.status, 'value') else tenant.status
        return cls(
            id=str(tenant.id),
            name=tenant.name,
            slug=tenant.slug,
            status=status,
            partner_id=str(tenant.partner_id) if tenant.partner_id else None,
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
    auth_ctx: dict[str, Any] = Depends(require_partner_or_owner),
) -> TenantResponse:
    """Create a new tenant.

    Platform owners can create tenants for any partner (or with no partner).
    Partners can only create tenants under themselves.
    """
    # Determine the partner_id for this tenant
    if auth_ctx["role"] == "partner":
        # Partner creates tenant scoped to themselves
        partner_id = auth_ctx["partner_id"]
        if body.partner_id and body.partner_id != partner_id:
            raise HTTPException(
                status_code=403,
                detail="Partners cannot assign tenants to other partners",
            )
    else:
        # Platform owner can optionally assign a partner
        partner_id = body.partner_id

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
        
        # Validate partner_id if provided
        if partner_id:
            partner_result = await session.execute(
                select(Partner).where(Partner.id == partner_id)
            )
            if not partner_result.scalar_one_or_none():
                raise ValidationError(
                    message=f"Partner with ID {partner_id} not found",
                    errors=[{"field": "partner_id", "message": "Partner does not exist"}]
                )

        # Create tenant
        tenant = Tenant(
            name=body.name,
            slug=body.slug,
            status=TenantStatus.ACTIVE,
            partner_id=partner_id,
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
            partner_id=str(partner_id) if partner_id else None,
            created_by=auth_ctx["role"],
        )

        return TenantResponse.from_model(tenant)


@router.get("/tenants", response_model=list[TenantResponse])
async def list_tenants(
    status: str | None = None,
    partner_id: UUID | None = None,
    skip: int = 0,
    limit: int = 100,
    auth_ctx: dict[str, Any] = Depends(require_partner_or_owner),
) -> list[TenantResponse]:
    """List tenants with optional filtering.

    Platform owners see all tenants (optionally filtered by partner_id).
    Partners see only their own tenants.
    """
    async with get_session_context() as session:
        query = select(Tenant)

        # Partner scoping: restrict to own tenants
        if auth_ctx["role"] == "partner":
            query = query.where(Tenant.partner_id == auth_ctx["partner_id"])
        elif partner_id:
            # Platform owner filtering by partner
            query = query.where(Tenant.partner_id == partner_id)

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
    auth_ctx: dict[str, Any] = Depends(require_partner_or_owner),
) -> TenantResponse:
    """Get tenant details by ID.

    Partners can only view their own tenants.
    """
    async with get_session_context() as session:
        result = await session.execute(
            select(Tenant).where(Tenant.id == tenant_id)
        )
        tenant = result.scalar_one_or_none()

        if not tenant:
            raise HTTPException(status_code=404, detail="Tenant not found")

        # Partner scoping check
        if auth_ctx["role"] == "partner" and tenant.partner_id != auth_ctx["partner_id"]:
            raise HTTPException(status_code=403, detail="Access denied to this tenant")

        return TenantResponse.from_model(tenant)


@router.put("/tenants/{tenant_id}", response_model=TenantResponse)
async def update_tenant(
    tenant_id: UUID,
    body: UpdateTenantRequest,
    auth_ctx: dict[str, Any] = Depends(require_partner_or_owner),
) -> TenantResponse:
    """Update tenant settings.

    Partners can only update their own tenants.
    """
    async with get_session_context() as session:
        result = await session.execute(
            select(Tenant).where(Tenant.id == tenant_id)
        )
        tenant = result.scalar_one_or_none()

        if not tenant:
            raise HTTPException(status_code=404, detail="Tenant not found")

        # Partner scoping check
        if auth_ctx["role"] == "partner" and tenant.partner_id != auth_ctx["partner_id"]:
            raise HTTPException(status_code=403, detail="Access denied to this tenant")

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
    auth_ctx: dict[str, Any] = Depends(require_partner_or_owner),
) -> CreateApiKeyResponse:
    """Generate a new API key for a tenant.

    Partners can only create keys for their own tenants.

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

        # Partner scoping check
        if auth_ctx["role"] == "partner" and tenant.partner_id != auth_ctx["partner_id"]:
            raise HTTPException(status_code=403, detail="Access denied to this tenant")

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
    auth_ctx: dict[str, Any] = Depends(require_partner_or_owner),
) -> list[ApiKeyResponse]:
    """List all API keys for a tenant.

    Partners can only list keys for their own tenants.
    """
    async with get_session_context() as session:
        # Partner scoping: verify tenant belongs to partner
        if auth_ctx["role"] == "partner":
            tenant_result = await session.execute(
                select(Tenant).where(Tenant.id == tenant_id)
            )
            tenant = tenant_result.scalar_one_or_none()
            if not tenant or tenant.partner_id != auth_ctx["partner_id"]:
                raise HTTPException(status_code=403, detail="Access denied to this tenant")

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
    auth_ctx: dict[str, Any] = Depends(require_partner_or_owner),
) -> dict[str, str]:
    """Revoke an API key.

    Partners can only revoke keys belonging to their own tenants.
    """
    async with get_session_context() as session:
        result = await session.execute(
            select(ApiKey).where(ApiKey.id == key_id)
        )
        api_key = result.scalar_one_or_none()

        if not api_key:
            raise HTTPException(status_code=404, detail="API key not found")

        # Partner scoping: verify the key's tenant belongs to the partner
        if auth_ctx["role"] == "partner":
            tenant_result = await session.execute(
                select(Tenant).where(Tenant.id == api_key.tenant_id)
            )
            tenant = tenant_result.scalar_one_or_none()
            if not tenant or tenant.partner_id != auth_ctx["partner_id"]:
                raise HTTPException(status_code=403, detail="Access denied to this API key")

        api_key.is_active = False
        await session.commit()

        logger.info(
            "API key revoked",
            api_key_id=str(api_key.id),
            tenant_id=str(api_key.tenant_id),
            key_name=api_key.name,
        )

        return {"status": "revoked", "key_id": str(key_id)}
