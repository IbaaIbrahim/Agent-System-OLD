"""Tenant subscription management endpoints."""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from libs.common import get_logger
from libs.db import get_session_context
from libs.db.models import Tenant
from ..services.subscription import SubscriptionService, get_subscription_service

logger = get_logger(__name__)

router = APIRouter(prefix="/tenants", tags=["Subscriptions"])


# ============================================================================
# Dependencies
# ============================================================================


async def require_partner_owner_or_tenant(
    request: Request, tenant_id: UUID
) -> dict:
    """Verify access to tenant subscription.

    Platform owners and partners can manage any tenant they own.
    Tenants can only view their own subscription.

    Returns:
        Dict with role and access level
    """
    is_platform_owner = getattr(request.state, "is_platform_owner", False)
    is_partner = getattr(request.state, "is_partner", False)
    partner_id = getattr(request.state, "partner_id", None)
    request_tenant_id = getattr(request.state, "tenant_id", None)

    if is_platform_owner:
        return {"role": "platform_owner", "can_write": True}

    if is_partner and partner_id:
        # Verify tenant belongs to partner
        async with get_session_context() as session:
            result = await session.execute(
                select(Tenant).where(Tenant.id == tenant_id)
            )
            tenant = result.scalar_one_or_none()
            if not tenant:
                raise HTTPException(status_code=404, detail="Tenant not found")
            if tenant.partner_id != partner_id:
                raise HTTPException(
                    status_code=403, detail="Tenant belongs to another partner"
                )
        return {"role": "partner", "can_write": True}

    if request_tenant_id and request_tenant_id == tenant_id:
        return {"role": "tenant", "can_write": False}

    raise HTTPException(
        status_code=403,
        detail="Access denied to this tenant's subscription",
    )


# ============================================================================
# Request/Response Models
# ============================================================================


class CreateSubscriptionRequest(BaseModel):
    """Request to create a subscription."""

    plan_id: UUID
    trial_days: int | None = Field(None, ge=0)


class UpdateSubscriptionRequest(BaseModel):
    """Request to update a subscription."""

    plan_id: UUID | None = None
    prorate: bool = True


class CancelSubscriptionRequest(BaseModel):
    """Request to cancel a subscription."""

    at_period_end: bool = True


class SubscriptionResponse(BaseModel):
    """Subscription information response."""

    id: str
    tenant_id: str
    plan_id: str
    status: str
    current_period_start: datetime
    current_period_end: datetime
    plan_credits_remaining_micros: int
    trial_ends_at: datetime | None
    cancelled_at: datetime | None
    cancel_at_period_end: bool
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, subscription) -> "SubscriptionResponse":
        """Create response from database model."""
        status = (
            subscription.status.value
            if hasattr(subscription.status, "value")
            else subscription.status
        )
        return cls(
            id=str(subscription.id),
            tenant_id=str(subscription.tenant_id),
            plan_id=str(subscription.plan_id),
            status=status,
            current_period_start=subscription.current_period_start,
            current_period_end=subscription.current_period_end,
            plan_credits_remaining_micros=subscription.plan_credits_remaining_micros,
            trial_ends_at=subscription.trial_ends_at,
            cancelled_at=subscription.cancelled_at,
            cancel_at_period_end=subscription.cancel_at_period_end,
            created_at=subscription.created_at,
            updated_at=subscription.updated_at,
        )


class SubscriptionWithPlanResponse(SubscriptionResponse):
    """Subscription response including plan details."""

    plan_name: str
    plan_slug: str
    plan_monthly_credits_micros: int


# ============================================================================
# Endpoints
# ============================================================================


@router.post("/{tenant_id}/subscription", response_model=SubscriptionResponse)
async def create_subscription(
    tenant_id: UUID,
    body: CreateSubscriptionRequest,
    request: Request,
    service: SubscriptionService = Depends(get_subscription_service),
):
    """Create a subscription for a tenant.

    Only partners and platform owners can create subscriptions.
    """
    auth_ctx = await require_partner_owner_or_tenant(request, tenant_id)
    if not auth_ctx["can_write"]:
        raise HTTPException(
            status_code=403,
            detail="Tenants cannot create their own subscriptions",
        )

    subscription = await service.create_subscription(
        tenant_id=tenant_id,
        plan_id=body.plan_id,
        trial_days=body.trial_days,
    )

    logger.info(
        "Subscription created",
        tenant_id=str(tenant_id),
        plan_id=str(body.plan_id),
        subscription_id=str(subscription.id),
    )

    return SubscriptionResponse.from_model(subscription)


@router.get("/{tenant_id}/subscription", response_model=SubscriptionResponse)
async def get_subscription(
    tenant_id: UUID,
    request: Request,
    service: SubscriptionService = Depends(get_subscription_service),
):
    """Get tenant's current subscription."""
    await require_partner_owner_or_tenant(request, tenant_id)

    subscription = await service.get_subscription(tenant_id)
    if not subscription:
        raise HTTPException(status_code=404, detail="No subscription found")

    return SubscriptionResponse.from_model(subscription)


@router.put("/{tenant_id}/subscription", response_model=SubscriptionResponse)
async def update_subscription(
    tenant_id: UUID,
    body: UpdateSubscriptionRequest,
    request: Request,
    service: SubscriptionService = Depends(get_subscription_service),
):
    """Update tenant's subscription (e.g., change plan)."""
    auth_ctx = await require_partner_owner_or_tenant(request, tenant_id)
    if not auth_ctx["can_write"]:
        raise HTTPException(
            status_code=403,
            detail="Tenants cannot modify their own subscriptions",
        )

    if body.plan_id:
        subscription = await service.change_plan(
            tenant_id=tenant_id,
            new_plan_id=body.plan_id,
            prorate=body.prorate,
        )
        logger.info(
            "Subscription plan changed",
            tenant_id=str(tenant_id),
            new_plan_id=str(body.plan_id),
        )
    else:
        subscription = await service.get_subscription(tenant_id)
        if not subscription:
            raise HTTPException(status_code=404, detail="No subscription found")

    return SubscriptionResponse.from_model(subscription)


@router.delete("/{tenant_id}/subscription")
async def cancel_subscription(
    tenant_id: UUID,
    request: Request,
    at_period_end: bool = True,
    service: SubscriptionService = Depends(get_subscription_service),
):
    """Cancel tenant's subscription.

    By default, cancellation takes effect at the end of the current
    billing period. Set at_period_end=false to cancel immediately.
    """
    auth_ctx = await require_partner_owner_or_tenant(request, tenant_id)
    if not auth_ctx["can_write"]:
        raise HTTPException(
            status_code=403,
            detail="Tenants cannot cancel their own subscriptions",
        )

    subscription = await service.cancel_subscription(
        tenant_id=tenant_id,
        at_period_end=at_period_end,
    )

    logger.info(
        "Subscription cancelled",
        tenant_id=str(tenant_id),
        at_period_end=at_period_end,
    )

    return {
        "status": "cancelled" if not at_period_end else "pending_cancellation",
        "subscription_id": str(subscription.id),
        "cancel_at_period_end": subscription.cancel_at_period_end,
    }


@router.post("/{tenant_id}/subscription/reactivate", response_model=SubscriptionResponse)
async def reactivate_subscription(
    tenant_id: UUID,
    request: Request,
    service: SubscriptionService = Depends(get_subscription_service),
):
    """Reactivate a cancelled subscription.

    Only works if the subscription was cancelled but hasn't
    reached the end of its billing period yet.
    """
    auth_ctx = await require_partner_owner_or_tenant(request, tenant_id)
    if not auth_ctx["can_write"]:
        raise HTTPException(
            status_code=403,
            detail="Tenants cannot reactivate subscriptions",
        )

    subscription = await service.reactivate_subscription(tenant_id)

    logger.info(
        "Subscription reactivated",
        tenant_id=str(tenant_id),
    )

    return SubscriptionResponse.from_model(subscription)
