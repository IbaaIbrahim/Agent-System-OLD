"""Rate limiting middleware with waterfall approach."""

import time
from uuid import UUID

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from libs.common import get_logger
from libs.common.config import get_settings
from libs.common.exceptions import RateLimitError
from libs.messaging.redis import get_redis_client

logger = get_logger(__name__)

# Paths exempt from rate limiting
EXEMPT_PATHS = {"/health", "/docs", "/redoc", "/openapi.json"}


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Middleware for rate limiting using Redis with sliding window."""

    async def dispatch(self, request: Request, call_next):
        # Skip rate limiting for exempt paths
        if request.url.path in EXEMPT_PATHS:
            return await call_next(request)

        # Skip for OPTIONS requests
        if request.method == "OPTIONS":
            return await call_next(request)

        # Get tenant ID from request state (set by auth middleware)
        tenant_id = getattr(request.state, "tenant_id", None)
        if not tenant_id:
            return await call_next(request)

        try:
            await self._check_rate_limit(request, tenant_id)
            return await call_next(request)

        except RateLimitError as e:
            logger.warning(
                "Rate limit exceeded",
                tenant_id=str(tenant_id),
                path=request.url.path,
                retry_after=e.retry_after,
            )
            response = JSONResponse(
                status_code=e.status_code,
                content=e.to_dict(),
            )
            if e.retry_after:
                response.headers["Retry-After"] = str(e.retry_after)
            return response

    async def _check_rate_limit(self, request: Request, tenant_id: UUID) -> None:
        """Check rate limits using sliding window algorithm.

        Implements a waterfall rate limiting strategy:
        1. Check requests per minute (RPM)
        2. Check tokens per minute (TPM) - estimated from request
        """
        settings = get_settings()
        redis = await get_redis_client()

        now = time.time()
        window_start = now - 60  # 1-minute sliding window

        # Get tenant-specific limits (from tenant object if available)
        tenant = getattr(request.state, "tenant", None)
        rpm_limit = (
            tenant.rate_limit_rpm if tenant and tenant.rate_limit_rpm
            else settings.rate_limit_rpm
        )
        tpm_limit = (
            tenant.rate_limit_tpm if tenant and tenant.rate_limit_tpm
            else settings.rate_limit_tpm
        )

        # Keys for rate limiting
        rpm_key = f"rate:rpm:{tenant_id}"
        tpm_key = f"rate:tpm:{tenant_id}"

        # Check RPM using sorted set with timestamps
        pipe = redis.pipeline()

        # Remove old entries
        pipe.zremrangebyscore(rpm_key, 0, window_start)

        # Count current entries
        pipe.zcard(rpm_key)

        # Execute pipeline
        results = await pipe.execute()
        current_rpm = results[1]

        if current_rpm >= rpm_limit:
            # Calculate retry time based on oldest request
            oldest = await redis.client.zrange(rpm_key, 0, 0, withscores=True)
            if oldest:
                retry_after = int(oldest[0][1] + 60 - now) + 1
            else:
                retry_after = 60

            raise RateLimitError(
                message=f"Rate limit exceeded: {current_rpm}/{rpm_limit} requests per minute",
                retry_after=retry_after,
                details={
                    "limit_type": "rpm",
                    "current": current_rpm,
                    "limit": rpm_limit,
                },
            )

        # Add current request to RPM counter
        await redis.client.zadd(rpm_key, {str(now): now})
        await redis.expire(rpm_key, 120)  # Expire after 2 minutes

        # For chat endpoints, also check TPM (estimate tokens from content length)
        if request.url.path.endswith("/chat/completions"):
            await self._check_token_rate(
                redis, tenant_id, tpm_key, tpm_limit, now, window_start
            )

    async def _check_token_rate(
        self,
        redis,
        tenant_id: UUID,
        tpm_key: str,
        tpm_limit: int,
        now: float,
        window_start: float,
    ) -> None:
        """Check token rate limit."""
        # Remove old entries and get current sum
        pipe = redis.pipeline()
        pipe.zremrangebyscore(tpm_key, 0, window_start)

        # Get all entries with scores (scores are token counts)
        pipe.zrange(tpm_key, 0, -1, withscores=True)

        results = await pipe.execute()
        entries = results[1] or []

        # Sum up tokens from all entries
        current_tokens = sum(score for _, score in entries)

        if current_tokens >= tpm_limit:
            raise RateLimitError(
                message=f"Token rate limit exceeded: {current_tokens}/{tpm_limit} tokens per minute",
                retry_after=60,
                details={
                    "limit_type": "tpm",
                    "current": int(current_tokens),
                    "limit": tpm_limit,
                },
            )

    async def record_token_usage(
        self,
        tenant_id: UUID,
        input_tokens: int,
        output_tokens: int,
    ) -> None:
        """Record token usage for rate limiting.

        Called after a request completes to track actual token usage.
        """
        redis = await get_redis_client()
        now = time.time()

        tpm_key = f"rate:tpm:{tenant_id}"
        total_tokens = input_tokens + output_tokens

        # Add token count to sorted set
        await redis.client.zadd(tpm_key, {f"{now}:{total_tokens}": total_tokens})
        await redis.expire(tpm_key, 120)
