"""Unit tests for internal transaction token v2 (with partner_id)."""

from uuid import uuid4

from libs.common.auth import (
    create_internal_transaction_token,
    verify_internal_transaction_token,
)


class TestInternalTokenV2:
    """Test internal transaction token v2 with partner_id support."""

    def test_token_version_is_2(self) -> None:
        """Token version should be 2 (includes partner_id field)."""
        token = create_internal_transaction_token(
            job_id=uuid4(),
            tenant_id=uuid4(),
            credit_check_passed=True,
            max_tokens=4096,
        )
        payload = verify_internal_transaction_token(token)
        assert payload["ver"] == 2

    def test_token_with_partner_id(self) -> None:
        """Token should encode partner_id when provided."""
        partner_id = uuid4()
        token = create_internal_transaction_token(
            job_id=uuid4(),
            tenant_id=uuid4(),
            credit_check_passed=True,
            max_tokens=4096,
            partner_id=partner_id,
        )
        payload = verify_internal_transaction_token(token)
        assert payload["partner_id"] == str(partner_id)

    def test_token_without_partner_id(self) -> None:
        """Token should have partner_id=None when not provided."""
        token = create_internal_transaction_token(
            job_id=uuid4(),
            tenant_id=uuid4(),
            credit_check_passed=True,
            max_tokens=4096,
        )
        payload = verify_internal_transaction_token(token)
        assert payload["partner_id"] is None

    def test_token_preserves_all_fields_with_partner(self) -> None:
        """Token with partner_id should preserve all standard fields."""
        job_id = uuid4()
        tenant_id = uuid4()
        partner_id = uuid4()

        token = create_internal_transaction_token(
            job_id=job_id,
            tenant_id=tenant_id,
            credit_check_passed=True,
            max_tokens=8192,
            partner_id=partner_id,
        )

        payload = verify_internal_transaction_token(token)

        assert payload["ver"] == 2
        assert payload["job_id"] == str(job_id)
        assert payload["tenant_id"] == str(tenant_id)
        assert payload["partner_id"] == str(partner_id)
        assert payload["credit_check_passed"] is True
        assert payload["limits"]["max_tokens"] == 8192
        assert "trace_id" in payload
        assert "exp" in payload
        assert "iat" in payload

    def test_backward_compat_no_partner_still_valid(self) -> None:
        """Tokens without partner_id should be decodable."""
        token = create_internal_transaction_token(
            job_id=uuid4(),
            tenant_id=uuid4(),
            credit_check_passed=False,
            max_tokens=4096,
        )

        payload = verify_internal_transaction_token(token)

        assert payload["credit_check_passed"] is False
        assert payload["partner_id"] is None
        assert payload["ver"] == 2
