"""Authentication utilities for JWT and API key handling."""

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import jwt
from pydantic import BaseModel

from libs.common.config import get_settings
from libs.common.exceptions import AuthenticationError


class TokenPayload(BaseModel):
    """JWT token payload structure."""

    sub: str  # Subject (user_id)
    tenant_id: str
    partner_id: str | None = None
    exp: datetime
    iat: datetime
    jti: str  # JWT ID for revocation
    scopes: list[str] = []


def create_access_token(
    user_id: str,
    tenant_id: str,
    scopes: list[str] | None = None,
    expires_delta: timedelta | None = None,
    partner_id: str | None = None,
) -> str:
    """Create a JWT access token.

    Args:
        user_id: User identifier
        tenant_id: Tenant identifier
        scopes: Permission scopes
        expires_delta: Custom expiration time
        partner_id: Partner identifier (if tenant belongs to a partner)

    Returns:
        Encoded JWT token
    """
    settings = get_settings()

    now = datetime.now(UTC)
    if expires_delta:
        expire = now + expires_delta
    else:
        expire = now + timedelta(seconds=settings.jwt_expiration)

    payload = TokenPayload(
        sub=user_id,
        tenant_id=tenant_id,
        partner_id=partner_id,
        exp=expire,
        iat=now,
        jti=secrets.token_urlsafe(16),
        scopes=scopes or [],
    )

    data = payload.model_dump()
    # JWT requires exp/iat as integer timestamps, not datetime objects
    data["exp"] = int(payload.exp.timestamp())
    data["iat"] = int(payload.iat.timestamp())

    return jwt.encode(
        data,
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )


def decode_access_token(token: str) -> TokenPayload:
    """Decode and validate a JWT access token.

    Args:
        token: JWT token string

    Returns:
        Decoded token payload

    Raises:
        AuthenticationError: If token is invalid or expired
    """
    settings = get_settings()

    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
        )
        return TokenPayload(**payload)
    except jwt.ExpiredSignatureError:
        raise AuthenticationError(
            message="Token has expired",
            details={"reason": "expired"},
        )
    except jwt.InvalidTokenError as e:
        raise AuthenticationError(
            message="Invalid token",
            details={"reason": str(e)},
        )


def generate_api_key() -> tuple[str, str]:
    """Generate a new API key and its hash.

    Returns:
        Tuple of (raw_key, hashed_key)
    """
    # Generate a random key with prefix for easy identification
    prefix = "sk-agent"
    random_part = secrets.token_urlsafe(32)
    raw_key = f"{prefix}-{random_part}"

    # Hash the key for storage
    hashed_key = hash_api_key(raw_key)

    return raw_key, hashed_key


def generate_partner_api_key() -> tuple[str, str]:
    """Generate a new partner API key and its hash.

    Partner keys use the 'pk-agent' prefix to distinguish them
    from tenant API keys ('sk-agent').

    Returns:
        Tuple of (raw_key, hashed_key)
    """
    prefix = "pk-agent"
    random_part = secrets.token_urlsafe(32)
    raw_key = f"{prefix}-{random_part}"

    hashed_key = hash_api_key(raw_key)

    return raw_key, hashed_key


def hash_api_key(api_key: str) -> str:
    """Hash an API key for secure storage.

    Args:
        api_key: Raw API key

    Returns:
        SHA-256 hash of the key
    """
    return hashlib.sha256(api_key.encode()).hexdigest()


def verify_api_key(provided_key: str, stored_hash: str) -> bool:
    """Verify an API key against its stored hash.

    Args:
        provided_key: API key provided by client
        stored_hash: Hash stored in database

    Returns:
        True if key is valid
    """
    provided_hash = hash_api_key(provided_key)
    return hmac.compare_digest(provided_hash, stored_hash)


def extract_bearer_token(authorization: str | None) -> str:
    """Extract token from Authorization header.

    Args:
        authorization: Authorization header value

    Returns:
        Bearer token

    Raises:
        AuthenticationError: If header is missing or malformed
    """
    if not authorization:
        raise AuthenticationError(
            message="Missing authorization header",
            details={"reason": "missing_header"},
        )

    parts = authorization.split()

    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise AuthenticationError(
            message="Invalid authorization header format",
            details={"reason": "invalid_format", "expected": "Bearer <token>"},
        )

    return parts[1]


def extract_api_key(authorization: str | None) -> str:
    """Extract API key from Authorization header.

    Args:
        authorization: Authorization header value

    Returns:
        API key

    Raises:
        AuthenticationError: If header is missing or malformed
    """
    if not authorization:
        raise AuthenticationError(
            message="Missing authorization header",
            details={"reason": "missing_header"},
        )

    # Support both "Bearer {key}" and raw key formats
    # Handles both tenant keys (sk-agent-*) and partner keys (pk-agent-*)
    if authorization.startswith("Bearer "):
        return authorization[7:]
    elif authorization.startswith(("sk-agent-", "pk-agent-")):
        return authorization
    else:
        raise AuthenticationError(
            message="Invalid API key format",
            details={"reason": "invalid_format"},
        )


# --- Internal Transaction Tokens ---
# These tokens travel with Kafka payloads so downstream workers
# can verify job legitimacy without access to the HTTP request context.


def create_internal_transaction_token(
    job_id: UUID,
    tenant_id: UUID,
    credit_check_passed: bool,
    max_tokens: int,
    partner_id: UUID | None = None,
) -> str:
    """Create an internal JWT for Kafka payload authentication.

    Signed with internal_jwt_secret (separate from user JWT secret).
    Short-lived (10 min TTL) to limit replay window.

    Args:
        job_id: Job identifier
        tenant_id: Tenant identifier
        credit_check_passed: Whether billing pre-check succeeded
        max_tokens: Maximum tokens allowed for this job
        partner_id: Partner identifier (if tenant belongs to a partner)

    Returns:
        Encoded JWT string
    """
    settings = get_settings()
    now = datetime.now(UTC)

    payload: dict[str, Any] = {
        "ver": 2,
        "trace_id": str(uuid4()),
        "job_id": str(job_id),
        "tenant_id": str(tenant_id),
        "partner_id": str(partner_id) if partner_id else None,
        "credit_check_passed": credit_check_passed,
        "limits": {"max_tokens": max_tokens},
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=10)).timestamp()),
    }

    return jwt.encode(
        payload,
        settings.internal_jwt_secret,
        algorithm="HS256",
    )


def verify_internal_transaction_token(token: str) -> dict[str, Any]:
    """Verify and decode an internal transaction token.

    Args:
        token: JWT token string

    Returns:
        Decoded token payload dict

    Raises:
        AuthenticationError: If token is expired, tampered, or invalid
    """
    settings = get_settings()

    try:
        return jwt.decode(
            token,
            settings.internal_jwt_secret,
            algorithms=["HS256"],
        )
    except jwt.ExpiredSignatureError:
        raise AuthenticationError(
            message="Internal transaction token expired",
            details={"reason": "expired"},
        )
    except jwt.InvalidTokenError as e:
        raise AuthenticationError(
            message="Invalid internal transaction token",
            details={"reason": str(e)},
        )
