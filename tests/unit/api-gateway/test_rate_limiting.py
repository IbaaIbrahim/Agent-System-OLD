"""Unit tests for waterfall rate limiting.

Tests the rate limiting middleware logic including tenant-level
and user-level waterfall enforcement.
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from libs.common.exceptions import RateLimitError



# Fix paths for imports to ensure both project root (for libs) and service root (for src) are in sys.path
_TEST_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _TEST_DIR.parent.parent.parent
_SERVICE_ROOT = _PROJECT_ROOT / "services" / "api-gateway"

if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


if str(_SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(_SERVICE_ROOT))

from src.middleware.rate_limit import RateLimitMiddleware  # noqa: E402



class TestRateLimitWaterfall:
    """Test the waterfall rate limiting strategy."""

    @pytest.fixture
    def middleware(self) -> RateLimitMiddleware:
        app = MagicMock()
        return RateLimitMiddleware(app)

    def _make_request(
        self,
        tenant_id=None,
        user_id=None,
        tenant_rpm=None,
        tenant_tpm=None,
        user_custom_rpm=None,
        partner_id=None,
        partner_rpm=None,
        partner_tpm=None,
        path="/api/v1/chat/completions",
    ) -> MagicMock:
        """Create a mock request with state attributes."""
        request = MagicMock()
        request.url.path = path
        request.method = "POST"

        # Set state attributes
        request.state.tenant_id = tenant_id or uuid4()
        request.state.user_id = user_id
        request.state.partner_id = partner_id

        # Tenant object with rate limits
        tenant = MagicMock()
        tenant.rate_limit_rpm = tenant_rpm
        tenant.rate_limit_tpm = tenant_tpm
        request.state.tenant = tenant

        # Partner object with rate limits
        if partner_id:
            partner = MagicMock()
            partner.rate_limit_rpm = partner_rpm
            partner.rate_limit_tpm = partner_tpm
            request.state.partner = partner
        else:
            request.state.partner = None

        # User object with custom limits
        if user_id:
            user = MagicMock()
            user.custom_rpm_limit = user_custom_rpm
            user.custom_tpm_limit = None
            request.state.user = user
        else:
            request.state.user = None

        return request

    def _make_redis_mock(
        self,
        tenant_rpm_count: int = 0,
        user_rpm_count: int = 0,
    ) -> AsyncMock:
        """Create a mock Redis client that returns configurable RPM counts."""
        redis = AsyncMock()

        # Pipeline mock — returns [removed_count, current_count]
        pipe = AsyncMock()
        # We'll configure the results per call
        call_count = 0

        async def pipeline_execute():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return [0, tenant_rpm_count]  # Tenant check
            elif call_count == 2:
                return [0, user_rpm_count]  # User check
            else:
                return [0, 0]  # TPM check

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

    async def test_tenant_under_limit_passes(self, middleware: RateLimitMiddleware) -> None:
        """Requests under tenant RPM limit should pass."""
        request = self._make_request(tenant_rpm=100)
        redis = self._make_redis_mock(tenant_rpm_count=50)

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
            await middleware._check_rate_limit(request, request.state.tenant_id, request.state.partner_id)

    async def test_tenant_over_limit_raises(self, middleware: RateLimitMiddleware) -> None:
        """Requests exceeding tenant RPM should raise RateLimitError."""
        request = self._make_request(tenant_rpm=100)
        redis = self._make_redis_mock(tenant_rpm_count=100)  # At limit

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
                await middleware._check_rate_limit(request, request.state.tenant_id, request.state.partner_id)

            assert "tenant" in exc_info.value.message.lower()
            assert exc_info.value.details["limit_scope"] == "tenant"

    async def test_user_with_custom_limit_enforced(
        self, middleware: RateLimitMiddleware
    ) -> None:
        """User-specific RPM limit should be enforced when set."""
        user_id = uuid4()
        request = self._make_request(
            tenant_rpm=100,
            user_id=user_id,
            user_custom_rpm=10,  # Custom limit: 10 RPM
        )
        # Tenant is at 5 (under 100), user is at 10 (at custom limit of 10)
        redis = self._make_redis_mock(tenant_rpm_count=5, user_rpm_count=10)

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
                await middleware._check_rate_limit(request, request.state.tenant_id, request.state.partner_id)

            assert "user" in exc_info.value.message.lower()
            assert exc_info.value.details["limit_scope"] == "user"

    async def test_user_inherits_tenant_limit_when_custom_is_none(
        self, middleware: RateLimitMiddleware
    ) -> None:
        """User with no custom limit should inherit tenant's RPM limit."""
        user_id = uuid4()
        request = self._make_request(
            tenant_rpm=50,
            user_id=user_id,
            user_custom_rpm=None,  # No custom limit → inherits 50
        )
        # Tenant at 5 (OK), user at 50 (at inherited limit of 50)
        redis = self._make_redis_mock(tenant_rpm_count=5, user_rpm_count=50)

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
                await middleware._check_rate_limit(request, request.state.tenant_id, request.state.partner_id)

            assert exc_info.value.details["limit_scope"] == "user"
            assert exc_info.value.details["limit"] == 50

    async def test_tenant_blocked_skips_user_check(
        self, middleware: RateLimitMiddleware
    ) -> None:
        """If tenant limit is exceeded, user check should not run."""
        user_id = uuid4()
        request = self._make_request(
            tenant_rpm=10,
            user_id=user_id,
            user_custom_rpm=200,  # Very generous user limit
        )
        # Tenant at 10 (at limit) — should fail at tenant level
        redis = self._make_redis_mock(tenant_rpm_count=10, user_rpm_count=0)

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
                await middleware._check_rate_limit(request, request.state.tenant_id, request.state.partner_id)

            # Should be tenant-level rejection
            assert exc_info.value.details["limit_scope"] == "tenant"

    async def test_no_user_id_skips_user_check(
        self, middleware: RateLimitMiddleware
    ) -> None:
        """API key auth (no user_id) should skip user-level check."""
        request = self._make_request(
            tenant_rpm=100,
            user_id=None,  # No user ID
        )
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

            # Should pass — only tenant check, and tenant is under limit
            await middleware._check_rate_limit(request, request.state.tenant_id, request.state.partner_id)
