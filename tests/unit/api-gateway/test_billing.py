"""Unit tests for the BillingService.

These tests use mocking since the billing service depends on Redis and PostgreSQL.
"""

import sys
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

    MICRODOLLARS_PER_DOLLAR,
    BillingError,
    BillingService,
    estimate_tokens_from_messages,
)


class TestEstimateTokensFromMessages:
    """Test token estimation from messages."""

    def test_estimates_from_content_length(self) -> None:
        """Should estimate ~4 chars per token."""
        msg = MagicMock()
        msg.content = "Hello world, this is a test message"  # 34 chars

        result = estimate_tokens_from_messages([msg])
        # 34 / 4 = 8, but min is 100
        assert result == 100

    def test_long_message_gives_higher_count(self) -> None:
        """Longer messages should give proportionally higher estimates."""
        msg = MagicMock()
        msg.content = "x" * 2000  # 2000 chars → 500 tokens

        result = estimate_tokens_from_messages([msg])
        assert result == 500

    def test_minimum_100_tokens(self) -> None:
        """Should return at least 100 tokens."""
        msg = MagicMock()
        msg.content = "Hi"

        result = estimate_tokens_from_messages([msg])
        assert result == 100

    def test_none_content_handled(self) -> None:
        """Messages with None content should not crash."""
        msg = MagicMock()
        msg.content = None

        result = estimate_tokens_from_messages([msg])
        assert result == 100  # minimum

    def test_multiple_messages_sum(self) -> None:
        """Token count should sum across all messages."""
        msg1 = MagicMock()
        msg1.content = "x" * 800  # 200 tokens
        msg2 = MagicMock()
        msg2.content = "y" * 1200  # 300 tokens

        result = estimate_tokens_from_messages([msg1, msg2])
        assert result == 500


class TestBillingServiceCheckBalance:
    """Test BillingService.check_credit_balance."""

    @pytest.fixture
    def billing(self) -> BillingService:
        return BillingService()

    async def test_sufficient_balance_returns_true(self, billing: BillingService) -> None:
        """Should return True when balance exceeds estimated cost."""
        tenant_id = uuid4()

        with (
            patch.object(billing, "_get_balance", new_callable=AsyncMock) as mock_balance,
            patch.object(billing, "_estimate_cost", new_callable=AsyncMock) as mock_cost,
        ):
            mock_balance.return_value = 50 * MICRODOLLARS_PER_DOLLAR  # $50
            mock_cost.return_value = 1 * MICRODOLLARS_PER_DOLLAR  # $1

            result = await billing.check_credit_balance(
                tenant_id, 1000, "anthropic", "claude-sonnet-4-20250514"
            )

            assert result is True

    async def test_insufficient_balance_returns_false(self, billing: BillingService) -> None:
        """Should return False when balance is less than estimated cost."""
        tenant_id = uuid4()

        with (
            patch.object(billing, "_get_balance", new_callable=AsyncMock) as mock_balance,
            patch.object(billing, "_estimate_cost", new_callable=AsyncMock) as mock_cost,
        ):
            mock_balance.return_value = 500_000  # $0.50
            mock_cost.return_value = 1 * MICRODOLLARS_PER_DOLLAR  # $1

            result = await billing.check_credit_balance(
                tenant_id, 1000, "anthropic", "claude-sonnet-4-20250514"
            )

            assert result is False

    async def test_exact_balance_returns_true(self, billing: BillingService) -> None:
        """Balance exactly equal to cost should pass."""
        tenant_id = uuid4()

        with (
            patch.object(billing, "_get_balance", new_callable=AsyncMock) as mock_balance,
            patch.object(billing, "_estimate_cost", new_callable=AsyncMock) as mock_cost,
        ):
            mock_balance.return_value = 1_000_000
            mock_cost.return_value = 1_000_000

            result = await billing.check_credit_balance(
                tenant_id, 1000, "anthropic", "claude-sonnet-4-20250514"
            )

            assert result is True


class TestBillingServiceReserveCredits:
    """Test BillingService.reserve_credits."""

    @pytest.fixture
    def billing(self) -> BillingService:
        return BillingService()

    async def test_reserve_returns_reservation_id(self, billing: BillingService) -> None:
        """Successful reservation should return a UUID string."""
        tenant_id = uuid4()
        mock_redis = AsyncMock()
        mock_redis.client = AsyncMock()
        mock_redis.client.decrby = AsyncMock(return_value=49_000_000)
        mock_redis.client.hset = AsyncMock()
        mock_redis.expire = AsyncMock()

        with patch(
            "src.services.billing.get_redis_client",
            new_callable=AsyncMock,
            return_value=mock_redis,
        ):
            reservation_id = await billing.reserve_credits(
                tenant_id, 1_000_000  # $1.00
            )

            assert isinstance(reservation_id, str)
            assert len(reservation_id) > 0
            mock_redis.client.decrby.assert_called_once()

    async def test_reserve_race_condition_restores_balance(
        self, billing: BillingService
    ) -> None:
        """If decrby goes negative (race), balance should be restored and error raised."""
        tenant_id = uuid4()
        mock_redis = AsyncMock()
        mock_redis.client = AsyncMock()
        mock_redis.client.decrby = AsyncMock(return_value=-500_000)  # Went negative
        mock_redis.client.incrby = AsyncMock()

        with patch(
            "src.services.billing.get_redis_client",
            new_callable=AsyncMock,
            return_value=mock_redis,
        ):
            with pytest.raises(BillingError):
                await billing.reserve_credits(tenant_id, 1_000_000)

            # Verify balance was restored
            mock_redis.client.incrby.assert_called_once_with(
                f"tenant:{tenant_id}:balance", 1_000_000
            )


class TestBillingServiceReleaseReservation:
    """Test BillingService.release_reservation."""

    @pytest.fixture
    def billing(self) -> BillingService:
        return BillingService()

    async def test_release_refunds_difference(self, billing: BillingService) -> None:
        """Should refund the difference between reserved and actual cost."""
        reservation_id = str(uuid4())
        tenant_id = str(uuid4())

        mock_redis = AsyncMock()
        mock_redis.client = AsyncMock()
        mock_redis.client.hgetall = AsyncMock(
            return_value={
                b"tenant_id": tenant_id.encode(),
                b"amount_micros": b"5000000",  # Reserved $5
                b"status": b"reserved",
            }
        )
        mock_redis.client.incrby = AsyncMock()
        mock_redis.client.hset = AsyncMock()

        with patch(
            "src.services.billing.get_redis_client",
            new_callable=AsyncMock,
            return_value=mock_redis,
        ):
            await billing.release_reservation(
                reservation_id, 3_000_000  # Actual cost: $3
            )

            # Should refund $2 (5M - 3M = 2M microdollars)
            mock_redis.client.incrby.assert_called_once_with(
                f"tenant:{tenant_id}:balance", 2_000_000
            )

    async def test_release_no_refund_if_actual_equals_reserved(
        self, billing: BillingService
    ) -> None:
        """No refund when actual equals reserved cost."""
        reservation_id = str(uuid4())
        tenant_id = str(uuid4())

        mock_redis = AsyncMock()
        mock_redis.client = AsyncMock()
        mock_redis.client.hgetall = AsyncMock(
            return_value={
                b"tenant_id": tenant_id.encode(),
                b"amount_micros": b"3000000",
                b"status": b"reserved",
            }
        )
        mock_redis.client.incrby = AsyncMock()
        mock_redis.client.hset = AsyncMock()

        with patch(
            "src.services.billing.get_redis_client",
            new_callable=AsyncMock,
            return_value=mock_redis,
        ):
            await billing.release_reservation(reservation_id, 3_000_000)

            # No refund needed
            mock_redis.client.incrby.assert_not_called()

    async def test_release_missing_reservation_does_not_crash(
        self, billing: BillingService
    ) -> None:
        """Expired/missing reservation should log warning but not raise."""
        mock_redis = AsyncMock()
        mock_redis.client = AsyncMock()
        mock_redis.client.hgetall = AsyncMock(return_value={})

        with patch(
            "src.services.billing.get_redis_client",
            new_callable=AsyncMock,
            return_value=mock_redis,
        ):
            # Should not raise
            await billing.release_reservation("nonexistent-id", 1_000_000)
