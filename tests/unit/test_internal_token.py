"""Unit tests for internal transaction token creation and verification."""

import time
from uuid import uuid4

import jwt
import pytest

from libs.common.auth import (
    create_internal_transaction_token,
    verify_internal_transaction_token,
)
from libs.common.config import get_settings
from libs.common.exceptions import AuthenticationError


class TestCreateInternalTransactionToken:
    """Test internal transaction token creation."""

    def test_creates_valid_jwt(self) -> None:
        """Token should be a valid JWT decodable with internal secret."""
        job_id = uuid4()
        tenant_id = uuid4()

        token = create_internal_transaction_token(
            job_id=job_id,
            tenant_id=tenant_id,
            credit_check_passed=True,
            max_tokens=4096,
        )

        assert isinstance(token, str)
        assert len(token) > 0

        # Should decode without error
        payload = verify_internal_transaction_token(token)
        assert payload["job_id"] == str(job_id)
        assert payload["tenant_id"] == str(tenant_id)

    def test_payload_contains_required_fields(self) -> None:
        """Token payload must include all required fields."""
        job_id = uuid4()
        tenant_id = uuid4()

        token = create_internal_transaction_token(
            job_id=job_id,
            tenant_id=tenant_id,
            credit_check_passed=True,
            max_tokens=8192,
        )

        payload = verify_internal_transaction_token(token)

        assert payload["ver"] == 1
        assert payload["job_id"] == str(job_id)
        assert payload["tenant_id"] == str(tenant_id)
        assert payload["credit_check_passed"] is True
        assert payload["limits"]["max_tokens"] == 8192
        assert "trace_id" in payload
        assert "exp" in payload
        assert "iat" in payload

    def test_trace_id_is_unique(self) -> None:
        """Each token should have a unique trace_id."""
        job_id = uuid4()
        tenant_id = uuid4()

        token1 = create_internal_transaction_token(
            job_id=job_id, tenant_id=tenant_id,
            credit_check_passed=True, max_tokens=4096,
        )
        token2 = create_internal_transaction_token(
            job_id=job_id, tenant_id=tenant_id,
            credit_check_passed=True, max_tokens=4096,
        )

        p1 = verify_internal_transaction_token(token1)
        p2 = verify_internal_transaction_token(token2)

        assert p1["trace_id"] != p2["trace_id"]

    def test_credit_check_false(self) -> None:
        """Token should correctly encode credit_check_passed=False."""
        token = create_internal_transaction_token(
            job_id=uuid4(), tenant_id=uuid4(),
            credit_check_passed=False, max_tokens=4096,
        )
        payload = verify_internal_transaction_token(token)
        assert payload["credit_check_passed"] is False

    def test_uses_internal_jwt_secret(self) -> None:
        """Token must be signed with internal_jwt_secret, not user JWT secret."""
        settings = get_settings()
        token = create_internal_transaction_token(
            job_id=uuid4(), tenant_id=uuid4(),
            credit_check_passed=True, max_tokens=4096,
        )

        # Should decode with internal secret
        payload = jwt.decode(
            token, settings.internal_jwt_secret, algorithms=["HS256"]
        )
        assert "job_id" in payload

        # Should NOT decode with user JWT secret (different key)
        if settings.jwt_secret != settings.internal_jwt_secret:
            with pytest.raises(jwt.InvalidSignatureError):
                jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])

    def test_expiration_is_10_minutes(self) -> None:
        """Token TTL should be ~10 minutes."""
        token = create_internal_transaction_token(
            job_id=uuid4(), tenant_id=uuid4(),
            credit_check_passed=True, max_tokens=4096,
        )
        payload = verify_internal_transaction_token(token)

        ttl = payload["exp"] - payload["iat"]
        assert ttl == 600  # 10 minutes in seconds


class TestVerifyInternalTransactionToken:
    """Test internal transaction token verification."""

    def test_valid_token(self) -> None:
        """Valid token should decode successfully."""
        job_id = uuid4()
        token = create_internal_transaction_token(
            job_id=job_id, tenant_id=uuid4(),
            credit_check_passed=True, max_tokens=4096,
        )

        payload = verify_internal_transaction_token(token)
        assert payload["job_id"] == str(job_id)

    def test_expired_token_raises(self) -> None:
        """Expired token should raise AuthenticationError."""
        settings = get_settings()

        # Manually create an expired token
        expired_payload = {
            "ver": 1,
            "trace_id": str(uuid4()),
            "job_id": str(uuid4()),
            "tenant_id": str(uuid4()),
            "credit_check_passed": True,
            "limits": {"max_tokens": 4096},
            "iat": int(time.time()) - 700,
            "exp": int(time.time()) - 100,  # Already expired
        }
        expired_token = jwt.encode(
            expired_payload, settings.internal_jwt_secret, algorithm="HS256"
        )

        with pytest.raises(AuthenticationError) as exc_info:
            verify_internal_transaction_token(expired_token)

        assert "expired" in exc_info.value.message.lower()

    def test_tampered_token_raises(self) -> None:
        """Token signed with wrong key should raise AuthenticationError."""
        tampered_payload = {
            "ver": 1,
            "job_id": str(uuid4()),
            "tenant_id": str(uuid4()),
            "exp": int(time.time()) + 600,
        }
        tampered_token = jwt.encode(
            tampered_payload, "wrong_secret_key_not_the_real_one", algorithm="HS256"
        )

        with pytest.raises(AuthenticationError) as exc_info:
            verify_internal_transaction_token(tampered_token)

        assert "invalid" in exc_info.value.message.lower()

    def test_garbage_string_raises(self) -> None:
        """Completely invalid string should raise AuthenticationError."""
        with pytest.raises(AuthenticationError):
            verify_internal_transaction_token("not.a.jwt.at.all")
