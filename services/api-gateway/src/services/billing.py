"""Billing service for credit checks and reservations.

Credits are stored as integer microdollars (1,000,000 = $1.00) to avoid
floating-point precision issues. Redis DECRBY works natively with integers.

This service supports both the legacy per-tenant billing and the new
subscription-based billing with plan credits and top-ups.
"""

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import select, update

from libs.common import get_logger
from libs.common.exceptions import AgentSystemError
from libs.db.models import (
    CreditTopUp,
    CreditUsageRecord,
    ModelPricing,
    PartnerPlan,
    TenantSubscription,
    TopUpStatus,
    UsageLedger,
)
from libs.db.session import get_session_context
from libs.messaging.redis import get_redis_client

logger = get_logger(__name__)

# 1 microdollar = $0.000001
MICRODOLLARS_PER_DOLLAR = 1_000_000


@dataclass
class CreditConsumptionResult:
    """Result of credit consumption."""

    total_consumed_micros: int
    plan_credits_used_micros: int
    topup_credits_used_micros: int
    remaining_plan_credits_micros: int
    remaining_topup_credits_micros: int


class BillingError(AgentSystemError):
    """Billing-specific error."""

    def __init__(self, message: str, details: dict | None = None) -> None:
        super().__init__(message=message, status_code=402, details=details)


class BillingService:
    """Manages credit checks, reservations, and balance tracking.

    All monetary values are in microdollars (int): 1,000,000 = $1.00.

    Partners can use two billing modes (configured in partner.settings):
    - "pool": Shared credit pool across all partner's tenants (default)
    - "per_tenant": Each tenant manages its own credits independently
    """

    # Redis key patterns
    BALANCE_KEY = "tenant:{tenant_id}:balance"
    PARTNER_BALANCE_KEY = "partner:{partner_id}:balance"
    RESERVATION_KEY = "reservation:{reservation_id}"
    BALANCE_CACHE_TTL = 60  # seconds

    async def check_credit_balance(
        self, tenant_id: UUID, estimated_tokens: int, provider: str, model_id: str
    ) -> bool:
        """Check if tenant has sufficient credits for estimated usage.

        Args:
            tenant_id: Tenant identifier
            estimated_tokens: Estimated input tokens for the request
            provider: LLM provider name
            model_id: LLM model identifier

        Returns:
            True if sufficient credits are available
        """
        balance_micros = await self._get_balance(tenant_id)
        estimated_cost_micros = await self._estimate_cost(
            estimated_tokens, provider, model_id
        )

        has_sufficient = balance_micros >= estimated_cost_micros

        logger.info(
            "Credit balance check",
            tenant_id=str(tenant_id),
            balance_micros=balance_micros,
            estimated_cost_micros=estimated_cost_micros,
            sufficient=has_sufficient,
        )

        return has_sufficient

    async def reserve_credits(
        self, tenant_id: UUID, estimated_cost_micros: int
    ) -> str:
        """Reserve credits for a job using atomic Redis decrement.

        Args:
            tenant_id: Tenant identifier
            estimated_cost_micros: Estimated cost in microdollars

        Returns:
            Reservation ID for tracking/refund

        Raises:
            BillingError: If reservation fails (insufficient funds after race)
        """
        redis = await get_redis_client()
        balance_key = self.BALANCE_KEY.format(tenant_id=tenant_id)

        # Atomic decrement — if another request races, one will go negative
        new_balance = await redis.client.decrby(balance_key, estimated_cost_micros)

        if new_balance < 0:
            # Race condition: restore and reject
            await redis.client.incrby(balance_key, estimated_cost_micros)
            raise BillingError(
                "Insufficient credits after reservation attempt",
                details={
                    "tenant_id": str(tenant_id),
                    "estimated_cost_micros": estimated_cost_micros,
                },
            )

        # Store reservation metadata for audit/refund
        reservation_id = str(uuid4())
        reservation_key = self.RESERVATION_KEY.format(reservation_id=reservation_id)

        await redis.client.hset(
            reservation_key,
            mapping={
                "tenant_id": str(tenant_id),
                "amount_micros": str(estimated_cost_micros),
                "timestamp": str(time.time()),
                "status": "reserved",
            },
        )
        await redis.expire(reservation_key, 3600)  # 1 hour TTL

        logger.info(
            "Credits reserved",
            tenant_id=str(tenant_id),
            reservation_id=reservation_id,
            amount_micros=estimated_cost_micros,
            new_balance_micros=new_balance,
        )

        return reservation_id

    async def release_reservation(
        self, reservation_id: str, actual_cost_micros: int
    ) -> None:
        """Settle a reservation: refund difference between estimated and actual cost.

        Args:
            reservation_id: Reservation ID from reserve_credits()
            actual_cost_micros: Actual cost after job completion
        """
        redis = await get_redis_client()
        reservation_key = self.RESERVATION_KEY.format(reservation_id=reservation_id)

        reservation = await redis.client.hgetall(reservation_key)
        if not reservation:
            logger.warning(
                "Reservation not found (may have expired)",
                reservation_id=reservation_id,
            )
            return

        tenant_id = reservation[b"tenant_id"].decode()
        reserved_micros = int(reservation[b"amount_micros"].decode())
        balance_key = self.BALANCE_KEY.format(tenant_id=tenant_id)

        # Refund the difference (reserved - actual)
        refund_micros = reserved_micros - actual_cost_micros
        if refund_micros > 0:
            await redis.client.incrby(balance_key, refund_micros)
            logger.info(
                "Credits refunded",
                tenant_id=tenant_id,
                reservation_id=reservation_id,
                refund_micros=refund_micros,
            )

        # Mark reservation as settled
        await redis.client.hset(
            reservation_key,
            mapping={
                "status": "settled",
                "actual_cost_micros": str(actual_cost_micros),
                "refund_micros": str(max(refund_micros, 0)),
            },
        )

    # -----------------------------------------------------------------------
    # Partner-level billing (pool mode)
    # -----------------------------------------------------------------------

    async def check_partner_credit_balance(
        self,
        partner_id: UUID,
        estimated_tokens: int,
        provider: str,
        model_id: str,
    ) -> bool:
        """Check if partner has sufficient credits (pool mode).

        Args:
            partner_id: Partner identifier
            estimated_tokens: Estimated input tokens
            provider: LLM provider name
            model_id: LLM model identifier

        Returns:
            True if sufficient credits are available
        """
        balance_micros = await self._get_partner_balance(partner_id)
        estimated_cost_micros = await self._estimate_cost(
            estimated_tokens, provider, model_id
        )

        has_sufficient = balance_micros >= estimated_cost_micros

        logger.info(
            "Partner credit balance check",
            partner_id=str(partner_id),
            balance_micros=balance_micros,
            estimated_cost_micros=estimated_cost_micros,
            sufficient=has_sufficient,
        )

        return has_sufficient

    async def reserve_partner_credits(
        self, partner_id: UUID, estimated_cost_micros: int
    ) -> str:
        """Reserve credits from partner's shared pool.

        Args:
            partner_id: Partner identifier
            estimated_cost_micros: Estimated cost in microdollars

        Returns:
            Reservation ID for tracking/refund
        """
        redis = await get_redis_client()
        balance_key = self.PARTNER_BALANCE_KEY.format(partner_id=partner_id)

        new_balance = await redis.client.decrby(balance_key, estimated_cost_micros)

        if new_balance < 0:
            await redis.client.incrby(balance_key, estimated_cost_micros)
            raise BillingError(
                "Insufficient partner credits after reservation attempt",
                details={
                    "partner_id": str(partner_id),
                    "estimated_cost_micros": estimated_cost_micros,
                },
            )

        reservation_id = str(uuid4())
        reservation_key = self.RESERVATION_KEY.format(reservation_id=reservation_id)

        await redis.client.hset(
            reservation_key,
            mapping={
                "partner_id": str(partner_id),
                "amount_micros": str(estimated_cost_micros),
                "timestamp": str(time.time()),
                "status": "reserved",
                "scope": "partner",
            },
        )
        await redis.expire(reservation_key, 3600)

        logger.info(
            "Partner credits reserved",
            partner_id=str(partner_id),
            reservation_id=reservation_id,
            amount_micros=estimated_cost_micros,
            new_balance_micros=new_balance,
        )

        return reservation_id

    async def release_partner_reservation(
        self, reservation_id: str, actual_cost_micros: int
    ) -> None:
        """Settle a partner-level reservation.

        Args:
            reservation_id: Reservation ID from reserve_partner_credits()
            actual_cost_micros: Actual cost after job completion
        """
        redis = await get_redis_client()
        reservation_key = self.RESERVATION_KEY.format(reservation_id=reservation_id)

        reservation = await redis.client.hgetall(reservation_key)
        if not reservation:
            logger.warning(
                "Partner reservation not found (may have expired)",
                reservation_id=reservation_id,
            )
            return

        partner_id = reservation[b"partner_id"].decode()
        reserved_micros = int(reservation[b"amount_micros"].decode())
        balance_key = self.PARTNER_BALANCE_KEY.format(partner_id=partner_id)

        refund_micros = reserved_micros - actual_cost_micros
        if refund_micros > 0:
            await redis.client.incrby(balance_key, refund_micros)
            logger.info(
                "Partner credits refunded",
                partner_id=partner_id,
                reservation_id=reservation_id,
                refund_micros=refund_micros,
            )

        await redis.client.hset(
            reservation_key,
            mapping={
                "status": "settled",
                "actual_cost_micros": str(actual_cost_micros),
                "refund_micros": str(max(refund_micros, 0)),
            },
        )

    async def _get_partner_balance(self, partner_id: UUID) -> int:
        """Get partner credit balance from Redis, falling back to DB.

        Args:
            partner_id: Partner identifier

        Returns:
            Balance in microdollars
        """
        redis = await get_redis_client()
        balance_key = self.PARTNER_BALANCE_KEY.format(partner_id=partner_id)

        cached = await redis.get(balance_key)
        if cached is not None:
            return int(cached)

        # Cache miss — read from partner's credit_balance_micros column
        balance_micros = await self._fetch_partner_balance_from_db(partner_id)
        await redis.set(balance_key, str(balance_micros), ex=self.BALANCE_CACHE_TTL)

        return balance_micros

    async def _fetch_partner_balance_from_db(self, partner_id: UUID) -> int:
        """Fetch partner credit balance from the database.

        Uses the partner's credit_balance_micros column as the initial pool,
        minus total usage from all tenant ledger entries belonging to this partner.

        Args:
            partner_id: Partner identifier

        Returns:
            Balance in microdollars
        """
        from libs.db.models import Partner, Tenant

        async with get_session_context() as session:
            # Get partner's configured balance
            partner_result = await session.execute(
                select(Partner.credit_balance_micros).where(Partner.id == partner_id)
            )
            initial_balance = partner_result.scalar_one_or_none()
            if initial_balance is None:
                initial_balance = 0

            # Sum all costs across all partner's tenants
            result = await session.execute(
                select(func.coalesce(func.sum(UsageLedger.cost), 0))
                .join(Tenant, UsageLedger.tenant_id == Tenant.id)
                .where(Tenant.partner_id == partner_id)
            )
            total_cost_dollars = float(result.scalar_one())

        total_cost_micros = int(total_cost_dollars * MICRODOLLARS_PER_DOLLAR)
        balance_micros = initial_balance - total_cost_micros

        return max(balance_micros, 0)

    async def estimate_cost(
        self, estimated_tokens: int, provider: str, model_id: str
    ) -> int:
        """Public wrapper for cost estimation.

        Args:
            estimated_tokens: Estimated input tokens
            provider: LLM provider
            model_id: Model identifier

        Returns:
            Estimated cost in microdollars
        """
        return await self._estimate_cost(estimated_tokens, provider, model_id)

    async def _get_balance(self, tenant_id: UUID) -> int:
        """Get tenant credit balance, using Redis cache with DB fallback.

        Args:
            tenant_id: Tenant identifier

        Returns:
            Balance in microdollars
        """
        redis = await get_redis_client()
        balance_key = self.BALANCE_KEY.format(tenant_id=tenant_id)

        # Try Redis cache first
        cached = await redis.get(balance_key)
        if cached is not None:
            return int(cached)

        # Cache miss — query DB
        balance_micros = await self._fetch_balance_from_db(tenant_id)

        # Cache the result
        await redis.set(balance_key, str(balance_micros), ex=self.BALANCE_CACHE_TTL)

        return balance_micros

    async def _fetch_balance_from_db(self, tenant_id: UUID) -> int:
        """Fetch tenant credit balance from the usage ledger.

        Computes: initial_balance - sum(all usage costs).

        Args:
            tenant_id: Tenant identifier

        Returns:
            Balance in microdollars
        """
        from ..config import get_config

        config = get_config()

        async with get_session_context() as session:
            # Sum all costs for this tenant
            result = await session.execute(
                select(func.coalesce(func.sum(UsageLedger.cost), 0)).where(
                    UsageLedger.tenant_id == tenant_id
                )
            )
            total_cost_dollars = float(result.scalar_one())

        total_cost_micros = int(total_cost_dollars * MICRODOLLARS_PER_DOLLAR)
        balance_micros = config.default_credit_balance_micros - total_cost_micros

        return max(balance_micros, 0)

    async def _estimate_cost(
        self, estimated_tokens: int, provider: str, model_id: str
    ) -> int:
        """Estimate cost for a request based on model pricing.

        Args:
            estimated_tokens: Estimated input tokens
            provider: LLM provider
            model_id: Model identifier

        Returns:
            Estimated cost in microdollars
        """
        pricing = await self._get_model_pricing(provider, model_id)

        if pricing is None:
            # No pricing info — use conservative default ($0.01 per 1K tokens)
            cost_dollars = (estimated_tokens / 1000) * 0.01
        else:
            cost_dollars = (estimated_tokens / 1000) * float(
                pricing.input_price_per_1k
            )

        return int(cost_dollars * MICRODOLLARS_PER_DOLLAR)

    async def _get_model_pricing(
        self, provider: str, model_id: str
    ) -> ModelPricing | None:
        """Get pricing for a specific model.

        Args:
            provider: LLM provider name
            model_id: Model identifier

        Returns:
            ModelPricing record or None if not configured
        """
        async with get_session_context() as session:
            result = await session.execute(
                select(ModelPricing).where(
                    ModelPricing.provider == provider,
                    ModelPricing.model_id == model_id,
                    ModelPricing.is_active == True,
                )
            )
            return result.scalar_one_or_none()

    # =========================================================================
    # Subscription-based billing (plan credits + top-ups)
    # =========================================================================

    async def get_tenant_credit_balance(self, tenant_id: UUID) -> dict:
        """Get tenant's total credit balance from subscription.

        Returns breakdown of plan credits and top-up credits.

        Args:
            tenant_id: Tenant identifier

        Returns:
            Dict with plan_credits, topup_credits, total_credits
        """
        async with get_session_context() as session:
            # Get subscription
            sub_result = await session.execute(
                select(TenantSubscription).where(
                    TenantSubscription.tenant_id == tenant_id
                )
            )
            subscription = sub_result.scalar_one_or_none()

            plan_credits = 0
            if subscription:
                plan_credits = subscription.plan_credits_remaining_micros

            # Get active top-ups
            topup_result = await session.execute(
                select(CreditTopUp).where(
                    CreditTopUp.tenant_id == tenant_id,
                    CreditTopUp.status == TopUpStatus.ACTIVE,
                    CreditTopUp.remaining_micros > 0,
                    CreditTopUp.expires_at > datetime.now(timezone.utc),
                )
            )
            topups = list(topup_result.scalars().all())
            topup_credits = sum(t.remaining_micros for t in topups)

            return {
                "plan_credits_micros": plan_credits,
                "topup_credits_micros": topup_credits,
                "total_credits_micros": plan_credits + topup_credits,
            }

    async def check_subscription_credits(
        self, tenant_id: UUID, amount_micros: int
    ) -> bool:
        """Check if tenant has sufficient credits (plan + top-ups).

        Args:
            tenant_id: Tenant identifier
            amount_micros: Required credit amount

        Returns:
            True if sufficient credits available
        """
        balance = await self.get_tenant_credit_balance(tenant_id)
        return balance["total_credits_micros"] >= amount_micros

    async def consume_credits(
        self,
        tenant_id: UUID,
        amount_micros: int,
        user_id: UUID | None = None,
        job_id: UUID | None = None,
        feature_slug: str | None = None,
        provider: str = "openai",
        model_id: str = "gpt-4o-mini",
        input_tokens: int = 0,
        output_tokens: int = 0,
        partner_cost_micros: int | None = None,
    ) -> CreditConsumptionResult:
        """Consume credits from tenant's plan and top-ups.

        Priority: Plan credits first, then top-ups (FIFO, skip expired).

        Args:
            tenant_id: Tenant identifier
            amount_micros: Amount to consume
            user_id: User identifier (optional)
            job_id: Job identifier (optional)
            feature_slug: Feature slug (optional)
            provider: LLM provider
            model_id: Model ID
            input_tokens: Input token count
            output_tokens: Output token count
            partner_cost_micros: Partner cost (defaults to amount_micros)

        Returns:
            CreditConsumptionResult with breakdown

        Raises:
            BillingError: If insufficient credits
        """
        if amount_micros <= 0:
            return CreditConsumptionResult(
                total_consumed_micros=0,
                plan_credits_used_micros=0,
                topup_credits_used_micros=0,
                remaining_plan_credits_micros=0,
                remaining_topup_credits_micros=0,
            )

        remaining = amount_micros
        plan_used = 0
        topup_used = 0

        async with get_session_context() as session:
            # Get subscription with lock
            sub_result = await session.execute(
                select(TenantSubscription)
                .where(TenantSubscription.tenant_id == tenant_id)
                .with_for_update()
            )
            subscription = sub_result.scalar_one_or_none()

            # Step 1: Try plan credits first
            if subscription and subscription.plan_credits_remaining_micros > 0:
                plan_consume = min(remaining, subscription.plan_credits_remaining_micros)
                subscription.plan_credits_remaining_micros -= plan_consume
                plan_used = plan_consume
                remaining -= plan_consume

            # Step 2: If still need more, consume from top-ups (FIFO)
            if remaining > 0:
                topup_result = await session.execute(
                    select(CreditTopUp)
                    .where(
                        CreditTopUp.tenant_id == tenant_id,
                        CreditTopUp.status == TopUpStatus.ACTIVE,
                        CreditTopUp.remaining_micros > 0,
                        CreditTopUp.expires_at > datetime.now(timezone.utc),
                    )
                    .order_by(CreditTopUp.created_at.asc())  # FIFO
                    .with_for_update()
                )
                topups = list(topup_result.scalars().all())

                for topup in topups:
                    if remaining <= 0:
                        break

                    consume = min(remaining, topup.remaining_micros)
                    topup.remaining_micros -= consume
                    topup_used += consume
                    remaining -= consume

                    # Mark depleted
                    if topup.remaining_micros == 0:
                        topup.status = TopUpStatus.DEPLETED

            # Check if we got enough
            if remaining > 0:
                # Rollback the session (don't commit partial consumption)
                raise BillingError(
                    "Insufficient credits",
                    details={
                        "tenant_id": str(tenant_id),
                        "required_micros": amount_micros,
                        "available_micros": plan_used + topup_used,
                    },
                )

            # Record usage
            usage_record = CreditUsageRecord(
                tenant_id=tenant_id,
                user_id=user_id,
                job_id=job_id,
                feature_slug=feature_slug,
                credits_consumed_micros=amount_micros,
                plan_credits_used_micros=plan_used,
                topup_credits_used_micros=topup_used,
                partner_cost_micros=partner_cost_micros or amount_micros,
                provider=provider,
                model_id=model_id,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
            session.add(usage_record)

            await session.commit()

            # Get remaining balances
            remaining_plan = 0
            if subscription:
                await session.refresh(subscription)
                remaining_plan = subscription.plan_credits_remaining_micros

            # Sum remaining top-ups
            topup_balance_result = await session.execute(
                select(CreditTopUp).where(
                    CreditTopUp.tenant_id == tenant_id,
                    CreditTopUp.status == TopUpStatus.ACTIVE,
                    CreditTopUp.remaining_micros > 0,
                    CreditTopUp.expires_at > datetime.now(timezone.utc),
                )
            )
            remaining_topup = sum(
                t.remaining_micros for t in topup_balance_result.scalars()
            )

        logger.info(
            "Credits consumed",
            tenant_id=str(tenant_id),
            total_consumed=amount_micros,
            plan_used=plan_used,
            topup_used=topup_used,
            remaining_plan=remaining_plan,
            remaining_topup=remaining_topup,
        )

        return CreditConsumptionResult(
            total_consumed_micros=amount_micros,
            plan_credits_used_micros=plan_used,
            topup_credits_used_micros=topup_used,
            remaining_plan_credits_micros=remaining_plan,
            remaining_topup_credits_micros=remaining_topup,
        )

    async def check_credit_rate_limit(
        self,
        tenant_id: UUID,
        user_id: UUID | None,
        amount_micros: int,
        plan_limits: dict | None = None,
    ) -> None:
        """Check credit consumption rate against plan limits.

        Uses Redis sorted sets to track credit usage over time windows.

        Args:
            tenant_id: Tenant identifier
            user_id: User identifier (optional)
            amount_micros: Amount about to be consumed
            plan_limits: Credit rate limits from plan (optional)
                Format: {"tenant": {"hourly": N, "daily": N}, "user": {"hourly": N, "daily": N}}

        Raises:
            BillingError: If rate limit exceeded
        """
        if not plan_limits:
            return

        redis = await get_redis_client()
        now = time.time()

        # Check tenant limits
        tenant_limits = plan_limits.get("tenant", {})
        if tenant_limits:
            await self._check_credit_window(
                redis,
                f"rate:credit:tenant:{tenant_id}:hourly",
                tenant_limits.get("hourly"),
                3600,  # 1 hour
                amount_micros,
                now,
                "Tenant hourly credit limit exceeded",
            )
            await self._check_credit_window(
                redis,
                f"rate:credit:tenant:{tenant_id}:daily",
                tenant_limits.get("daily"),
                86400,  # 24 hours
                amount_micros,
                now,
                "Tenant daily credit limit exceeded",
            )

        # Check user limits
        if user_id:
            user_limits = plan_limits.get("user", {})
            if user_limits:
                await self._check_credit_window(
                    redis,
                    f"rate:credit:user:{user_id}:hourly",
                    user_limits.get("hourly"),
                    3600,
                    amount_micros,
                    now,
                    "User hourly credit limit exceeded",
                )
                await self._check_credit_window(
                    redis,
                    f"rate:credit:user:{user_id}:daily",
                    user_limits.get("daily"),
                    86400,
                    amount_micros,
                    now,
                    "User daily credit limit exceeded",
                )

    async def _check_credit_window(
        self,
        redis,
        key: str,
        limit: int | None,
        window_seconds: int,
        amount_micros: int,
        now: float,
        error_message: str,
    ) -> None:
        """Check credit usage within a time window.

        Args:
            redis: Redis client
            key: Redis key for the sorted set
            limit: Credit limit for the window (None = no limit)
            window_seconds: Window size in seconds
            amount_micros: Amount about to be consumed
            now: Current timestamp
            error_message: Error message if limit exceeded
        """
        if not limit:
            return

        window_start = now - window_seconds

        # Remove old entries and get current total
        pipe = redis.client.pipeline()
        pipe.zremrangebyscore(key, "-inf", window_start)
        pipe.zrangewithscores(key, 0, -1)
        results = await pipe.execute()

        entries = results[1] or []
        current_total = sum(score for _, score in entries)

        if current_total + amount_micros > limit:
            raise BillingError(
                error_message,
                details={
                    "current_usage": current_total,
                    "limit": limit,
                    "requested": amount_micros,
                },
            )

    async def record_credit_usage(
        self,
        tenant_id: UUID,
        user_id: UUID | None,
        amount_micros: int,
    ) -> None:
        """Record credit usage for rate limiting.

        Should be called after successful credit consumption.

        Args:
            tenant_id: Tenant identifier
            user_id: User identifier (optional)
            amount_micros: Amount consumed
        """
        redis = await get_redis_client()
        now = time.time()

        # Record tenant usage
        await redis.client.zadd(
            f"rate:credit:tenant:{tenant_id}:hourly",
            {f"{now}:{uuid4().hex[:8]}": amount_micros},
        )
        await redis.expire(f"rate:credit:tenant:{tenant_id}:hourly", 3700)

        await redis.client.zadd(
            f"rate:credit:tenant:{tenant_id}:daily",
            {f"{now}:{uuid4().hex[:8]}": amount_micros},
        )
        await redis.expire(f"rate:credit:tenant:{tenant_id}:daily", 86500)

        # Record user usage
        if user_id:
            await redis.client.zadd(
                f"rate:credit:user:{user_id}:hourly",
                {f"{now}:{uuid4().hex[:8]}": amount_micros},
            )
            await redis.expire(f"rate:credit:user:{user_id}:hourly", 3700)

            await redis.client.zadd(
                f"rate:credit:user:{user_id}:daily",
                {f"{now}:{uuid4().hex[:8]}": amount_micros},
            )
            await redis.expire(f"rate:credit:user:{user_id}:daily", 86500)

    async def expire_topups(self) -> int:
        """Mark expired top-ups.

        Should be called periodically by a background job.

        Returns:
            Number of top-ups expired
        """
        async with get_session_context() as session:
            result = await session.execute(
                update(CreditTopUp)
                .where(
                    CreditTopUp.status == TopUpStatus.ACTIVE,
                    CreditTopUp.expires_at <= datetime.now(timezone.utc),
                )
                .values(status=TopUpStatus.EXPIRED)
                .returning(CreditTopUp.id)
            )
            expired_ids = list(result.scalars().all())
            await session.commit()

            if expired_ids:
                logger.info(
                    "Top-ups expired",
                    count=len(expired_ids),
                )

            return len(expired_ids)


def estimate_tokens_from_messages(messages: list) -> int:
    """Rough token estimation from message content.

    Uses ~4 characters per token heuristic for estimation.

    Args:
        messages: List of chat messages

    Returns:
        Estimated token count
    """
    total_chars = 0
    for msg in messages:
        content = getattr(msg, "content", None) or ""
        total_chars += len(content)
    # ~4 chars per token is a reasonable approximation
    return max(total_chars // 4, 100)  # Minimum 100 tokens
