"""Unit tests for logging utilities."""

import logging
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from libs.common.logging import add_service_info, add_timestamp


def test_add_timestamp() -> None:
    """Test that add_timestamp adds a valid ISO 8601 timestamp."""
    event_dict = {"event": "test_event"}
    logger = MagicMock(spec=logging.Logger)

    result = add_timestamp(logger, "info", event_dict)

    assert "timestamp" in result
    assert result["event"] == "test_event"

    # Verify it's a valid ISO format string
    try:
        datetime.fromisoformat(result["timestamp"])
    except ValueError:
        pytest.fail("timestamp is not in valid ISO format")


def test_add_service_info() -> None:
    """Test that add_service_info processor adds the correct service name."""
    service_name = "test-service"
    processor = add_service_info(service_name)

    event_dict = {"event": "test_event"}
    logger = MagicMock(spec=logging.Logger)

    result = processor(logger, "info", event_dict)

    assert result["service"] == service_name
    assert result["event"] == "test_event"


def test_add_service_info_updates_existing() -> None:
    """Test that add_service_info overwrites existing service info if present."""
    service_name = "new-service"
    processor = add_service_info(service_name)

    event_dict = {"event": "test_event", "service": "old-service"}
    logger = MagicMock(spec=logging.Logger)

    result = processor(logger, "info", event_dict)

    assert result["service"] == service_name
    assert result["event"] == "test_event"
