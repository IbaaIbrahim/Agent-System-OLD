"""Subscription service for managing tenant subscriptions and billing cycles.

Handles plan subscriptions, billing periods, and monthly credit resets.
"""

from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select

from libs.common import get_logger
from libs.common.exceptions import AgentSystemError
from libs.db.models import (
    CreditTopUp,
    Partner,
    PartnerPlan,
    PartnerPlanStatus,
    SubscriptionStatus,
    Tenant,
    TenantSubscription,
    TopUpStatus,
)
from libs.db.session import get_session_context
from libs.messaging.redis import get_redis_client

logger = get_logger(__name__)

# Redis key patterns
SUBSCRIPTION_CACHE_KEY = "subscription:tenant:{tenant_id}"
PLAN_CACHE_KEY = "plan:{plan_id}"
CACHE_TTL = 300  # 5 minutes


class SubscriptionError(AgentSystemError):
    """Subscription-specific error."""

    def __init__(self, message: str, details: dict | None = None) -> None:
        super().__init__(message=message, status_code=400, details=details)


class SubscriptionService:
    """Manages tenant subscriptions and billing cycles."""

    async def create_subscription(
        self,
        tenant_id: UUID,
        plan_id: UUID,
        trial_days: int | None = None,
    ) -> TenantSubscription:
        """Create a new subscription for a tenant.

        Args:
            tenant_id: Tenant identifier
            plan_id: Plan identifier
            trial_days: Optional trial period in days

        Returns:
            Created TenantSubscription

        Raises:
            SubscriptionError: If tenant already has subscription or plan invalid
        """
        async with get_session_context() as session:
            # Check tenant exists and get partner_id
            tenant_result = await session.execute(
                select(Tenant).where(Tenant.id == tenant_id)
            )
            tenant = tenant_result.scalar_one_or_none()
            if not tenant:
                raise SubscriptionError(
                    f"Tenant not found: {tenant_id}",
                    details={"tenant_id": str(tenant_id)},
                )

            # Check for existing subscription
            existing_result = await session.execute(
                select(TenantSubscription).where(
                    TenantSubscription.tenant_id == tenant_id
                )
            )
            if existing_result.scalar_one_or_none():
                raise SubscriptionError(
                    "Tenant already has a subscription",
                    details={"tenant_id": str(tenant_id)},
                )

            # Validate plan exists and belongs to tenant's partner
            plan_result = await session.execute(
                select(PartnerPlan).where(
                    PartnerPlan.id == plan_id,
                    PartnerPlan.status == PartnerPlanStatus.ACTIVE,
                )
            )
            plan = plan_result.scalar_one_or_none()
            if not plan:
                raise SubscriptionError(
                    f"Plan not found or inactive: {plan_id}",
                    details={"plan_id": str(plan_id)},
                )

            # Verify plan belongs to tenant's partner
            if tenant.partner_id and plan.partner_id != tenant.partner_id:
                raise SubscriptionError(
                    "Plan does not belong to tenant's partner",
                    details={
                        "plan_id": str(plan_id),
                        "tenant_partner_id": str(tenant.partner_id),
                        "plan_partner_id": str(plan.partner_id),
                    },
                )

            # Calculate billing period
            now = datetime.now(timezone.utc)
            period_end = now + timedelta(days=plan.billing_cycle_days)

            # Determine initial status
            status = SubscriptionStatus.ACTIVE
            trial_ends_at = None
            if trial_days and trial_days > 0:
                status = SubscriptionStatus.TRIAL
                trial_ends_at = now + timedelta(days=trial_days)

            # Create subscription
            subscription = TenantSubscription(
                tenant_id=tenant_id,
                plan_id=plan_id,
                status=status,
                current_period_start=now,
                current_period_end=period_end,
                plan_credits_remaining_micros=plan.monthly_credits_micros,
                trial_ends_at=trial_ends_at,
            )
            session.add(subscription)
            await session.commit()
            await session.refresh(subscription)

            # Invalidate cache
            await self._invalidate_subscription_cache(tenant_id)

            logger.info(
                "Subscription created",
                tenant_id=str(tenant_id),
                plan_id=str(plan_id),
                subscription_id=str(subscription.id),
                status=status.value,
                credits=plan.monthly_credits_micros,
            )

            return subscription

    async def get_subscription(
        self, tenant_id: UUID
    ) -> TenantSubscription | None:
        """Get tenant's subscription.

        Args:
            tenant_id: Tenant identifier

        Returns:
            TenantSubscription or None
        """
        async with get_session_context() as session:
            result = await session.execute(
                select(TenantSubscription).where(
                    TenantSubscription.tenant_id == tenant_id
                )
            )
            return result.scalar_one_or_none()

    async def get_subscription_with_plan(
        self, tenant_id: UUID
    ) -> tuple[TenantSubscription, PartnerPlan] | None:
        """Get tenant's subscription with associated plan.

        Uses Redis cache for performance.

        Args:
            tenant_id: Tenant identifier

        Returns:
            Tuple of (TenantSubscription, PartnerPlan) or None
        """
        redis = await get_redis_client()
        cache_key = SUBSCRIPTION_CACHE_KEY.format(tenant_id=tenant_id)

        # Try cache
        cached = await redis.get(cache_key)
        if cached:
            import json
            data = json.loads(cached)
            # Reconstruct from cache (simplified - full impl would use Pydantic)
            # For now, just fetch from DB since we need the ORM objects
            pass

        async with get_session_context() as session:
            result = await session.execute(
                select(TenantSubscription, PartnerPlan)
                .join(PartnerPlan, TenantSubscription.plan_id == PartnerPlan.id)
                .where(TenantSubscription.tenant_id == tenant_id)
            )
            row = result.first()
            if not row:
                return None

            subscription, plan = row

            # Check if period needs reset
            now = datetime.now(timezone.utc)
            if subscription.current_period_end <= now:
                subscription = await self._reset_billing_period(
                    session, subscription, plan
                )

            return subscription, plan

    async def change_plan(
        self,
        tenant_id: UUID,
        new_plan_id: UUID,
        prorate: bool = True,
    ) -> TenantSubscription:
        """Change tenant's subscription plan.

        Args:
            tenant_id: Tenant identifier
            new_plan_id: New plan identifier
            prorate: Whether to prorate credits (not implemented yet)

        Returns:
            Updated TenantSubscription
        """
        async with get_session_context() as session:
            # Get existing subscription
            result = await session.execute(
                select(TenantSubscription)
                .where(TenantSubscription.tenant_id == tenant_id)
                .with_for_update()
            )
            subscription = result.scalar_one_or_none()
            if not subscription:
                raise SubscriptionError(
                    "No subscription found",
                    details={"tenant_id": str(tenant_id)},
                )

            # Validate new plan
            plan_result = await session.execute(
                select(PartnerPlan).where(
                    PartnerPlan.id == new_plan_id,
                    PartnerPlan.status == PartnerPlanStatus.ACTIVE,
                )
            )
            new_plan = plan_result.scalar_one_or_none()
            if not new_plan:
                raise SubscriptionError(
                    f"Plan not found or inactive: {new_plan_id}",
                    details={"plan_id": str(new_plan_id)},
                )

            old_plan_id = subscription.plan_id
            subscription.plan_id = new_plan_id

            # For now, simple approach: keep remaining credits, add new plan's credits
            # A more sophisticated approach would prorate based on period usage
            if prorate:
                subscription.plan_credits_remaining_micros += new_plan.monthly_credits_micros
            else:
                subscription.plan_credits_remaining_micros = new_plan.monthly_credits_micros

            await session.commit()
            await session.refresh(subscription)

            # Invalidate cache
            await self._invalidate_subscription_cache(tenant_id)

            logger.info(
                "Subscription plan changed",
                tenant_id=str(tenant_id),
                old_plan_id=str(old_plan_id),
                new_plan_id=str(new_plan_id),
            )

            return subscription

    async def cancel_subscription(
        self,
        tenant_id: UUID,
        at_period_end: bool = True,
    ) -> TenantSubscription:
        """Cancel tenant's subscription.

        Args:
            tenant_id: Tenant identifier
            at_period_end: If True, cancel at end of current period

        Returns:
            Updated TenantSubscription
        """
        async with get_session_context() as session:
            result = await session.execute(
                select(TenantSubscription)
                .where(TenantSubscription.tenant_id == tenant_id)
                .with_for_update()
            )
            subscription = result.scalar_one_or_none()
            if not subscription:
                raise SubscriptionError(
                    "No subscription found",
                    details={"tenant_id": str(tenant_id)},
                )

            now = datetime.now(timezone.utc)

            if at_period_end:
                subscription.cancel_at_period_end = True
                subscription.cancelled_at = now
            else:
                subscription.status = SubscriptionStatus.CANCELLED
                subscription.cancelled_at = now
                subscription.plan_credits_remaining_micros = 0

            await session.commit()
            await session.refresh(subscription)

            # Invalidate cache
            await self._invalidate_subscription_cache(tenant_id)

            logger.info(
                "Subscription cancelled",
                tenant_id=str(tenant_id),
                at_period_end=at_period_end,
            )

            return subscription

    async def reactivate_subscription(
        self, tenant_id: UUID
    ) -> TenantSubscription:
        """Reactivate a cancelled subscription.

        Args:
            tenant_id: Tenant identifier

        Returns:
            Updated TenantSubscription
        """
        async with get_session_context() as session:
            result = await session.execute(
                select(TenantSubscription)
                .where(TenantSubscription.tenant_id == tenant_id)
                .with_for_update()
            )
            subscription = result.scalar_one_or_none()
            if not subscription:
                raise SubscriptionError(
                    "No subscription found",
                    details={"tenant_id": str(tenant_id)},
                )

            subscription.status = SubscriptionStatus.ACTIVE
            subscription.cancel_at_period_end = False
            subscription.cancelled_at = None

            await session.commit()
            await session.refresh(subscription)

            # Invalidate cache
            await self._invalidate_subscription_cache(tenant_id)

            logger.info(
                "Subscription reactivated",
                tenant_id=str(tenant_id),
            )

            return subscription

    async def reset_monthly_credits(
        self, subscription_id: UUID
    ) -> TenantSubscription:
        """Reset plan credits at start of new billing period.

        Args:
            subscription_id: Subscription identifier

        Returns:
            Updated TenantSubscription
        """
        async with get_session_context() as session:
            result = await session.execute(
                select(TenantSubscription, PartnerPlan)
                .join(PartnerPlan, TenantSubscription.plan_id == PartnerPlan.id)
                .where(TenantSubscription.id == subscription_id)
                .with_for_update()
            )
            row = result.first()
            if not row:
                raise SubscriptionError(
                    f"Subscription not found: {subscription_id}",
                    details={"subscription_id": str(subscription_id)},
                )

            subscription, plan = row
            subscription = await self._reset_billing_period(session, subscription, plan)

            # Invalidate cache
            await self._invalidate_subscription_cache(subscription.tenant_id)

            return subscription

    async def _reset_billing_period(
        self,
        session,
        subscription: TenantSubscription,
        plan: PartnerPlan,
    ) -> TenantSubscription:
        """Internal method to reset billing period.

        Args:
            session: DB session
            subscription: Subscription to reset
            plan: Associated plan

        Returns:
            Updated subscription
        """
        now = datetime.now(timezone.utc)

        # Handle cancellation at period end
        if subscription.cancel_at_period_end:
            subscription.status = SubscriptionStatus.CANCELLED
            subscription.plan_credits_remaining_micros = 0
            await session.commit()
            await session.refresh(subscription)
            logger.info(
                "Subscription cancelled at period end",
                subscription_id=str(subscription.id),
                tenant_id=str(subscription.tenant_id),
            )
            return subscription

        # Advance billing period
        subscription.current_period_start = now
        subscription.current_period_end = now + timedelta(days=plan.billing_cycle_days)
        subscription.plan_credits_remaining_micros = plan.monthly_credits_micros

        # Handle trial ending
        if subscription.status == SubscriptionStatus.TRIAL:
            if subscription.trial_ends_at and subscription.trial_ends_at <= now:
                subscription.status = SubscriptionStatus.ACTIVE

        await session.commit()
        await session.refresh(subscription)

        logger.info(
            "Billing period reset",
            subscription_id=str(subscription.id),
            tenant_id=str(subscription.tenant_id),
            new_period_start=subscription.current_period_start.isoformat(),
            new_period_end=subscription.current_period_end.isoformat(),
            credits_reset_to=plan.monthly_credits_micros,
        )

        return subscription

    async def _invalidate_subscription_cache(self, tenant_id: UUID) -> None:
        """Invalidate subscription cache for a tenant."""
        redis = await get_redis_client()
        cache_key = SUBSCRIPTION_CACHE_KEY.format(tenant_id=tenant_id)
        await redis.client.delete(cache_key)

    # =========================================================================
    # Plan Management
    # =========================================================================

    async def create_plan(
        self,
        partner_id: UUID,
        name: str,
        slug: str,
        monthly_credits_micros: int = 0,
        extra_credit_price_micros: int = 1_000_000,
        extra_credit_lifetime_days: int = 365,
        rate_limit_rpm: int | None = None,
        rate_limit_tpm: int | None = None,
        credit_rate_limits: dict | None = None,
        features: dict | None = None,
        margin_percent: float = 0.0,
        billing_cycle_days: int = 30,
        description: str | None = None,
        display_order: int = 0,
    ) -> PartnerPlan:
        """Create a new subscription plan for a partner.

        Args:
            partner_id: Partner identifier
            name: Plan display name
            slug: Plan slug (unique per partner)
            monthly_credits_micros: Monthly credit allocation
            extra_credit_price_micros: Price per 1M extra credits
            extra_credit_lifetime_days: How long top-ups last
            rate_limit_rpm: Requests per minute limit
            rate_limit_tpm: Tokens per minute limit
            credit_rate_limits: Credit consumption rate limits
            features: Feature configuration
            margin_percent: Partner margin percentage
            billing_cycle_days: Billing cycle length
            description: Plan description
            display_order: Display order on pricing page

        Returns:
            Created PartnerPlan
        """
        async with get_session_context() as session:
            # Verify partner exists
            partner_result = await session.execute(
                select(Partner).where(Partner.id == partner_id)
            )
            if not partner_result.scalar_one_or_none():
                raise SubscriptionError(
                    f"Partner not found: {partner_id}",
                    details={"partner_id": str(partner_id)},
                )

            # Check slug uniqueness for this partner
            existing = await session.execute(
                select(PartnerPlan).where(
                    PartnerPlan.partner_id == partner_id,
                    PartnerPlan.slug == slug,
                )
            )
            if existing.scalar_one_or_none():
                raise SubscriptionError(
                    f"Plan slug already exists: {slug}",
                    details={"partner_id": str(partner_id), "slug": slug},
                )

            plan = PartnerPlan(
                partner_id=partner_id,
                name=name,
                slug=slug,
                status=PartnerPlanStatus.ACTIVE,
                monthly_credits_micros=monthly_credits_micros,
                extra_credit_price_micros=extra_credit_price_micros,
                extra_credit_lifetime_days=extra_credit_lifetime_days,
                rate_limit_rpm=rate_limit_rpm,
                rate_limit_tpm=rate_limit_tpm,
                credit_rate_limits=credit_rate_limits or {},
                features=features or {},
                margin_percent=margin_percent,
                billing_cycle_days=billing_cycle_days,
                description=description,
                display_order=display_order,
            )
            session.add(plan)
            await session.commit()
            await session.refresh(plan)

            logger.info(
                "Plan created",
                partner_id=str(partner_id),
                plan_id=str(plan.id),
                slug=slug,
            )

            return plan

    async def get_plan(self, plan_id: UUID) -> PartnerPlan | None:
        """Get a plan by ID.

        Args:
            plan_id: Plan identifier

        Returns:
            PartnerPlan or None
        """
        async with get_session_context() as session:
            result = await session.execute(
                select(PartnerPlan).where(PartnerPlan.id == plan_id)
            )
            return result.scalar_one_or_none()

    async def list_plans(
        self,
        partner_id: UUID,
        include_archived: bool = False,
    ) -> list[PartnerPlan]:
        """List plans for a partner.

        Args:
            partner_id: Partner identifier
            include_archived: Whether to include archived plans

        Returns:
            List of PartnerPlan
        """
        async with get_session_context() as session:
            query = (
                select(PartnerPlan)
                .where(PartnerPlan.partner_id == partner_id)
                .order_by(PartnerPlan.display_order, PartnerPlan.created_at)
            )

            if not include_archived:
                query = query.where(PartnerPlan.status != PartnerPlanStatus.ARCHIVED)

            result = await session.execute(query)
            return list(result.scalars().all())

    async def update_plan(
        self,
        plan_id: UUID,
        **updates,
    ) -> PartnerPlan:
        """Update a plan.

        Args:
            plan_id: Plan identifier
            **updates: Fields to update

        Returns:
            Updated PartnerPlan
        """
        async with get_session_context() as session:
            result = await session.execute(
                select(PartnerPlan)
                .where(PartnerPlan.id == plan_id)
                .with_for_update()
            )
            plan = result.scalar_one_or_none()
            if not plan:
                raise SubscriptionError(
                    f"Plan not found: {plan_id}",
                    details={"plan_id": str(plan_id)},
                )

            # Apply updates
            allowed_fields = {
                "name", "description", "monthly_credits_micros",
                "extra_credit_price_micros", "extra_credit_lifetime_days",
                "rate_limit_rpm", "rate_limit_tpm", "credit_rate_limits",
                "features", "margin_percent", "billing_cycle_days",
                "display_order", "status",
            }
            for key, value in updates.items():
                if key in allowed_fields:
                    setattr(plan, key, value)

            await session.commit()
            await session.refresh(plan)

            # Invalidate plan cache
            redis = await get_redis_client()
            cache_key = PLAN_CACHE_KEY.format(plan_id=plan_id)
            await redis.client.delete(cache_key)

            logger.info(
                "Plan updated",
                plan_id=str(plan_id),
                updates=list(updates.keys()),
            )

            return plan

    async def archive_plan(self, plan_id: UUID) -> PartnerPlan:
        """Archive a plan (soft delete).

        Args:
            plan_id: Plan identifier

        Returns:
            Updated PartnerPlan
        """
        return await self.update_plan(plan_id, status=PartnerPlanStatus.ARCHIVED)

    # =========================================================================
    # Top-Up Management
    # =========================================================================

    async def purchase_top_up(
        self,
        tenant_id: UUID,
        amount_micros: int,
        external_transaction_id: str | None = None,
    ) -> CreditTopUp:
        """Purchase additional credits for a tenant.

        Price and lifetime are determined by the tenant's current plan.

        Args:
            tenant_id: Tenant identifier
            amount_micros: Credit amount to purchase
            external_transaction_id: External payment reference

        Returns:
            Created CreditTopUp
        """
        async with get_session_context() as session:
            # Get subscription and plan
            result = await session.execute(
                select(TenantSubscription, PartnerPlan)
                .join(PartnerPlan, TenantSubscription.plan_id == PartnerPlan.id)
                .where(TenantSubscription.tenant_id == tenant_id)
            )
            row = result.first()
            if not row:
                raise SubscriptionError(
                    "Tenant has no active subscription",
                    details={"tenant_id": str(tenant_id)},
                )

            subscription, plan = row

            # Calculate price based on plan's extra_credit_price_micros (per 1M)
            price_per_credit = plan.extra_credit_price_micros / 1_000_000
            price_paid_micros = int(amount_micros * price_per_credit)

            # Calculate expiration based on plan's extra_credit_lifetime_days
            expires_at = datetime.now(timezone.utc) + timedelta(
                days=plan.extra_credit_lifetime_days
            )

            top_up = CreditTopUp(
                tenant_id=tenant_id,
                subscription_id=subscription.id,
                amount_micros=amount_micros,
                remaining_micros=amount_micros,
                price_paid_micros=price_paid_micros,
                status=TopUpStatus.ACTIVE,
                expires_at=expires_at,
                external_transaction_id=external_transaction_id,
            )
            session.add(top_up)
            await session.commit()
            await session.refresh(top_up)

            logger.info(
                "Top-up purchased",
                tenant_id=str(tenant_id),
                top_up_id=str(top_up.id),
                amount_micros=amount_micros,
                price_paid_micros=price_paid_micros,
                expires_at=expires_at.isoformat(),
            )

            return top_up

    async def list_top_ups(
        self,
        tenant_id: UUID,
        status: TopUpStatus | None = None,
        include_expired: bool = False,
    ) -> list[CreditTopUp]:
        """List top-ups for a tenant.

        Args:
            tenant_id: Tenant identifier
            status: Filter by status
            include_expired: Whether to include expired top-ups

        Returns:
            List of CreditTopUp
        """
        async with get_session_context() as session:
            query = (
                select(CreditTopUp)
                .where(CreditTopUp.tenant_id == tenant_id)
                .order_by(CreditTopUp.created_at.asc())  # FIFO order
            )

            if status:
                query = query.where(CreditTopUp.status == status)
            elif not include_expired:
                query = query.where(CreditTopUp.status != TopUpStatus.EXPIRED)

            result = await session.execute(query)
            return list(result.scalars().all())


# Singleton instance
_subscription_service: SubscriptionService | None = None


def get_subscription_service() -> SubscriptionService:
    """Get subscription service singleton."""
    global _subscription_service
    if _subscription_service is None:
        _subscription_service = SubscriptionService()
    return _subscription_service
