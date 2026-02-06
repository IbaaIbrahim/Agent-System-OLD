"""Unit tests for credit consumption in the BillingService.

Tests cover FIFO credit consumption, plan vs top-up priority, rate limiting, and cost calculation.
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest



# Fix paths for imports to ensure both project root (for libs) and service root (for src) are in sys.path
_TEST_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _TEST_DIR.parent.parent.parent
_SERVICE_ROOT = _PROJECT_ROOT / "services" / "api-gateway"

if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


if str(_SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(_SERVICE_ROOT))

from src.services.billing import (  # noqa: E402

    BillingService,
    BillingError,
    CreditConsumptionResult,
)


class TestCreditConsumptionZeroAmount:
    """Test zero amount consumption."""

    @pytest.fixture
    def billing(self) -> BillingService:
        return BillingService()

    async def test_zero_amount_returns_empty_result(
        self, billing: BillingService
    ) -> None:
        """Should return empty result for zero amount."""
        tenant_id = uuid4()

        result = await billing.consume_credits(
            tenant_id=tenant_id,
            amount_micros=0,
        )

        assert result.total_consumed_micros == 0
        assert result.plan_credits_used_micros == 0
        assert result.topup_credits_used_micros == 0


class TestCreditConsumptionInsufficientFunds:
    """Test insufficient credit scenarios."""

    @pytest.fixture
    def billing(self) -> BillingService:
        return BillingService()

    async def test_raises_error_when_insufficient_credits(
        self, billing: BillingService
    ) -> None:
        """Should raise BillingError when total credits are insufficient."""
        tenant_id = uuid4()
        cost = 10_000_000  # $10

        # Mock subscription with insufficient credits
        mock_sub = MagicMock()
        mock_sub.plan_credits_remaining_micros = 1_000_000  # Only $1

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=mock_sub)

        # Mock topup result - empty
        mock_topup_result = MagicMock()
        mock_topup_result.scalars = MagicMock()
        mock_topup_result.scalars.return_value.all = MagicMock(return_value=[])

        call_count = 0

        def mock_execute(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return mock_result
            return mock_topup_result

        mock_session.execute = AsyncMock(side_effect=mock_execute)

        with patch(
            "src.services.billing.get_session_context"
        ) as mock_ctx:
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=None)

            with pytest.raises(BillingError) as exc_info:
                await billing.consume_credits(
                    tenant_id=tenant_id,
                    amount_micros=cost,
                )

            assert "insufficient" in str(exc_info.value).lower()


class TestCreditRateLimiting:
    """Test credit rate limiting functionality."""

    @pytest.fixture
    def billing(self) -> BillingService:
        return BillingService()

    async def test_no_limits_passes_silently(self, billing: BillingService) -> None:
        """Should pass when no limits are configured."""
        tenant_id = uuid4()

        # Should not raise
        await billing.check_credit_rate_limit(
            tenant_id=tenant_id,
            user_id=None,
            amount_micros=1_000_000,
            plan_limits=None,
        )

    async def test_raises_error_when_over_tenant_hourly(
        self, billing: BillingService
    ) -> None:
        """Should raise BillingError when tenant hourly limit exceeded."""
        tenant_id = uuid4()

        plan_limits = {
            "tenant": {"hourly": 5_000_000, "daily": 50_000_000},
        }

        mock_redis = AsyncMock()
        mock_redis.client = AsyncMock()

        # Pipeline returns: [None, [(entry, score)...]]
        # Simulate 4.5M already used
        pipe = AsyncMock()
        pipe.zremrangebyscore = MagicMock()
        pipe.zrangewithscores = MagicMock()
        pipe.execute = AsyncMock(
            return_value=[None, [(b"entry1", 4_500_000.0)]]
        )
        mock_redis.client.pipeline = MagicMock(return_value=pipe)

        with patch(
            "src.services.billing.get_redis_client",
            new_callable=AsyncMock,
            return_value=mock_redis,
        ):
            with pytest.raises(BillingError) as exc_info:
                await billing.check_credit_rate_limit(
                    tenant_id=tenant_id,
                    user_id=None,
                    amount_micros=1_000_000,  # Would exceed 5M limit
                    plan_limits=plan_limits,
                )

            assert "limit exceeded" in str(exc_info.value).lower()


class TestGetTenantCreditBalance:
    """Test getting tenant credit balance."""

    @pytest.fixture
    def billing(self) -> BillingService:
        return BillingService()

    async def test_returns_plan_and_topup_credits(
        self, billing: BillingService
    ) -> None:
        """Should return both plan credits and top-up credits."""
        tenant_id = uuid4()

        mock_sub = MagicMock()
        mock_sub.plan_credits_remaining_micros = 5_000_000

        topup1 = MagicMock()
        topup1.remaining_micros = 2_000_000
        topup2 = MagicMock()
        topup2.remaining_micros = 1_500_000

        mock_session = AsyncMock()

        # First call returns subscription
        # Second call returns top-ups
        call_count = 0

        def mock_execute(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            result = MagicMock()
            if call_count == 1:
                result.scalar_one_or_none = MagicMock(return_value=mock_sub)
            else:
                result.scalars = MagicMock()
                result.scalars.return_value.all = MagicMock(
                    return_value=[topup1, topup2]
                )
            return result

        mock_session.execute = AsyncMock(side_effect=mock_execute)

        with patch(
            "src.services.billing.get_session_context"
        ) as mock_ctx:
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=None)

            balance = await billing.get_tenant_credit_balance(tenant_id)

            assert balance["plan_credits_micros"] == 5_000_000
            assert balance["topup_credits_micros"] == 3_500_000  # 2M + 1.5M
            assert balance["total_credits_micros"] == 8_500_000

    async def test_returns_zero_without_subscription(
        self, billing: BillingService
    ) -> None:
        """Should return zeros when tenant has no subscription."""
        tenant_id = uuid4()

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=None)
        mock_result.scalars = MagicMock()
        mock_result.scalars.return_value.all = MagicMock(return_value=[])
        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch(
            "src.services.billing.get_session_context"
        ) as mock_ctx:
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=None)

            balance = await billing.get_tenant_credit_balance(tenant_id)

            assert balance["plan_credits_micros"] == 0
            assert balance["topup_credits_micros"] == 0
            assert balance["total_credits_micros"] == 0


class TestCheckSubscriptionCredits:
    """Test subscription credit checking."""

    @pytest.fixture
    def billing(self) -> BillingService:
        return BillingService()

    async def test_returns_true_with_sufficient_credits(
        self, billing: BillingService
    ) -> None:
        """Should return True when total credits cover the amount."""
        tenant_id = uuid4()

        with patch.object(
            billing,
            "get_tenant_credit_balance",
            new_callable=AsyncMock,
            return_value={
                "plan_credits_micros": 5_000_000,
                "topup_credits_micros": 3_000_000,
                "total_credits_micros": 8_000_000,
            },
        ):
            result = await billing.check_subscription_credits(
                tenant_id=tenant_id,
                amount_micros=5_000_000,
            )

            assert result is True

    async def test_returns_false_with_insufficient_credits(
        self, billing: BillingService
    ) -> None:
        """Should return False when credits are insufficient."""
        tenant_id = uuid4()

        with patch.object(
            billing,
            "get_tenant_credit_balance",
            new_callable=AsyncMock,
            return_value={
                "plan_credits_micros": 1_000_000,
                "topup_credits_micros": 1_000_000,
                "total_credits_micros": 2_000_000,
            },
        ):
            result = await billing.check_subscription_credits(
                tenant_id=tenant_id,
                amount_micros=5_000_000,
            )

            assert result is False


class TestExpireTopups:
    """Test top-up expiration."""

    @pytest.fixture
    def billing(self) -> BillingService:
        return BillingService()

    async def test_expires_old_topups(self, billing: BillingService) -> None:
        """Should mark expired top-ups."""
        expired_id = uuid4()

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars = MagicMock()
        mock_result.scalars.return_value.all = MagicMock(
            return_value=[expired_id]
        )
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.commit = AsyncMock()

        with patch(
            "src.services.billing.get_session_context"
        ) as mock_ctx:
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=None)

            count = await billing.expire_topups()

            assert count == 1

    async def test_returns_zero_when_nothing_to_expire(
        self, billing: BillingService
    ) -> None:
        """Should return zero when no top-ups to expire."""
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars = MagicMock()
        mock_result.scalars.return_value.all = MagicMock(return_value=[])
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.commit = AsyncMock()

        with patch(
            "src.services.billing.get_session_context"
        ) as mock_ctx:
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=None)

            count = await billing.expire_topups()

            assert count == 0


class TestRecordCreditUsage:
    """Test credit usage recording for rate limiting."""

    @pytest.fixture
    def billing(self) -> BillingService:
        return BillingService()

    async def test_records_tenant_usage(self, billing: BillingService) -> None:
        """Should record tenant credit usage."""
        tenant_id = uuid4()

        mock_redis = AsyncMock()
        mock_redis.client = AsyncMock()
        mock_redis.client.zadd = AsyncMock()
        mock_redis.expire = AsyncMock()

        with patch(
            "src.services.billing.get_redis_client",
            new_callable=AsyncMock,
            return_value=mock_redis,
        ):
            await billing.record_credit_usage(
                tenant_id=tenant_id,
                user_id=None,
                amount_micros=1_000_000,
            )

            # Should call zadd for tenant hourly and daily
            assert mock_redis.client.zadd.call_count == 2

    async def test_records_user_usage_when_provided(
        self, billing: BillingService
    ) -> None:
        """Should record user credit usage when user_id provided."""
        tenant_id = uuid4()
        user_id = uuid4()

        mock_redis = AsyncMock()
        mock_redis.client = AsyncMock()
        mock_redis.client.zadd = AsyncMock()
        mock_redis.expire = AsyncMock()

        with patch(
            "src.services.billing.get_redis_client",
            new_callable=AsyncMock,
            return_value=mock_redis,
        ):
            await billing.record_credit_usage(
                tenant_id=tenant_id,
                user_id=user_id,
                amount_micros=1_000_000,
            )

            # Should call zadd 4 times: tenant hourly, tenant daily, user hourly, user daily
            assert mock_redis.client.zadd.call_count == 4


class TestCreditConsumptionResult:
    """Test CreditConsumptionResult dataclass."""

    def test_creates_with_all_fields(self) -> None:
        """Should create result with all fields."""
        result = CreditConsumptionResult(
            total_consumed_micros=5_000_000,
            plan_credits_used_micros=3_000_000,
            topup_credits_used_micros=2_000_000,
            remaining_plan_credits_micros=7_000_000,
            remaining_topup_credits_micros=1_000_000,
        )

        assert result.total_consumed_micros == 5_000_000
        assert result.plan_credits_used_micros == 3_000_000
        assert result.topup_credits_used_micros == 2_000_000
        assert result.remaining_plan_credits_micros == 7_000_000
        assert result.remaining_topup_credits_micros == 1_000_000
