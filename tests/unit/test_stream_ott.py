"""Unit tests for stream one-time token (OTT) creation and verification.

Tests cover OTT lifecycle: creation, valid verification, expiry,
wrong purpose, signature tampering, and jti uniqueness.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import uuid4

import jwt
import pytest

from libs.common.auth import (
    StreamOTTPayload,
    create_stream_ott,
    verify_stream_ott,
)
from libs.common.exceptions import AuthenticationError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FAKE_INTERNAL_SECRET = "test_internal_secret_at_least_32_chars_long!!"
FAKE_OTT_TTL = 60


def _mock_settings():
    """Return a mock settings object with the fields OTT functions need."""
    from unittest.mock import MagicMock

    s = MagicMock()
    s.internal_jwt_secret = FAKE_INTERNAL_SECRET
    s.ott_ttl_seconds = FAKE_OTT_TTL
    return s


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCreateStreamOTT:
    """Tests for create_stream_ott()."""

    @patch("libs.common.auth.get_settings", _mock_settings)
    def test_create_ott_returns_jwt(self) -> None:
        """Should return a decodable JWT with correct claims."""
        job_id = uuid4()
        tenant_id = uuid4()
        user_id = uuid4()
        partner_id = uuid4()

        token = create_stream_ott(
            job_id=job_id,
            tenant_id=tenant_id,
            user_id=user_id,
            partner_id=partner_id,
        )

        payload = jwt.decode(token, FAKE_INTERNAL_SECRET, algorithms=["HS256"])

        assert payload["purpose"] == "stream_ott"
        assert payload["job_id"] == str(job_id)
        assert payload["tenant_id"] == str(tenant_id)
        assert payload["user_id"] == str(user_id)
        assert payload["partner_id"] == str(partner_id)
        assert "jti" in payload
        assert "exp" in payload
        assert "iat" in payload

    @patch("libs.common.auth.get_settings", _mock_settings)
    def test_create_ott_optional_fields_none(self) -> None:
        """Should set user_id and partner_id to None when not provided."""
        token = create_stream_ott(job_id=uuid4(), tenant_id=uuid4())

        payload = jwt.decode(token, FAKE_INTERNAL_SECRET, algorithms=["HS256"])

        assert payload["user_id"] is None
        assert payload["partner_id"] is None

    @patch("libs.common.auth.get_settings", _mock_settings)
    def test_ott_jti_uniqueness(self) -> None:
        """Two OTTs should have different jti values."""
        job_id = uuid4()
        tenant_id = uuid4()

        token_a = create_stream_ott(job_id=job_id, tenant_id=tenant_id)
        token_b = create_stream_ott(job_id=job_id, tenant_id=tenant_id)

        payload_a = jwt.decode(token_a, FAKE_INTERNAL_SECRET, algorithms=["HS256"])
        payload_b = jwt.decode(token_b, FAKE_INTERNAL_SECRET, algorithms=["HS256"])

        assert payload_a["jti"] != payload_b["jti"]

    @patch("libs.common.auth.get_settings", _mock_settings)
    def test_ott_expiry_within_ttl(self) -> None:
        """Expiry should be approximately ott_ttl_seconds from now."""
        before = datetime.now(UTC)
        token = create_stream_ott(job_id=uuid4(), tenant_id=uuid4())
        after = datetime.now(UTC)

        payload = jwt.decode(token, FAKE_INTERNAL_SECRET, algorithms=["HS256"])
        exp = datetime.fromtimestamp(payload["exp"], tz=UTC)
        iat = datetime.fromtimestamp(payload["iat"], tz=UTC)

        assert exp - iat == timedelta(seconds=FAKE_OTT_TTL)
        # iat is truncated to int(timestamp), so allow 1s tolerance
        assert exp >= before + timedelta(seconds=FAKE_OTT_TTL - 1)
        assert exp <= after + timedelta(seconds=FAKE_OTT_TTL + 1)


class TestVerifyStreamOTT:
    """Tests for verify_stream_ott()."""

    @patch("libs.common.auth.get_settings", _mock_settings)
    def test_verify_ott_valid(self) -> None:
        """Roundtrip create → verify should return correct payload."""
        job_id = uuid4()
        tenant_id = uuid4()
        user_id = uuid4()

        token = create_stream_ott(
            job_id=job_id, tenant_id=tenant_id, user_id=user_id
        )
        result = verify_stream_ott(token)

        assert isinstance(result, StreamOTTPayload)
        assert result.purpose == "stream_ott"
        assert result.job_id == str(job_id)
        assert result.tenant_id == str(tenant_id)
        assert result.user_id == str(user_id)
        assert result.partner_id is None

    @patch("libs.common.auth.get_settings", _mock_settings)
    def test_verify_ott_expired(self) -> None:
        """Should raise AuthenticationError for expired tokens."""
        now = datetime.now(UTC)
        payload = {
            "purpose": "stream_ott",
            "job_id": str(uuid4()),
            "tenant_id": str(uuid4()),
            "user_id": None,
            "partner_id": None,
            "jti": "test-jti",
            "iat": int((now - timedelta(minutes=5)).timestamp()),
            "exp": int((now - timedelta(minutes=1)).timestamp()),
        }
        token = jwt.encode(payload, FAKE_INTERNAL_SECRET, algorithm="HS256")

        with pytest.raises(AuthenticationError, match="expired"):
            verify_stream_ott(token)

    @patch("libs.common.auth.get_settings", _mock_settings)
    def test_verify_ott_wrong_purpose(self) -> None:
        """Should reject tokens with wrong purpose claim."""
        now = datetime.now(UTC)
        payload = {
            "purpose": "internal_transaction",
            "job_id": str(uuid4()),
            "tenant_id": str(uuid4()),
            "user_id": None,
            "partner_id": None,
            "jti": "test-jti",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=5)).timestamp()),
        }
        token = jwt.encode(payload, FAKE_INTERNAL_SECRET, algorithm="HS256")

        with pytest.raises(AuthenticationError, match="purpose"):
            verify_stream_ott(token)

    @patch("libs.common.auth.get_settings", _mock_settings)
    def test_verify_ott_tampered(self) -> None:
        """Should reject tokens signed with a different secret."""
        now = datetime.now(UTC)
        payload = {
            "purpose": "stream_ott",
            "job_id": str(uuid4()),
            "tenant_id": str(uuid4()),
            "user_id": None,
            "partner_id": None,
            "jti": "test-jti",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=5)).timestamp()),
        }
        token = jwt.encode(payload, "wrong_secret_key_totally_different", algorithm="HS256")

        with pytest.raises(AuthenticationError, match="Invalid"):
            verify_stream_ott(token)
