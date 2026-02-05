"""System feature and partner feature configuration endpoints."""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from libs.common import get_logger
from ..services.feature import FeatureConfig, FeatureService, get_feature_service

logger = get_logger(__name__)

# Two routers: one for platform admin, one for partner config
admin_router = APIRouter(prefix="/admin/features", tags=["Features"])
partner_router = APIRouter(prefix="/partner/features", tags=["Partner Features"])


# ============================================================================
# Dependencies
# ============================================================================


def require_platform_owner(request: Request) -> None:
    """Verify that request is from platform owner."""
    is_platform_owner = getattr(request.state, "is_platform_owner", False)
    if not is_platform_owner:
        raise HTTPException(
            status_code=403,
            detail="Only platform owners can manage system features",
        )


def require_partner_or_owner(request: Request) -> dict:
    """Verify that request is from partner or platform owner."""
    is_platform_owner = getattr(request.state, "is_platform_owner", False)
    is_partner = getattr(request.state, "is_partner", False)
    partner_id = getattr(request.state, "partner_id", None)

    if is_platform_owner:
        return {"role": "platform_owner", "partner_id": None}

    if is_partner and partner_id:
        return {"role": "partner", "partner_id": partner_id}

    raise HTTPException(
        status_code=403,
        detail="Only partners or platform owners can access feature configuration",
    )


# ============================================================================
# Request/Response Models
# ============================================================================


class CreateSystemFeatureRequest(BaseModel):
    """Request to create a system feature."""

    slug: str = Field(..., pattern=r"^[a-z0-9_]+$", min_length=1, max_length=63)
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    default_provider: str = Field(..., min_length=1, max_length=50)
    default_model_id: str = Field(..., min_length=1, max_length=100)
    weight_multiplier: float = Field(default=1.0, ge=0.0)
    requires_approval: bool = False


class UpdateSystemFeatureRequest(BaseModel):
    """Request to update a system feature."""

    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = None
    default_provider: str | None = Field(None, min_length=1, max_length=50)
    default_model_id: str | None = Field(None, min_length=1, max_length=100)
    weight_multiplier: float | None = Field(None, ge=0.0)
    is_active: bool | None = None
    requires_approval: bool | None = None


class ConfigurePartnerFeatureRequest(BaseModel):
    """Request to configure a feature for a partner."""

    provider: str | None = Field(None, max_length=50)
    model_id: str | None = Field(None, max_length=100)
    weight_multiplier: float | None = Field(None, ge=0.0)
    is_enabled: bool | None = None


class SystemFeatureResponse(BaseModel):
    """System feature response."""

    id: str
    slug: str
    name: str
    description: str | None
    default_provider: str
    default_model_id: str
    weight_multiplier: float
    is_active: bool
    requires_approval: bool
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, feature) -> "SystemFeatureResponse":
        """Create response from database model."""
        return cls(
            id=str(feature.id),
            slug=feature.slug,
            name=feature.name,
            description=feature.description,
            default_provider=feature.default_provider,
            default_model_id=feature.default_model_id,
            weight_multiplier=float(feature.weight_multiplier),
            is_active=feature.is_active,
            requires_approval=feature.requires_approval,
            created_at=feature.created_at,
            updated_at=feature.updated_at,
        )


class FeatureConfigResponse(BaseModel):
    """Feature configuration response (with partner overrides applied)."""

    feature_id: str
    slug: str
    name: str
    provider: str
    model_id: str
    weight_multiplier: float
    is_enabled: bool
    requires_approval: bool

    @classmethod
    def from_config(cls, config: FeatureConfig) -> "FeatureConfigResponse":
        """Create response from FeatureConfig."""
        return cls(
            feature_id=str(config.feature_id),
            slug=config.slug,
            name=config.name,
            provider=config.provider,
            model_id=config.model_id,
            weight_multiplier=config.weight_multiplier,
            is_enabled=config.is_enabled,
            requires_approval=config.requires_approval,
        )


class PartnerFeatureConfigResponse(BaseModel):
    """Partner feature configuration response."""

    id: str
    partner_id: str
    feature_id: str
    provider: str | None
    model_id: str | None
    weight_multiplier: float | None
    is_enabled: bool
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, config) -> "PartnerFeatureConfigResponse":
        """Create response from database model."""
        return cls(
            id=str(config.id),
            partner_id=str(config.partner_id),
            feature_id=str(config.feature_id),
            provider=config.provider,
            model_id=config.model_id,
            weight_multiplier=float(config.weight_multiplier) if config.weight_multiplier else None,
            is_enabled=config.is_enabled,
            created_at=config.created_at,
            updated_at=config.updated_at,
        )


# ============================================================================
# Platform Admin Endpoints
# ============================================================================


@admin_router.post("", response_model=SystemFeatureResponse)
async def create_system_feature(
    body: CreateSystemFeatureRequest,
    _=Depends(require_platform_owner),
    service: FeatureService = Depends(get_feature_service),
):
    """Create a new system feature.

    System features define platform-level capabilities like translation,
    RAG, document analysis, etc., with default model routing.
    """
    feature = await service.create_system_feature(
        slug=body.slug,
        name=body.name,
        description=body.description,
        default_provider=body.default_provider,
        default_model_id=body.default_model_id,
        weight_multiplier=body.weight_multiplier,
        requires_approval=body.requires_approval,
    )

    logger.info(
        "System feature created",
        feature_id=str(feature.id),
        slug=body.slug,
    )

    return SystemFeatureResponse.from_model(feature)


@admin_router.get("", response_model=list[SystemFeatureResponse])
async def list_system_features(
    include_inactive: bool = False,
    _=Depends(require_platform_owner),
    service: FeatureService = Depends(get_feature_service),
):
    """List all system features."""
    features = await service.list_features(
        partner_id=None,
        include_inactive=include_inactive,
    )

    # Need to get the actual SystemFeature models
    from sqlalchemy import select
    from libs.db import get_session_context
    from libs.db.models import SystemFeature

    async with get_session_context() as session:
        query = select(SystemFeature).order_by(SystemFeature.slug)
        if not include_inactive:
            query = query.where(SystemFeature.is_active == True)
        result = await session.execute(query)
        db_features = list(result.scalars().all())

    return [SystemFeatureResponse.from_model(f) for f in db_features]


@admin_router.get("/{feature_id}", response_model=SystemFeatureResponse)
async def get_system_feature(
    feature_id: UUID,
    _=Depends(require_platform_owner),
):
    """Get a specific system feature."""
    from sqlalchemy import select
    from libs.db import get_session_context
    from libs.db.models import SystemFeature

    async with get_session_context() as session:
        result = await session.execute(
            select(SystemFeature).where(SystemFeature.id == feature_id)
        )
        feature = result.scalar_one_or_none()

    if not feature:
        raise HTTPException(status_code=404, detail="Feature not found")

    return SystemFeatureResponse.from_model(feature)


@admin_router.put("/{feature_id}", response_model=SystemFeatureResponse)
async def update_system_feature(
    feature_id: UUID,
    body: UpdateSystemFeatureRequest,
    _=Depends(require_platform_owner),
    service: FeatureService = Depends(get_feature_service),
):
    """Update a system feature."""
    updates = {k: v for k, v in body.model_dump().items() if v is not None}

    feature = await service.update_system_feature(feature_id, **updates)

    logger.info(
        "System feature updated",
        feature_id=str(feature_id),
        updates=list(updates.keys()),
    )

    return SystemFeatureResponse.from_model(feature)


# ============================================================================
# Partner Feature Configuration Endpoints
# ============================================================================


@partner_router.get("", response_model=list[FeatureConfigResponse])
async def list_partner_features(
    request: Request,
    auth_ctx: dict = Depends(require_partner_or_owner),
    service: FeatureService = Depends(get_feature_service),
):
    """List all features with partner's configuration applied.

    Returns the effective configuration for each feature, merging
    system defaults with partner overrides.
    """
    partner_id = auth_ctx.get("partner_id")
    if not partner_id:
        partner_id = request.query_params.get("partner_id")
        if not partner_id:
            raise HTTPException(
                status_code=400,
                detail="partner_id query parameter required for platform owners",
            )
        partner_id = UUID(partner_id)

    configs = await service.list_features(partner_id=partner_id)
    return [FeatureConfigResponse.from_config(c) for c in configs]


@partner_router.get("/{feature_slug}", response_model=FeatureConfigResponse)
async def get_partner_feature(
    feature_slug: str,
    request: Request,
    auth_ctx: dict = Depends(require_partner_or_owner),
    service: FeatureService = Depends(get_feature_service),
):
    """Get effective configuration for a specific feature."""
    partner_id = auth_ctx.get("partner_id")
    if not partner_id:
        partner_id = request.query_params.get("partner_id")
        if not partner_id:
            raise HTTPException(
                status_code=400,
                detail="partner_id query parameter required for platform owners",
            )
        partner_id = UUID(partner_id)

    config = await service.get_feature_config(partner_id, feature_slug)
    return FeatureConfigResponse.from_config(config)


@partner_router.put("/{feature_slug}", response_model=PartnerFeatureConfigResponse)
async def configure_partner_feature(
    feature_slug: str,
    body: ConfigurePartnerFeatureRequest,
    request: Request,
    auth_ctx: dict = Depends(require_partner_or_owner),
    service: FeatureService = Depends(get_feature_service),
):
    """Configure a feature for a partner.

    Partners can override the default provider, model, and weight
    for each feature. Set values to null to use system defaults.
    """
    partner_id = auth_ctx.get("partner_id")
    if not partner_id:
        partner_id = request.query_params.get("partner_id")
        if not partner_id:
            raise HTTPException(
                status_code=400,
                detail="partner_id query parameter required for platform owners",
            )
        partner_id = UUID(partner_id)

    config = await service.configure_feature(
        partner_id=partner_id,
        feature_slug=feature_slug,
        provider=body.provider,
        model_id=body.model_id,
        weight_multiplier=body.weight_multiplier,
        is_enabled=body.is_enabled,
    )

    logger.info(
        "Partner feature configured",
        partner_id=str(partner_id),
        feature_slug=feature_slug,
    )

    return PartnerFeatureConfigResponse.from_model(config)
