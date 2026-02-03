"""Billing service for credit checks and reservations.

Credits are stored as integer microdollars (1,000,000 = $1.00) to avoid
floating-point precision issues. Redis DECRBY works natively with integers.
"""

import time
from uuid import UUID, uuid4

from sqlalchemy import func, select

from libs.common import get_logger
from libs.common.exceptions import AgentSystemError
from libs.db.models import ModelPricing, UsageLedger
from libs.db.session import get_session_context
from libs.messaging.redis import get_redis_client

logger = get_logger(__name__)

# 1 microdollar = $0.000001
MICRODOLLARS_PER_DOLLAR = 1_000_000


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
