"""Unit tests for the SubscriptionService.

Tests cover plan management, subscription lifecycle, and credit allocation.
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

from src.services.subscription import (  # noqa: E402
    SubscriptionService,
    SubscriptionError,
)


class TestSubscriptionServiceCreatePlan:
    """Test SubscriptionService plan creation."""

    @pytest.fixture
    def service(self) -> SubscriptionService:
        return SubscriptionService()

    async def test_create_plan_duplicate_slug_fails(
        self, service: SubscriptionService
    ) -> None:
        """Should raise error if slug already exists for partner."""
        partner_id = uuid4()

        existing_plan = MagicMock()
        existing_plan.id = uuid4()

        mock_partner = MagicMock()
        mock_partner.id = partner_id

        mock_session = AsyncMock()
        call_count = 0

        def mock_scalar():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return mock_partner  # Partner exists
            return existing_plan  # Plan with slug exists

        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(side_effect=mock_scalar)
        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch(
            "src.services.subscription.get_session_context"
        ) as mock_ctx:
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=None)

            with pytest.raises(SubscriptionError) as exc_info:
                await service.create_plan(
                    partner_id=partner_id,
                    name="Plus",
                    slug="plus",
                    monthly_credits_micros=10_000_000,
                )

            assert "already exists" in str(exc_info.value)


class TestSubscriptionServiceGetSubscription:
    """Test SubscriptionService subscription retrieval."""

    @pytest.fixture
    def service(self) -> SubscriptionService:
        return SubscriptionService()

    async def test_get_subscription_returns_none_when_not_found(
        self, service: SubscriptionService
    ) -> None:
        """Should return None when subscription doesn't exist."""
        tenant_id = uuid4()

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=None)
        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch(
            "src.services.subscription.get_session_context"
        ) as mock_ctx:
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await service.get_subscription(tenant_id)

            assert result is None

    async def test_get_subscription_returns_existing(
        self, service: SubscriptionService
    ) -> None:
        """Should return subscription when it exists."""
        tenant_id = uuid4()

        mock_sub = MagicMock()
        mock_sub.id = uuid4()
        mock_sub.tenant_id = tenant_id
        mock_sub.plan_credits_remaining_micros = 5_000_000

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=mock_sub)
        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch(
            "src.services.subscription.get_session_context"
        ) as mock_ctx:
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await service.get_subscription(tenant_id)

            assert result is not None
            assert result.tenant_id == tenant_id


class TestSubscriptionServiceListPlans:
    """Test SubscriptionService plan listing."""

    @pytest.fixture
    def service(self) -> SubscriptionService:
        return SubscriptionService()

    async def test_list_plans_returns_active_plans(
        self, service: SubscriptionService
    ) -> None:
        """Should return only active plans for partner."""
        partner_id = uuid4()

        plan1 = MagicMock()
        plan1.id = uuid4()
        plan1.slug = "free"
        plan2 = MagicMock()
        plan2.id = uuid4()
        plan2.slug = "plus"

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars = MagicMock()
        mock_result.scalars.return_value.all = MagicMock(return_value=[plan1, plan2])
        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch(
            "src.services.subscription.get_session_context"
        ) as mock_ctx:
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=None)

            plans = await service.list_plans(partner_id)

            assert len(plans) == 2
            assert plans[0].slug == "free"
            assert plans[1].slug == "plus"

    async def test_list_plans_empty_for_new_partner(
        self, service: SubscriptionService
    ) -> None:
        """Should return empty list for partner with no plans."""
        partner_id = uuid4()

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars = MagicMock()
        mock_result.scalars.return_value.all = MagicMock(return_value=[])
        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch(
            "src.services.subscription.get_session_context"
        ) as mock_ctx:
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=None)

            plans = await service.list_plans(partner_id)

            assert len(plans) == 0


class TestSubscriptionServiceGetPlan:
    """Test SubscriptionService single plan retrieval."""

    @pytest.fixture
    def service(self) -> SubscriptionService:
        return SubscriptionService()

    async def test_get_plan_returns_plan_when_found(
        self, service: SubscriptionService
    ) -> None:
        """Should return plan when it exists."""
        plan_id = uuid4()

        mock_plan = MagicMock()
        mock_plan.id = plan_id
        mock_plan.slug = "plus"

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=mock_plan)
        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch(
            "src.services.subscription.get_session_context"
        ) as mock_ctx:
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await service.get_plan(plan_id)

            assert result is not None
            assert result.slug == "plus"

    async def test_get_plan_returns_none_when_not_found(
        self, service: SubscriptionService
    ) -> None:
        """Should return None when plan doesn't exist."""
        plan_id = uuid4()

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=None)
        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch(
            "src.services.subscription.get_session_context"
        ) as mock_ctx:
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await service.get_plan(plan_id)

            assert result is None


class TestSubscriptionServiceArchivePlan:
    """Test SubscriptionService plan archival."""

    @pytest.fixture
    def service(self) -> SubscriptionService:
        return SubscriptionService()

    async def test_archive_plan_sets_status(
        self, service: SubscriptionService
    ) -> None:
        """Should set plan status to archived."""
        plan_id = uuid4()

        mock_plan = MagicMock()
        mock_plan.id = plan_id

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=mock_plan)
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.commit = AsyncMock()
        mock_session.refresh = AsyncMock()

        mock_redis = AsyncMock()
        mock_redis.client = AsyncMock()
        mock_redis.client.delete = AsyncMock()

        with patch(
            "src.services.subscription.get_session_context"
        ) as mock_ctx:
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=None)

            with patch(
                "src.services.subscription.get_redis_client",
                return_value=mock_redis
            ):
                with patch("src.services.subscription.PartnerPlanStatus") as MockStatus:
                    MockStatus.ARCHIVED = "archived"

                    result = await service.archive_plan(plan_id)

                    assert mock_plan.status == "archived"


class TestSubscriptionServiceListTopUps:
    """Test SubscriptionService top-up listing."""

    @pytest.fixture
    def service(self) -> SubscriptionService:
        return SubscriptionService()

    async def test_list_topups_returns_tenant_topups(
        self, service: SubscriptionService
    ) -> None:
        """Should return top-ups for tenant."""
        tenant_id = uuid4()

        topup1 = MagicMock()
        topup1.id = uuid4()
        topup1.remaining_micros = 2_000_000
        topup2 = MagicMock()
        topup2.id = uuid4()
        topup2.remaining_micros = 1_000_000

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars = MagicMock()
        mock_result.scalars.return_value.all = MagicMock(
            return_value=[topup1, topup2]
        )
        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch(
            "src.services.subscription.get_session_context"
        ) as mock_ctx:
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=None)

            topups = await service.list_top_ups(tenant_id)

            assert len(topups) == 2
            assert topups[0].remaining_micros == 2_000_000

    async def test_list_topups_empty_when_none(
        self, service: SubscriptionService
    ) -> None:
        """Should return empty list when no top-ups exist."""
        tenant_id = uuid4()

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars = MagicMock()
        mock_result.scalars.return_value.all = MagicMock(return_value=[])
        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch(
            "src.services.subscription.get_session_context"
        ) as mock_ctx:
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=None)

            topups = await service.list_top_ups(tenant_id)

            assert len(topups) == 0
