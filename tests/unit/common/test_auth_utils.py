"""Unit tests for authentication utilities."""

import pytest

from libs.common.auth import (
    create_access_token,
    decode_access_token,
    generate_api_key,
    hash_api_key,
    verify_api_key,
)
from libs.common.exceptions import AuthenticationError


class TestJWTTokens:
    """Test JWT token creation and validation."""

    def test_create_and_decode_access_token(self) -> None:
        """Test creating and decoding a JWT access token."""
        user_id = "550e8400-e29b-41d4-a716-446655440000"
        tenant_id = "660e8400-e29b-41d4-a716-446655440000"
        scopes = ["job:create", "stream:read"]

        # Create token
        token = create_access_token(
            user_id=user_id,
            tenant_id=tenant_id,
            scopes=scopes,
        )

        assert isinstance(token, str)
        assert len(token) > 0

        # Decode token
        payload = decode_access_token(token)

        assert payload.sub == user_id
        assert payload.tenant_id == tenant_id
        assert payload.scopes == scopes
        assert payload.jti  # JWT ID should be present

    def test_decode_invalid_token(self) -> None:
        """Test that decoding an invalid token raises AuthenticationError."""
        with pytest.raises(AuthenticationError) as exc_info:
            decode_access_token("invalid_token")

        assert "Invalid token" in str(exc_info.value.message)

    def test_decode_malformed_token(self) -> None:
        """Test that decoding a malformed token raises AuthenticationError."""
        with pytest.raises(AuthenticationError):
            decode_access_token("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.invalid")


class TestAPIKeyGeneration:
    """Test API key generation and hashing."""

    def test_generate_api_key(self) -> None:
        """Test API key generation returns key and hash."""
        raw_key, key_hash = generate_api_key()

        # Check raw key format
        assert raw_key.startswith("sk-agent-")
        assert len(raw_key) > 50  # Should be long enough

        # Check hash
        assert len(key_hash) == 64  # SHA-256 produces 64 hex chars
        assert all(c in "0123456789abcdef" for c in key_hash)

    def test_api_key_uniqueness(self) -> None:
        """Test that generated API keys are unique."""
        key1, hash1 = generate_api_key()
        key2, hash2 = generate_api_key()

        assert key1 != key2
        assert hash1 != hash2

    def test_hash_api_key(self) -> None:
        """Test API key hashing produces consistent hashes."""
        api_key = "sk-agent-test123"

        hash1 = hash_api_key(api_key)
        hash2 = hash_api_key(api_key)

        # Same key should produce same hash
        assert hash1 == hash2
        assert len(hash1) == 64

    def test_verify_api_key_valid(self) -> None:
        """Test verifying a valid API key."""
        raw_key, stored_hash = generate_api_key()

        result = verify_api_key(raw_key, stored_hash)

        assert result is True

    def test_verify_api_key_invalid(self) -> None:
        """Test verifying an invalid API key."""
        _, stored_hash = generate_api_key()
        wrong_key = "sk-agent-wrong123"

        result = verify_api_key(wrong_key, stored_hash)

        assert result is False
