"""Partner management endpoints (platform owner only)."""

from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from libs.common import get_logger
from libs.common.auth import generate_partner_api_key, hash_api_key
from libs.common.exceptions import ValidationError
from libs.db import get_session_context
from libs.db.models import Partner, PartnerApiKey, PartnerStatus

logger = get_logger(__name__)

router = APIRouter(prefix="/admin", tags=["Partners"])


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
            detail="Only platform owners can manage partners",
        )


# ============================================================================
# Request/Response Models
# ============================================================================


class CreatePartnerRequest(BaseModel):
    """Request to create a new partner."""

    name: str = Field(..., min_length=1, max_length=255)
    slug: str = Field(..., pattern=r"^[a-z0-9-]+$", min_length=1, max_length=63)
    contact_email: str | None = None
    rate_limit_rpm: int | None = Field(None, ge=1)
    rate_limit_tpm: int | None = Field(None, ge=1)
    credit_balance_micros: int | None = Field(None, ge=0)
    settings: dict[str, Any] | None = None


class PartnerResponse(BaseModel):
    """Partner information response."""

    id: str
    name: str
    slug: str
    status: str
    contact_email: str | None
    rate_limit_rpm: int | None
    rate_limit_tpm: int | None
    credit_balance_micros: int | None
    settings: dict[str, Any]
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, partner: Partner) -> "PartnerResponse":
        """Create response from database model."""
        status = partner.status.value if hasattr(partner.status, "value") else partner.status
        return cls(
            id=str(partner.id),
            name=partner.name,
            slug=partner.slug,
            status=status,
            contact_email=partner.contact_email,
            rate_limit_rpm=partner.rate_limit_rpm,
            rate_limit_tpm=partner.rate_limit_tpm,
            credit_balance_micros=partner.credit_balance_micros,
            settings=partner.settings or {},
            created_at=partner.created_at,
            updated_at=partner.updated_at,
        )


class UpdatePartnerRequest(BaseModel):
    """Request to update partner settings."""

    name: str | None = Field(None, min_length=1, max_length=255)
    status: str | None = Field(None, pattern="^(active|suspended|deleted)$")
    contact_email: str | None = None
    rate_limit_rpm: int | None = Field(None, ge=1)
    rate_limit_tpm: int | None = Field(None, ge=1)
    credit_balance_micros: int | None = Field(None, ge=0)
    settings: dict[str, Any] | None = None


class CreatePartnerApiKeyRequest(BaseModel):
    """Request to create a partner API key."""

    name: str = Field(..., min_length=1, max_length=255)
    scopes: list[str] | None = None
    expires_at: datetime | None = None


class PartnerApiKeyResponse(BaseModel):
    """Partner API key response."""

    id: str
    partner_id: str
    name: str
    key_prefix: str
    scopes: list[Any]
    is_active: bool
    expires_at: datetime | None
    created_at: datetime
    last_used_at: datetime | None

    @classmethod
    def from_model(cls, api_key: PartnerApiKey) -> "PartnerApiKeyResponse":
        """Create response from database model."""
        return cls(
            id=str(api_key.id),
            partner_id=str(api_key.partner_id),
            name=api_key.name,
            key_prefix=api_key.key_prefix,
            scopes=api_key.scopes or [],
            is_active=api_key.is_active,
            expires_at=api_key.expires_at,
            created_at=api_key.created_at,
            last_used_at=api_key.last_used_at,
        )


class CreatePartnerApiKeyResponse(BaseModel):
    """Response when creating a new partner API key (includes raw key)."""

    api_key: str  # Raw key - only shown once!
    key_info: PartnerApiKeyResponse


# ============================================================================
# Partner Management Endpoints
# ============================================================================


@router.post("/partners", response_model=PartnerResponse)
async def create_partner(
    body: CreatePartnerRequest,
    _: None = Depends(require_platform_owner),
) -> PartnerResponse:
    """Create a new partner (platform owner only).

    Partners are businesses that manage their own set of tenants
    in the B2B2B model.
    """
    async with get_session_context() as session:
        # Check if slug already exists
        result = await session.execute(
            select(Partner).where(Partner.slug == body.slug)
        )
        if result.scalar_one_or_none():
            raise ValidationError(
                message="Partner slug already exists",
                errors=[{"field": "slug", "message": f"Slug '{body.slug}' is already taken"}],
            )

        partner = Partner(
            name=body.name,
            slug=body.slug,
            status=PartnerStatus.ACTIVE,
            contact_email=body.contact_email,
            rate_limit_rpm=body.rate_limit_rpm,
            rate_limit_tpm=body.rate_limit_tpm,
            credit_balance_micros=body.credit_balance_micros,
            settings=body.settings or {},
        )

        session.add(partner)
        await session.commit()
        await session.refresh(partner)

        logger.info(
            "Partner created",
            partner_id=str(partner.id),
            partner_slug=partner.slug,
            partner_name=partner.name,
        )

        return PartnerResponse.from_model(partner)


@router.get("/partners", response_model=list[PartnerResponse])
async def list_partners(
    status: str | None = None,
    skip: int = 0,
    limit: int = 100,
    _: None = Depends(require_platform_owner),
) -> list[PartnerResponse]:
    """List all partners (platform owner only)."""
    async with get_session_context() as session:
        query = select(Partner)

        if status:
            try:
                status_enum = PartnerStatus(status)
                query = query.where(Partner.status == status_enum)
            except ValueError:
                raise ValidationError(
                    message="Invalid status value",
                    errors=[{"field": "status", "message": "Must be one of: active, suspended, deleted"}],
                )

        query = query.order_by(Partner.created_at.desc()).offset(skip).limit(limit)

        result = await session.execute(query)
        partners = result.scalars().all()

        return [PartnerResponse.from_model(p) for p in partners]


@router.get("/partners/{partner_id}", response_model=PartnerResponse)
async def get_partner(
    partner_id: UUID,
    _: None = Depends(require_platform_owner),
) -> PartnerResponse:
    """Get partner details by ID (platform owner only)."""
    async with get_session_context() as session:
        result = await session.execute(
            select(Partner).where(Partner.id == partner_id)
        )
        partner = result.scalar_one_or_none()

        if not partner:
            raise HTTPException(status_code=404, detail="Partner not found")

        return PartnerResponse.from_model(partner)


@router.put("/partners/{partner_id}", response_model=PartnerResponse)
async def update_partner(
    partner_id: UUID,
    body: UpdatePartnerRequest,
    _: None = Depends(require_platform_owner),
) -> PartnerResponse:
    """Update partner settings (platform owner only)."""
    async with get_session_context() as session:
        result = await session.execute(
            select(Partner).where(Partner.id == partner_id)
        )
        partner = result.scalar_one_or_none()

        if not partner:
            raise HTTPException(status_code=404, detail="Partner not found")

        if body.name is not None:
            partner.name = body.name

        if body.status is not None:
            partner.status = PartnerStatus(body.status)

        if body.contact_email is not None:
            partner.contact_email = body.contact_email

        if body.rate_limit_rpm is not None:
            partner.rate_limit_rpm = body.rate_limit_rpm

        if body.rate_limit_tpm is not None:
            partner.rate_limit_tpm = body.rate_limit_tpm

        if body.credit_balance_micros is not None:
            partner.credit_balance_micros = body.credit_balance_micros

        if body.settings is not None:
            partner.settings = body.settings

        await session.commit()
        await session.refresh(partner)

        logger.info(
            "Partner updated",
            partner_id=str(partner.id),
            partner_slug=partner.slug,
        )

        return PartnerResponse.from_model(partner)


# ============================================================================
# Partner API Key Management
# ============================================================================


@router.post(
    "/partners/{partner_id}/api-keys",
    response_model=CreatePartnerApiKeyResponse,
)
async def create_partner_api_key(
    partner_id: UUID,
    body: CreatePartnerApiKeyRequest,
    _: None = Depends(require_platform_owner),
) -> CreatePartnerApiKeyResponse:
    """Generate a new API key for a partner (platform owner only).

    IMPORTANT: The raw API key is only returned once in this response.
    Store it securely - it cannot be retrieved again.

    Partner keys use the 'pk-agent-' prefix.
    """
    async with get_session_context() as session:
        # Verify partner exists
        result = await session.execute(
            select(Partner).where(Partner.id == partner_id)
        )
        partner = result.scalar_one_or_none()

        if not partner:
            raise HTTPException(status_code=404, detail="Partner not found")

        # Generate partner API key
        raw_key, key_hash = generate_partner_api_key()

        # Extract prefix for display
        key_prefix = "-".join(raw_key.split("-")[:2])

        api_key = PartnerApiKey(
            partner_id=partner_id,
            name=body.name,
            key_hash=key_hash,
            key_prefix=key_prefix,
            scopes=body.scopes or ["*"],
            is_active=True,
            expires_at=body.expires_at,
        )

        session.add(api_key)
        await session.commit()
        await session.refresh(api_key)

        logger.info(
            "Partner API key created",
            api_key_id=str(api_key.id),
            partner_id=str(partner_id),
            partner_slug=partner.slug,
            key_name=body.name,
        )

        return CreatePartnerApiKeyResponse(
            api_key=raw_key,
            key_info=PartnerApiKeyResponse.from_model(api_key),
        )


@router.get(
    "/partners/{partner_id}/api-keys",
    response_model=list[PartnerApiKeyResponse],
)
async def list_partner_api_keys(
    partner_id: UUID,
    include_inactive: bool = False,
    _: None = Depends(require_platform_owner),
) -> list[PartnerApiKeyResponse]:
    """List all API keys for a partner (platform owner only)."""
    async with get_session_context() as session:
        query = select(PartnerApiKey).where(
            PartnerApiKey.partner_id == partner_id
        )

        if not include_inactive:
            query = query.where(PartnerApiKey.is_active == True)

        query = query.order_by(PartnerApiKey.created_at.desc())

        result = await session.execute(query)
        api_keys = result.scalars().all()

        return [PartnerApiKeyResponse.from_model(key) for key in api_keys]


@router.delete("/partner-api-keys/{key_id}")
async def revoke_partner_api_key(
    key_id: UUID,
    _: None = Depends(require_platform_owner),
) -> dict[str, str]:
    """Revoke a partner API key (platform owner only)."""
    async with get_session_context() as session:
        result = await session.execute(
            select(PartnerApiKey).where(PartnerApiKey.id == key_id)
        )
        api_key = result.scalar_one_or_none()

        if not api_key:
            raise HTTPException(status_code=404, detail="Partner API key not found")

        api_key.is_active = False
        await session.commit()

        logger.info(
            "Partner API key revoked",
            api_key_id=str(api_key.id),
            partner_id=str(api_key.partner_id),
            key_name=api_key.name,
        )

        # Invalidate cache
        from ..services.partner_api_key_cache import PartnerApiKeyCache

        await PartnerApiKeyCache.invalidate(api_key.key_hash)

        return {"status": "revoked", "key_id": str(key_id)}
