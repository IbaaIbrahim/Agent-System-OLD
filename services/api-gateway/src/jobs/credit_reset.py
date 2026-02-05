"""Monthly credit reset job.

Runs daily to reset plan credits for subscriptions entering a new billing period.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from libs.common import get_logger
from libs.db.models import PartnerPlan, SubscriptionStatus, TenantSubscription
from libs.db.session import get_session_context

logger = get_logger(__name__)


async def reset_monthly_credits() -> int:
    """Reset plan credits for subscriptions starting a new billing period.

    Should be run daily (e.g., at 00:05 UTC).

    For each subscription where current_period_end <= now:
    - If cancel_at_period_end is True, mark as cancelled
    - Otherwise, reset plan_credits_remaining_micros and advance period

    Returns:
        Number of subscriptions reset
    """
    now = datetime.now(timezone.utc)
    reset_count = 0
    cancelled_count = 0

    async with get_session_context() as session:
        # Get subscriptions that need reset (period has ended)
        result = await session.execute(
            select(TenantSubscription, PartnerPlan)
            .join(PartnerPlan, TenantSubscription.plan_id == PartnerPlan.id)
            .where(
                TenantSubscription.status.in_([
                    SubscriptionStatus.ACTIVE,
                    SubscriptionStatus.TRIAL,
                ]),
                TenantSubscription.current_period_end <= now,
            )
            .with_for_update()
        )
        rows = list(result.all())

        for subscription, plan in rows:
            # Handle pending cancellation
            if subscription.cancel_at_period_end:
                subscription.status = SubscriptionStatus.CANCELLED
                subscription.plan_credits_remaining_micros = 0
                cancelled_count += 1
                logger.info(
                    "Subscription cancelled at period end",
                    subscription_id=str(subscription.id),
                    tenant_id=str(subscription.tenant_id),
                )
                continue

            # Handle trial ending
            if subscription.status == SubscriptionStatus.TRIAL:
                if subscription.trial_ends_at and subscription.trial_ends_at <= now:
                    subscription.status = SubscriptionStatus.ACTIVE

            # Reset billing period
            subscription.current_period_start = now
            subscription.current_period_end = now + timedelta(days=plan.billing_cycle_days)
            subscription.plan_credits_remaining_micros = plan.monthly_credits_micros
            reset_count += 1

            logger.info(
                "Subscription credits reset",
                subscription_id=str(subscription.id),
                tenant_id=str(subscription.tenant_id),
                new_period_end=subscription.current_period_end.isoformat(),
                credits_reset_to=plan.monthly_credits_micros,
            )

        await session.commit()

    logger.info(
        "Monthly credit reset completed",
        subscriptions_reset=reset_count,
        subscriptions_cancelled=cancelled_count,
    )

    return reset_count
