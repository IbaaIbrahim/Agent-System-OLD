"""Partner plan management endpoints."""

from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from libs.common import get_logger
from ..services.subscription import SubscriptionService, get_subscription_service

logger = get_logger(__name__)

router = APIRouter(prefix="/partner/plans", tags=["Plans"])


# ============================================================================
# Dependencies
# ============================================================================


def require_partner_or_owner(request: Request) -> dict:
    """Verify that request is from partner or platform owner.

    Returns:
        Dict with role and partner_id
    """
    is_platform_owner = getattr(request.state, "is_platform_owner", False)
    is_partner = getattr(request.state, "is_partner", False)
    partner_id = getattr(request.state, "partner_id", None)

    if is_platform_owner:
        return {"role": "platform_owner", "partner_id": None}

    if is_partner and partner_id:
        return {"role": "partner", "partner_id": partner_id}

    raise HTTPException(
        status_code=403,
        detail="Only partners or platform owners can manage plans",
    )


# ============================================================================
# Request/Response Models
# ============================================================================


class CreatePlanRequest(BaseModel):
    """Request to create a new plan."""

    name: str = Field(..., min_length=1, max_length=255)
    slug: str = Field(..., pattern=r"^[a-z0-9-]+$", min_length=1, max_length=63)
    description: str | None = None
    monthly_credits_micros: int = Field(default=0, ge=0)
    extra_credit_price_micros: int = Field(default=1_000_000, ge=0)
    extra_credit_lifetime_days: int = Field(default=365, ge=1)
    rate_limit_rpm: int | None = Field(None, ge=1)
    rate_limit_tpm: int | None = Field(None, ge=1)
    credit_rate_limits: dict[str, Any] | None = None
    features: dict[str, Any] | None = None
    margin_percent: float = Field(default=0.0, ge=0.0, le=100.0)
    billing_cycle_days: int = Field(default=30, ge=1)
    display_order: int = Field(default=0, ge=0)


class UpdatePlanRequest(BaseModel):
    """Request to update a plan."""

    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = None
    monthly_credits_micros: int | None = Field(None, ge=0)
    extra_credit_price_micros: int | None = Field(None, ge=0)
    extra_credit_lifetime_days: int | None = Field(None, ge=1)
    rate_limit_rpm: int | None = Field(None, ge=1)
    rate_limit_tpm: int | None = Field(None, ge=1)
    credit_rate_limits: dict[str, Any] | None = None
    features: dict[str, Any] | None = None
    margin_percent: float | None = Field(None, ge=0.0, le=100.0)
    billing_cycle_days: int | None = Field(None, ge=1)
    display_order: int | None = Field(None, ge=0)
    status: str | None = Field(None, pattern="^(active|archived|draft)$")


class PlanResponse(BaseModel):
    """Plan information response."""

    id: str
    partner_id: str
    name: str
    slug: str
    status: str
    description: str | None
    monthly_credits_micros: int
    extra_credit_price_micros: int
    extra_credit_lifetime_days: int
    rate_limit_rpm: int | None
    rate_limit_tpm: int | None
    credit_rate_limits: dict[str, Any]
    features: dict[str, Any]
    margin_percent: float
    billing_cycle_days: int
    display_order: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, plan) -> "PlanResponse":
        """Create response from database model."""
        status = plan.status.value if hasattr(plan.status, "value") else plan.status
        return cls(
            id=str(plan.id),
            partner_id=str(plan.partner_id),
            name=plan.name,
            slug=plan.slug,
            status=status,
            description=plan.description,
            monthly_credits_micros=plan.monthly_credits_micros,
            extra_credit_price_micros=plan.extra_credit_price_micros,
            extra_credit_lifetime_days=plan.extra_credit_lifetime_days,
            rate_limit_rpm=plan.rate_limit_rpm,
            rate_limit_tpm=plan.rate_limit_tpm,
            credit_rate_limits=plan.credit_rate_limits or {},
            features=plan.features or {},
            margin_percent=float(plan.margin_percent),
            billing_cycle_days=plan.billing_cycle_days,
            display_order=plan.display_order,
            created_at=plan.created_at,
            updated_at=plan.updated_at,
        )


# ============================================================================
# Endpoints
# ============================================================================


@router.post("", response_model=PlanResponse)
async def create_plan(
    body: CreatePlanRequest,
    request: Request,
    auth_ctx: dict = Depends(require_partner_or_owner),
    service: SubscriptionService = Depends(get_subscription_service),
):
    """Create a new subscription plan.

    Partners can only create plans for themselves.
    Platform owners must specify partner_id in the request.
    """
    # Get partner_id
    partner_id = auth_ctx.get("partner_id")
    if not partner_id:
        # Platform owner must specify which partner
        partner_id = request.query_params.get("partner_id")
        if not partner_id:
            raise HTTPException(
                status_code=400,
                detail="partner_id query parameter required for platform owners",
            )
        partner_id = UUID(partner_id)

    plan = await service.create_plan(
        partner_id=partner_id,
        name=body.name,
        slug=body.slug,
        description=body.description,
        monthly_credits_micros=body.monthly_credits_micros,
        extra_credit_price_micros=body.extra_credit_price_micros,
        extra_credit_lifetime_days=body.extra_credit_lifetime_days,
        rate_limit_rpm=body.rate_limit_rpm,
        rate_limit_tpm=body.rate_limit_tpm,
        credit_rate_limits=body.credit_rate_limits,
        features=body.features,
        margin_percent=body.margin_percent,
        billing_cycle_days=body.billing_cycle_days,
        display_order=body.display_order,
    )

    logger.info(
        "Plan created",
        partner_id=str(partner_id),
        plan_id=str(plan.id),
        slug=body.slug,
    )

    return PlanResponse.from_model(plan)


@router.get("", response_model=list[PlanResponse])
async def list_plans(
    request: Request,
    include_archived: bool = False,
    auth_ctx: dict = Depends(require_partner_or_owner),
    service: SubscriptionService = Depends(get_subscription_service),
):
    """List all plans for a partner.

    Partners see only their own plans.
    Platform owners must specify partner_id.
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

    plans = await service.list_plans(
        partner_id=partner_id,
        include_archived=include_archived,
    )

    return [PlanResponse.from_model(p) for p in plans]


@router.get("/{plan_id}", response_model=PlanResponse)
async def get_plan(
    plan_id: UUID,
    auth_ctx: dict = Depends(require_partner_or_owner),
    service: SubscriptionService = Depends(get_subscription_service),
):
    """Get a specific plan by ID."""
    plan = await service.get_plan(plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")

    # Verify ownership
    if auth_ctx["role"] == "partner" and plan.partner_id != auth_ctx["partner_id"]:
        raise HTTPException(status_code=403, detail="Plan belongs to another partner")

    return PlanResponse.from_model(plan)


@router.put("/{plan_id}", response_model=PlanResponse)
async def update_plan(
    plan_id: UUID,
    body: UpdatePlanRequest,
    auth_ctx: dict = Depends(require_partner_or_owner),
    service: SubscriptionService = Depends(get_subscription_service),
):
    """Update a plan."""
    # Verify ownership first
    existing = await service.get_plan(plan_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Plan not found")

    if auth_ctx["role"] == "partner" and existing.partner_id != auth_ctx["partner_id"]:
        raise HTTPException(status_code=403, detail="Plan belongs to another partner")

    # Build updates dict from non-None values
    updates = {k: v for k, v in body.model_dump().items() if v is not None}

    plan = await service.update_plan(plan_id, **updates)

    logger.info(
        "Plan updated",
        plan_id=str(plan_id),
        updates=list(updates.keys()),
    )

    return PlanResponse.from_model(plan)


@router.delete("/{plan_id}")
async def archive_plan(
    plan_id: UUID,
    auth_ctx: dict = Depends(require_partner_or_owner),
    service: SubscriptionService = Depends(get_subscription_service),
):
    """Archive a plan (soft delete).

    Archived plans cannot be assigned to new subscriptions
    but existing subscriptions continue to work.
    """
    # Verify ownership first
    existing = await service.get_plan(plan_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Plan not found")

    if auth_ctx["role"] == "partner" and existing.partner_id != auth_ctx["partner_id"]:
        raise HTTPException(status_code=403, detail="Plan belongs to another partner")

    await service.archive_plan(plan_id)

    logger.info("Plan archived", plan_id=str(plan_id))

    return {"status": "archived", "plan_id": str(plan_id)}
