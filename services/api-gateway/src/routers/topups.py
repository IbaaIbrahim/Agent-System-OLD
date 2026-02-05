"""Credit top-up and balance management endpoints."""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from libs.common import get_logger
from libs.db import get_session_context
from libs.db.models import Tenant
from ..services.billing import BillingService
from ..services.subscription import SubscriptionService, get_subscription_service

logger = get_logger(__name__)

router = APIRouter(prefix="/tenants", tags=["Credits"])


# ============================================================================
# Dependencies
# ============================================================================


async def require_access_to_tenant(request: Request, tenant_id: UUID) -> dict:
    """Verify access to tenant credits.

    Returns:
        Dict with role and write permission
    """
    is_platform_owner = getattr(request.state, "is_platform_owner", False)
    is_partner = getattr(request.state, "is_partner", False)
    partner_id = getattr(request.state, "partner_id", None)
    request_tenant_id = getattr(request.state, "tenant_id", None)

    if is_platform_owner:
        return {"role": "platform_owner", "can_write": True}

    if is_partner and partner_id:
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
        return {"role": "tenant", "can_write": True}

    raise HTTPException(
        status_code=403,
        detail="Access denied to this tenant's credits",
    )


def get_billing_service() -> BillingService:
    """Get billing service instance."""
    return BillingService()


# ============================================================================
# Request/Response Models
# ============================================================================


class PurchaseTopUpRequest(BaseModel):
    """Request to purchase additional credits."""

    amount_micros: int = Field(..., gt=0, description="Credit amount to purchase")
    external_transaction_id: str | None = None


class TopUpResponse(BaseModel):
    """Top-up information response."""

    id: str
    tenant_id: str
    amount_micros: int
    remaining_micros: int
    price_paid_micros: int
    status: str
    expires_at: datetime
    external_transaction_id: str | None
    created_at: datetime

    @classmethod
    def from_model(cls, topup) -> "TopUpResponse":
        """Create response from database model."""
        status = topup.status.value if hasattr(topup.status, "value") else topup.status
        return cls(
            id=str(topup.id),
            tenant_id=str(topup.tenant_id),
            amount_micros=topup.amount_micros,
            remaining_micros=topup.remaining_micros,
            price_paid_micros=topup.price_paid_micros,
            status=status,
            expires_at=topup.expires_at,
            external_transaction_id=topup.external_transaction_id,
            created_at=topup.created_at,
        )


class CreditBalanceResponse(BaseModel):
    """Credit balance response."""

    tenant_id: str
    plan_credits_micros: int
    topup_credits_micros: int
    total_credits_micros: int


class CreditUsageResponse(BaseModel):
    """Credit usage summary response."""

    tenant_id: str
    period_start: datetime
    period_end: datetime
    total_consumed_micros: int
    plan_credits_used_micros: int
    topup_credits_used_micros: int
    request_count: int


# ============================================================================
# Endpoints
# ============================================================================


@router.get("/{tenant_id}/credits", response_model=CreditBalanceResponse)
async def get_credit_balance(
    tenant_id: UUID,
    request: Request,
    billing: BillingService = Depends(get_billing_service),
):
    """Get tenant's current credit balance.

    Returns breakdown of plan credits and top-up credits.
    """
    await require_access_to_tenant(request, tenant_id)

    balance = await billing.get_tenant_credit_balance(tenant_id)

    return CreditBalanceResponse(
        tenant_id=str(tenant_id),
        plan_credits_micros=balance["plan_credits_micros"],
        topup_credits_micros=balance["topup_credits_micros"],
        total_credits_micros=balance["total_credits_micros"],
    )


@router.post("/{tenant_id}/credits/top-up", response_model=TopUpResponse)
async def purchase_topup(
    tenant_id: UUID,
    body: PurchaseTopUpRequest,
    request: Request,
    subscription_service: SubscriptionService = Depends(get_subscription_service),
):
    """Purchase additional credits for a tenant.

    The price and lifetime of the top-up is determined by the
    tenant's current subscription plan.
    """
    await require_access_to_tenant(request, tenant_id)

    topup = await subscription_service.purchase_top_up(
        tenant_id=tenant_id,
        amount_micros=body.amount_micros,
        external_transaction_id=body.external_transaction_id,
    )

    logger.info(
        "Top-up purchased",
        tenant_id=str(tenant_id),
        topup_id=str(topup.id),
        amount_micros=body.amount_micros,
        price_paid_micros=topup.price_paid_micros,
    )

    return TopUpResponse.from_model(topup)


@router.get("/{tenant_id}/credits/top-ups", response_model=list[TopUpResponse])
async def list_topups(
    tenant_id: UUID,
    request: Request,
    status: str | None = None,
    include_expired: bool = False,
    subscription_service: SubscriptionService = Depends(get_subscription_service),
):
    """List all top-ups for a tenant.

    By default, expired top-ups are not included.
    """
    await require_access_to_tenant(request, tenant_id)

    # Convert status string to enum if provided
    status_filter = None
    if status:
        from libs.db.models import TopUpStatus
        try:
            status_filter = TopUpStatus(status)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid status: {status}. Valid values: active, depleted, expired, refunded",
            )

    topups = await subscription_service.list_top_ups(
        tenant_id=tenant_id,
        status=status_filter,
        include_expired=include_expired,
    )

    return [TopUpResponse.from_model(t) for t in topups]


@router.get("/{tenant_id}/credits/usage", response_model=CreditUsageResponse)
async def get_credit_usage(
    tenant_id: UUID,
    request: Request,
    subscription_service: SubscriptionService = Depends(get_subscription_service),
):
    """Get credit usage summary for current billing period.

    Returns total credits consumed broken down by plan vs top-up credits.
    """
    await require_access_to_tenant(request, tenant_id)

    # Get subscription for period info
    subscription = await subscription_service.get_subscription(tenant_id)
    if not subscription:
        raise HTTPException(status_code=404, detail="No subscription found")

    # Get usage records for current period
    from sqlalchemy import func, select
    from libs.db.models import CreditUsageRecord
    async with get_session_context() as session:
        result = await session.execute(
            select(
                func.coalesce(func.sum(CreditUsageRecord.credits_consumed_micros), 0),
                func.coalesce(func.sum(CreditUsageRecord.plan_credits_used_micros), 0),
                func.coalesce(func.sum(CreditUsageRecord.topup_credits_used_micros), 0),
                func.count(CreditUsageRecord.id),
            ).where(
                CreditUsageRecord.tenant_id == tenant_id,
                CreditUsageRecord.created_at >= subscription.current_period_start,
                CreditUsageRecord.created_at < subscription.current_period_end,
            )
        )
        row = result.first()
        total_consumed = int(row[0]) if row else 0
        plan_used = int(row[1]) if row else 0
        topup_used = int(row[2]) if row else 0
        request_count = int(row[3]) if row else 0

    return CreditUsageResponse(
        tenant_id=str(tenant_id),
        period_start=subscription.current_period_start,
        period_end=subscription.current_period_end,
        total_consumed_micros=total_consumed,
        plan_credits_used_micros=plan_used,
        topup_credits_used_micros=topup_used,
        request_count=request_count,
    )
