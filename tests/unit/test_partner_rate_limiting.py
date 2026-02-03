"""Unit tests for partner-level rate limiting in the waterfall.

Tests that partner RPM is checked first (Step 0), tenant RPM
inherits from partner when not set, and legacy tenants without
a partner skip the partner-level check.
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from libs.common.exceptions import RateLimitError

# api-gateway uses a hyphen, which is not a valid Python package name.
_SERVICE_ROOT = str(Path(__file__).resolve().parents[2] / "services" / "api-gateway")
if _SERVICE_ROOT not in sys.path:
    sys.path.insert(0, _SERVICE_ROOT)

from src.middleware.rate_limit import RateLimitMiddleware  # noqa: E402


class TestPartnerRateLimitWaterfall:
    """Test partner-level rate limiting in the waterfall."""

    @pytest.fixture
    def middleware(self) -> RateLimitMiddleware:
        app = MagicMock()
        return RateLimitMiddleware(app)

    def _make_request(
        self,
        tenant_id=None,
        partner_id=None,
        tenant_rpm=None,
        tenant_tpm=None,
        partner_rpm=None,
        partner_tpm=None,
        user_id=None,
        path="/api/v1/chat/completions",
    ) -> MagicMock:
        """Create a mock request with partner and tenant state."""
        request = MagicMock()
        request.url.path = path
        request.method = "POST"

        request.state.tenant_id = tenant_id or uuid4()
        request.state.partner_id = partner_id
        request.state.user_id = user_id
        request.state.user = None

        tenant = MagicMock()
        tenant.rate_limit_rpm = tenant_rpm
        tenant.rate_limit_tpm = tenant_tpm
        request.state.tenant = tenant

        if partner_id:
            partner = MagicMock()
            partner.rate_limit_rpm = partner_rpm
            partner.rate_limit_tpm = partner_tpm
            request.state.partner = partner
        else:
            request.state.partner = None

        return request

    def _make_redis_mock(
        self,
        partner_rpm_count: int = 0,
        tenant_rpm_count: int = 0,
    ) -> AsyncMock:
        """Create a mock Redis returning configurable RPM counts.

        Pipeline execute order:
        1. Partner RPM check (if partner_id exists and has limits)
        2. Tenant RPM check
        3+. User/TPM checks
        """
        redis = AsyncMock()
        pipe = AsyncMock()
        call_count = 0

        async def pipeline_execute():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return [0, partner_rpm_count]
            elif call_count == 2:
                return [0, tenant_rpm_count]
            else:
                return [0, []]  # TPM check: zrange returns empty list

        pipe.execute = pipeline_execute
        pipe.zremrangebyscore = MagicMock()
        pipe.zcard = MagicMock()
        pipe.zrange = MagicMock()

        redis.pipeline = MagicMock(return_value=pipe)
        redis.client = AsyncMock()
        redis.client.zadd = AsyncMock()
        redis.client.zrange = AsyncMock(return_value=[])
        redis.expire = AsyncMock()

        return redis

    async def test_partner_under_limit_passes(
        self, middleware: RateLimitMiddleware
    ) -> None:
        """Request under partner RPM limit should pass to tenant check."""
        partner_id = uuid4()
        request = self._make_request(
            partner_id=partner_id,
            partner_rpm=200,
            tenant_rpm=100,
        )
        redis = self._make_redis_mock(partner_rpm_count=50, tenant_rpm_count=5)

        with patch(
            "src.middleware.rate_limit.get_redis_client",
            new_callable=AsyncMock,
            return_value=redis,
        ), patch(
            "src.middleware.rate_limit.get_settings",
        ) as mock_settings:
            mock_settings.return_value.rate_limit_rpm = 60
            mock_settings.return_value.rate_limit_tpm = 100000

            # Should not raise
            await middleware._check_rate_limit(
                request, request.state.tenant_id, partner_id
            )

    async def test_partner_over_limit_raises(
        self, middleware: RateLimitMiddleware
    ) -> None:
        """Request exceeding partner RPM should raise RateLimitError."""
        partner_id = uuid4()
        request = self._make_request(
            partner_id=partner_id,
            partner_rpm=100,
            tenant_rpm=200,  # Tenant has higher limit, but partner blocks
        )
        redis = self._make_redis_mock(partner_rpm_count=100)

        with patch(
            "src.middleware.rate_limit.get_redis_client",
            new_callable=AsyncMock,
            return_value=redis,
        ), patch(
            "src.middleware.rate_limit.get_settings",
        ) as mock_settings:
            mock_settings.return_value.rate_limit_rpm = 60
            mock_settings.return_value.rate_limit_tpm = 100000

            with pytest.raises(RateLimitError) as exc_info:
                await middleware._check_rate_limit(
                    request, request.state.tenant_id, partner_id
                )

            assert "partner" in exc_info.value.message.lower()
            assert exc_info.value.details["limit_scope"] == "partner"

    async def test_tenant_inherits_partner_rpm_when_none(
        self, middleware: RateLimitMiddleware
    ) -> None:
        """Tenant without explicit RPM should inherit partner's RPM."""
        partner_id = uuid4()
        request = self._make_request(
            partner_id=partner_id,
            partner_rpm=50,
            tenant_rpm=None,  # No tenant-specific limit → falls back to partner's 50
        )
        # Partner at 5 (under 50), tenant at 50 (at inherited limit of 50)
        redis = self._make_redis_mock(partner_rpm_count=5, tenant_rpm_count=50)

        with patch(
            "src.middleware.rate_limit.get_redis_client",
            new_callable=AsyncMock,
            return_value=redis,
        ), patch(
            "src.middleware.rate_limit.get_settings",
        ) as mock_settings:
            mock_settings.return_value.rate_limit_rpm = 60
            mock_settings.return_value.rate_limit_tpm = 100000

            with pytest.raises(RateLimitError) as exc_info:
                await middleware._check_rate_limit(
                    request, request.state.tenant_id, partner_id
                )

            assert exc_info.value.details["limit_scope"] == "tenant"
            assert exc_info.value.details["limit"] == 50  # Inherited from partner

    async def test_legacy_tenant_skips_partner_check(
        self, middleware: RateLimitMiddleware
    ) -> None:
        """Tenant without partner_id should skip partner-level check entirely."""
        request = self._make_request(
            partner_id=None,
            tenant_rpm=100,
            path="/api/v1/jobs",  # Non-chat path to avoid TPM check
        )
        # No partner RPM pipeline call; first pipeline call is tenant
        redis = self._make_redis_mock(tenant_rpm_count=5)

        with patch(
            "src.middleware.rate_limit.get_redis_client",
            new_callable=AsyncMock,
            return_value=redis,
        ), patch(
            "src.middleware.rate_limit.get_settings",
        ) as mock_settings:
            mock_settings.return_value.rate_limit_rpm = 60
            mock_settings.return_value.rate_limit_tpm = 100000

            # Should pass without partner check
            await middleware._check_rate_limit(
                request, request.state.tenant_id, None
            )

    async def test_partner_blocked_skips_tenant_check(
        self, middleware: RateLimitMiddleware
    ) -> None:
        """If partner limit is hit, tenant check should not run."""
        partner_id = uuid4()
        request = self._make_request(
            partner_id=partner_id,
            partner_rpm=10,
            tenant_rpm=1000,  # Very generous tenant limit
        )
        redis = self._make_redis_mock(partner_rpm_count=10, tenant_rpm_count=0)

        with patch(
            "src.middleware.rate_limit.get_redis_client",
            new_callable=AsyncMock,
            return_value=redis,
        ), patch(
            "src.middleware.rate_limit.get_settings",
        ) as mock_settings:
            mock_settings.return_value.rate_limit_rpm = 60
            mock_settings.return_value.rate_limit_tpm = 100000

            with pytest.raises(RateLimitError) as exc_info:
                await middleware._check_rate_limit(
                    request, request.state.tenant_id, partner_id
                )

            # Should be partner-level rejection, not tenant
            assert exc_info.value.details["limit_scope"] == "partner"

    async def test_partner_no_rpm_limit_skips_partner_check(
        self, middleware: RateLimitMiddleware
    ) -> None:
        """Partner without rate_limit_rpm should skip partner RPM check."""
        partner_id = uuid4()
        request = self._make_request(
            partner_id=partner_id,
            partner_rpm=None,  # No partner RPM limit
            tenant_rpm=100,
            path="/api/v1/jobs",  # Non-chat path to avoid TPM check
        )
        # First pipeline call goes to tenant (partner check skipped)
        redis = self._make_redis_mock(tenant_rpm_count=5)

        with patch(
            "src.middleware.rate_limit.get_redis_client",
            new_callable=AsyncMock,
            return_value=redis,
        ), patch(
            "src.middleware.rate_limit.get_settings",
        ) as mock_settings:
            mock_settings.return_value.rate_limit_rpm = 60
            mock_settings.return_value.rate_limit_tpm = 100000

            # Should pass — partner has no RPM limit, tenant is under
            await middleware._check_rate_limit(
                request, request.state.tenant_id, partner_id
            )
