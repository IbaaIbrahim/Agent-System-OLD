"""Unit tests for the WalletService.

Tests cover partner wallet operations including deposits, debits, and balance checks.
"""

import sys
from datetime import datetime, timezone
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

from src.services.wallet import (  # noqa: E402

    WalletService,
    WalletError,
    WALLET_BALANCE_KEY,
)


class TestWalletServiceGetBalance:
    """Test WalletService.get_balance."""

    @pytest.fixture
    def service(self) -> WalletService:
        return WalletService()

    async def test_get_balance_from_cache(self, service: WalletService) -> None:
        """Should return cached balance when available."""
        partner_id = uuid4()
        cached_balance = 50_000_000  # $50

        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=str(cached_balance))

        with patch(
            "src.services.wallet.get_redis_client",
            new_callable=AsyncMock,
            return_value=mock_redis,
        ):
            result = await service.get_balance(partner_id)

            assert result == cached_balance
            mock_redis.get.assert_called_once_with(
                WALLET_BALANCE_KEY.format(partner_id=partner_id)
            )

    async def test_get_balance_cache_miss_queries_db(
        self, service: WalletService
    ) -> None:
        """Should query database and cache result on cache miss."""
        partner_id = uuid4()
        db_balance = 25_000_000  # $25

        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)
        mock_redis.set = AsyncMock()

        mock_wallet = MagicMock()
        mock_wallet.balance_micros = db_balance

        with (
            patch(
                "src.services.wallet.get_redis_client",
                new_callable=AsyncMock,
                return_value=mock_redis,
            ),
            patch.object(
                service,
                "get_or_create_wallet",
                new_callable=AsyncMock,
                return_value=mock_wallet,
            ),
        ):
            result = await service.get_balance(partner_id)

            assert result == db_balance
            # Verify cache was populated
            mock_redis.set.assert_called_once()


class TestWalletServiceDeposit:
    """Test WalletService.deposit."""

    @pytest.fixture
    def service(self) -> WalletService:
        return WalletService()

    async def test_deposit_creates_record(self, service: WalletService) -> None:
        """Deposit should create a record and update balance."""
        partner_id = uuid4()
        amount = 10_000_000  # $10

        mock_wallet = MagicMock()
        mock_wallet.id = uuid4()
        mock_wallet.balance_micros = 20_000_000
        mock_wallet.total_deposited_micros = 50_000_000

        mock_deposit = MagicMock()
        mock_deposit.id = uuid4()
        mock_deposit.amount_micros = amount

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=mock_wallet)
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.add = MagicMock()
        mock_session.commit = AsyncMock()
        mock_session.flush = AsyncMock()

        created_deposit = None

        async def mock_refresh(obj):
            nonlocal created_deposit
            if hasattr(obj, "amount_micros"):
                created_deposit = obj
                obj.id = uuid4()
                obj.created_at = datetime.now(timezone.utc)

        mock_session.refresh = mock_refresh

        mock_redis = AsyncMock()
        mock_redis.client = AsyncMock()
        mock_redis.client.delete = AsyncMock()

        with (
            patch(
                "src.services.wallet.get_session_context"
            ) as mock_ctx,
            patch(
                "src.services.wallet.get_redis_client",
                new_callable=AsyncMock,
                return_value=mock_redis,
            ),
        ):
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=None)

            await service.deposit(
                partner_id=partner_id,
                amount_micros=amount,
                payment_method="stripe",
                auto_complete=True,
            )

            # Verify deposit was created
            assert created_deposit is not None
            assert created_deposit.amount_micros == amount

            # Verify wallet balance was updated
            assert mock_wallet.balance_micros == 30_000_000  # 20M + 10M
            assert mock_wallet.total_deposited_micros == 60_000_000

            # Verify cache was invalidated
            mock_redis.client.delete.assert_called_once()

    async def test_deposit_negative_amount_fails(
        self, service: WalletService
    ) -> None:
        """Negative deposit amount should raise error."""
        partner_id = uuid4()

        with pytest.raises(WalletError) as exc_info:
            await service.deposit(
                partner_id=partner_id,
                amount_micros=-1_000_000,
            )

        assert "positive" in str(exc_info.value).lower()

    async def test_deposit_zero_amount_fails(self, service: WalletService) -> None:
        """Zero deposit amount should raise error."""
        partner_id = uuid4()

        with pytest.raises(WalletError) as exc_info:
            await service.deposit(
                partner_id=partner_id,
                amount_micros=0,
            )

        assert "positive" in str(exc_info.value).lower()


class TestWalletServiceDebit:
    """Test WalletService.debit."""

    @pytest.fixture
    def service(self) -> WalletService:
        return WalletService()

    async def test_debit_success(self, service: WalletService) -> None:
        """Successful debit should decrease balance."""
        partner_id = uuid4()
        amount = 5_000_000  # $5

        mock_wallet = MagicMock()
        mock_wallet.balance_micros = 50_000_000
        mock_wallet.total_spent_micros = 10_000_000

        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value="50000000")
        mock_redis.set = AsyncMock()
        mock_redis.client = AsyncMock()
        mock_redis.client.decrby = AsyncMock(return_value=45_000_000)

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=mock_wallet)
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.commit = AsyncMock()

        with (
            patch(
                "src.services.wallet.get_redis_client",
                new_callable=AsyncMock,
                return_value=mock_redis,
            ),
            patch(
                "src.services.wallet.get_session_context"
            ) as mock_ctx,
        ):
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await service.debit(
                partner_id=partner_id,
                amount_micros=amount,
                reason="job:test-123",
            )

            assert result is True
            mock_redis.client.decrby.assert_called_once()

    async def test_debit_insufficient_balance_fails(
        self, service: WalletService
    ) -> None:
        """Debit exceeding balance should restore and raise error."""
        partner_id = uuid4()
        amount = 100_000_000  # $100

        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value="50000000")
        mock_redis.set = AsyncMock()
        mock_redis.client = AsyncMock()
        mock_redis.client.decrby = AsyncMock(return_value=-50_000_000)  # Goes negative
        mock_redis.client.incrby = AsyncMock()

        with patch(
            "src.services.wallet.get_redis_client",
            new_callable=AsyncMock,
            return_value=mock_redis,
        ):
            with pytest.raises(WalletError) as exc_info:
                await service.debit(
                    partner_id=partner_id,
                    amount_micros=amount,
                    reason="job:test-123",
                )

            assert "insufficient" in str(exc_info.value).lower()
            # Verify balance was restored
            mock_redis.client.incrby.assert_called_once_with(
                WALLET_BALANCE_KEY.format(partner_id=partner_id), amount
            )

    async def test_debit_zero_amount_succeeds(self, service: WalletService) -> None:
        """Zero amount debit should return True without action."""
        partner_id = uuid4()

        result = await service.debit(
            partner_id=partner_id,
            amount_micros=0,
            reason="free-tier",
        )

        assert result is True


class TestWalletServiceLowBalance:
    """Test WalletService low balance checking."""

    @pytest.fixture
    def service(self) -> WalletService:
        return WalletService()

    async def test_check_low_balance_below_threshold(
        self, service: WalletService
    ) -> None:
        """Should return True when below threshold."""
        partner_id = uuid4()

        mock_wallet = MagicMock()
        mock_wallet.balance_micros = 5_000_000  # $5
        mock_wallet.low_balance_threshold_micros = 10_000_000  # $10 threshold
        mock_wallet.last_low_balance_alert_at = None

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=mock_wallet)
        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch(
            "src.services.wallet.get_session_context"
        ) as mock_ctx:
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await service.check_low_balance(partner_id)

            assert result is True

    async def test_check_low_balance_above_threshold(
        self, service: WalletService
    ) -> None:
        """Should return False when above threshold."""
        partner_id = uuid4()

        mock_wallet = MagicMock()
        mock_wallet.balance_micros = 15_000_000  # $15
        mock_wallet.low_balance_threshold_micros = 10_000_000  # $10 threshold

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=mock_wallet)
        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch(
            "src.services.wallet.get_session_context"
        ) as mock_ctx:
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await service.check_low_balance(partner_id)

            assert result is False

    async def test_check_low_balance_no_threshold_set(
        self, service: WalletService
    ) -> None:
        """Should return False when no threshold is configured."""
        partner_id = uuid4()

        mock_wallet = MagicMock()
        mock_wallet.balance_micros = 1_000_000  # Low balance
        mock_wallet.low_balance_threshold_micros = None  # No threshold

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=mock_wallet)
        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch(
            "src.services.wallet.get_session_context"
        ) as mock_ctx:
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await service.check_low_balance(partner_id)

            assert result is False

    async def test_check_low_balance_recently_alerted(
        self, service: WalletService
    ) -> None:
        """Should return False if alert was sent recently (within 24h)."""
        partner_id = uuid4()

        mock_wallet = MagicMock()
        mock_wallet.balance_micros = 5_000_000
        mock_wallet.low_balance_threshold_micros = 10_000_000
        mock_wallet.last_low_balance_alert_at = datetime.now(timezone.utc)  # Just now

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=mock_wallet)
        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch(
            "src.services.wallet.get_session_context"
        ) as mock_ctx:
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await service.check_low_balance(partner_id)

            assert result is False


class TestWalletServiceGetOrCreateWallet:
    """Test WalletService.get_or_create_wallet."""

    @pytest.fixture
    def service(self) -> WalletService:
        return WalletService()

    async def test_returns_existing_wallet(self, service: WalletService) -> None:
        """Should return existing wallet if found."""
        partner_id = uuid4()

        mock_wallet = MagicMock()
        mock_wallet.id = uuid4()
        mock_wallet.partner_id = partner_id
        mock_wallet.balance_micros = 100_000_000

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=mock_wallet)
        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch(
            "src.services.wallet.get_session_context"
        ) as mock_ctx:
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await service.get_or_create_wallet(partner_id)

            assert result == mock_wallet
            # Should not call add for new wallet
            mock_session.add.assert_not_called()

    async def test_creates_wallet_for_valid_partner(
        self, service: WalletService
    ) -> None:
        """Should create new wallet for partner without one."""
        partner_id = uuid4()

        mock_partner = MagicMock()
        mock_partner.id = partner_id

        mock_session = AsyncMock()
        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return None  # No existing wallet
            else:
                return mock_partner  # Partner exists

        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(side_effect=side_effect)
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.add = MagicMock()
        mock_session.commit = AsyncMock()

        created_wallet = None

        async def mock_refresh(obj):
            nonlocal created_wallet
            created_wallet = obj
            obj.id = uuid4()

        mock_session.refresh = mock_refresh

        with patch(
            "src.services.wallet.get_session_context"
        ) as mock_ctx:
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await service.get_or_create_wallet(partner_id)

            # Verify wallet was created with correct initial values
            assert created_wallet.balance_micros == 0
            assert created_wallet.total_deposited_micros == 0
            assert created_wallet.total_spent_micros == 0

    async def test_raises_error_for_invalid_partner(
        self, service: WalletService
    ) -> None:
        """Should raise error if partner does not exist."""
        partner_id = uuid4()

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=None)
        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch(
            "src.services.wallet.get_session_context"
        ) as mock_ctx:
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=None)

            with pytest.raises(WalletError) as exc_info:
                await service.get_or_create_wallet(partner_id)

            assert "not found" in str(exc_info.value).lower()
