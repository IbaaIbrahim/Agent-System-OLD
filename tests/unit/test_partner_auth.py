"""Unit tests for partner authentication utilities."""

import pytest

from libs.common.auth import (
    create_access_token,
    decode_access_token,
    extract_api_key,
    generate_partner_api_key,
    hash_api_key,
    verify_api_key,
)
from libs.common.exceptions import AuthenticationError


class TestPartnerApiKeyGeneration:
    """Test partner API key generation and hashing."""

    def test_generate_partner_api_key_prefix(self) -> None:
        """Partner API key should have pk-agent- prefix."""
        raw_key, key_hash = generate_partner_api_key()

        assert raw_key.startswith("pk-agent-")
        assert len(raw_key) > 50

    def test_generate_partner_api_key_hash(self) -> None:
        """Partner API key hash should be a valid SHA-256 hex digest."""
        raw_key, key_hash = generate_partner_api_key()

        assert len(key_hash) == 64
        assert all(c in "0123456789abcdef" for c in key_hash)

    def test_partner_key_uniqueness(self) -> None:
        """Generated partner API keys should be unique."""
        key1, hash1 = generate_partner_api_key()
        key2, hash2 = generate_partner_api_key()

        assert key1 != key2
        assert hash1 != hash2

    def test_partner_key_verify(self) -> None:
        """Partner API key should be verifiable against its hash."""
        raw_key, stored_hash = generate_partner_api_key()

        assert verify_api_key(raw_key, stored_hash) is True

    def test_partner_key_verify_wrong_key(self) -> None:
        """Wrong key should not verify against stored hash."""
        _, stored_hash = generate_partner_api_key()

        assert verify_api_key("pk-agent-wrong123", stored_hash) is False

    def test_partner_key_hash_consistent(self) -> None:
        """Same partner key should produce the same hash."""
        raw_key, _ = generate_partner_api_key()

        hash1 = hash_api_key(raw_key)
        hash2 = hash_api_key(raw_key)

        assert hash1 == hash2


class TestExtractApiKeyPartner:
    """Test API key extraction with partner keys."""

    def test_extract_partner_key_with_bearer(self) -> None:
        """Should extract pk-agent-* key from Bearer header."""
        key = extract_api_key("Bearer pk-agent-test123")
        assert key == "pk-agent-test123"

    def test_extract_partner_key_raw(self) -> None:
        """Should extract raw pk-agent-* key without Bearer prefix."""
        key = extract_api_key("pk-agent-test123")
        assert key == "pk-agent-test123"

    def test_extract_tenant_key_still_works(self) -> None:
        """Tenant keys (sk-agent-*) should still be extractable."""
        key = extract_api_key("sk-agent-test123")
        assert key == "sk-agent-test123"

    def test_extract_invalid_key_raises(self) -> None:
        """Invalid key format should raise AuthenticationError."""
        with pytest.raises(AuthenticationError):
            extract_api_key("invalid-key-format")

    def test_extract_missing_header_raises(self) -> None:
        """Missing header should raise AuthenticationError."""
        with pytest.raises(AuthenticationError):
            extract_api_key(None)


class TestJWTWithPartnerId:
    """Test JWT tokens with partner_id field."""

    def test_create_token_with_partner_id(self) -> None:
        """JWT with partner_id should decode correctly."""
        user_id = "550e8400-e29b-41d4-a716-446655440000"
        tenant_id = "660e8400-e29b-41d4-a716-446655440000"
        partner_id = "770e8400-e29b-41d4-a716-446655440000"

        token = create_access_token(
            user_id=user_id,
            tenant_id=tenant_id,
            scopes=["job:create"],
            partner_id=partner_id,
        )

        payload = decode_access_token(token)

        assert payload.sub == user_id
        assert payload.tenant_id == tenant_id
        assert payload.partner_id == partner_id

    def test_create_token_without_partner_id(self) -> None:
        """JWT without partner_id should have it as None."""
        token = create_access_token(
            user_id="550e8400-e29b-41d4-a716-446655440000",
            tenant_id="660e8400-e29b-41d4-a716-446655440000",
        )

        payload = decode_access_token(token)

        assert payload.partner_id is None

    def test_token_backward_compatibility(self) -> None:
        """Token created without partner_id should still decode correctly."""
        token = create_access_token(
            user_id="550e8400-e29b-41d4-a716-446655440000",
            tenant_id="660e8400-e29b-41d4-a716-446655440000",
            scopes=["job:create", "stream:read"],
        )

        payload = decode_access_token(token)

        assert payload.sub == "550e8400-e29b-41d4-a716-446655440000"
        assert payload.tenant_id == "660e8400-e29b-41d4-a716-446655440000"
        assert payload.partner_id is None
        assert payload.scopes == ["job:create", "stream:read"]
