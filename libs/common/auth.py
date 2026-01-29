"""Authentication utilities for JWT and API key handling."""

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from pydantic import BaseModel

from libs.common.config import get_settings
from libs.common.exceptions import AuthenticationError


class TokenPayload(BaseModel):
    """JWT token payload structure."""

    sub: str  # Subject (user_id)
    tenant_id: str
    exp: datetime
    iat: datetime
    jti: str  # JWT ID for revocation
    scopes: list[str] = []


def create_access_token(
    user_id: str,
    tenant_id: str,
    scopes: list[str] | None = None,
    expires_delta: timedelta | None = None,
) -> str:
    """Create a JWT access token.

    Args:
        user_id: User identifier
        tenant_id: Tenant identifier
        scopes: Permission scopes
        expires_delta: Custom expiration time

    Returns:
        Encoded JWT token
    """
    settings = get_settings()

    now = datetime.now(timezone.utc)
    if expires_delta:
        expire = now + expires_delta
    else:
        expire = now + timedelta(seconds=settings.jwt_expiration)

    payload = TokenPayload(
        sub=user_id,
        tenant_id=tenant_id,
        exp=expire,
        iat=now,
        jti=secrets.token_urlsafe(16),
        scopes=scopes or [],
    )

    return jwt.encode(
        payload.model_dump(mode="json"),
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

    # Support both "Bearer sk-agent-..." and "sk-agent-..." formats
    if authorization.startswith("Bearer "):
        return authorization[7:]
    elif authorization.startswith("sk-agent-"):
        return authorization
    else:
        raise AuthenticationError(
            message="Invalid API key format",
            details={"reason": "invalid_format"},
        )
