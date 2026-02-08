"""Unit tests for the FeatureService.

Tests cover system feature management and partner feature configuration.
"""

import sys
from datetime import datetime, timezone
from decimal import Decimal
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

from src.services.feature import (  # noqa: E402

    FeatureService,
    FeatureConfig,
    FeatureError,
)


class TestFeatureConfig:
    """Test FeatureConfig dataclass."""

    def test_creates_with_all_fields(self) -> None:
        """Should create FeatureConfig with all fields."""
        feature_id = uuid4()
        config = FeatureConfig(
            feature_id=feature_id,
            slug="translation",
            name="Translation",
            provider="openai",
            model_id="gpt-4o-mini",
            weight_multiplier=1.5,
            is_enabled=True,
            requires_approval=False,
        )

        assert config.feature_id == feature_id
        assert config.slug == "translation"
        assert config.provider == "openai"
        assert config.weight_multiplier == 1.5

    def test_defaults_to_enabled(self) -> None:
        """Should default is_enabled to True."""
        config = FeatureConfig(
            feature_id=uuid4(),
            slug="test",
            name="Test",
            provider="openai",
            model_id="gpt-4o-mini",
            weight_multiplier=1.0,
            is_enabled=True,
            requires_approval=False,
        )

        assert config.is_enabled is True


class TestFeatureServiceCreateSystemFeature:
    """Test FeatureService.create_system_feature."""

    @pytest.fixture
    def service(self) -> FeatureService:
        return FeatureService()

    async def test_creates_feature(self, service: FeatureService) -> None:
        """Should create new system feature."""
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=None)
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.add = MagicMock()
        mock_session.commit = AsyncMock()

        created_feature = None

        async def mock_refresh(obj):
            nonlocal created_feature
            created_feature = obj
            obj.id = uuid4()
            obj.created_at = datetime.now(timezone.utc)
            obj.updated_at = datetime.now(timezone.utc)

        mock_session.refresh = mock_refresh

        with patch(
            "src.services.feature.get_session_context"
        ) as mock_ctx:
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=None)

            result = await service.create_system_feature(
                slug="code_review",
                name="Code Review",
                default_provider="openai",
                default_model_id="gpt-4o",
                weight_multiplier=2.0,
            )

            assert created_feature is not None
            assert created_feature.slug == "code_review"
            assert created_feature.default_provider == "openai"
            assert created_feature.weight_multiplier == 2.0

    async def test_duplicate_slug_fails(self, service: FeatureService) -> None:
        """Should raise error if slug already exists."""
        mock_existing = MagicMock()
        mock_existing.id = uuid4()

        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=mock_existing)
        mock_session.execute = AsyncMock(return_value=mock_result)

        with patch(
            "src.services.feature.get_session_context"
        ) as mock_ctx:
            mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_ctx.return_value.__aexit__ = AsyncMock(return_value=None)

            with pytest.raises(FeatureError) as exc_info:
                await service.create_system_feature(
                    slug="translation",
                    name="Translation",
                    default_provider="openai",
                    default_model_id="gpt-4o-mini",
                )

            assert "already exists" in str(exc_info.value).lower()


class TestFeatureError:
    """Test FeatureError exception."""

    def test_creates_with_message(self) -> None:
        """Should create error with message."""
        error = FeatureError("Test error message")

        assert "test error message" in str(error).lower()

    def test_creates_with_details(self) -> None:
        """Should create error with details."""
        error = FeatureError(
            "Test error",
            details={"feature_slug": "translation"},
        )

        assert error.details == {"feature_slug": "translation"}
