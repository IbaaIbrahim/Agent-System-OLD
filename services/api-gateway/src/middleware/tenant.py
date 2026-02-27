"""Tenant context middleware."""

from uuid import UUID

from fastapi import Request
from sqlalchemy import select
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from libs.common import get_logger
from libs.common.exceptions import AuthorizationError
from libs.db import get_session_context
from libs.db.models import Partner, Tenant

logger = get_logger(__name__)

# Paths that don't need tenant context
EXEMPT_PATHS = {"/health", "/docs", "/redoc", "/openapi.json"}


class TenantMiddleware(BaseHTTPMiddleware):
    """Middleware for loading tenant context."""

    async def dispatch(self, request: Request, call_next):
        # Skip for exempt paths
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
            # Load full tenant if not already loaded
            if not hasattr(request.state, "tenant") or request.state.tenant is None:
                await self._load_tenant(request, tenant_id)

            return await call_next(request)

        except AuthorizationError as e:
            logger.warning(
                "Tenant authorization failed",
                tenant_id=str(tenant_id),
                error=e.message,
            )
            return JSONResponse(
                status_code=e.status_code,
                content=e.to_dict(),
            )

    async def _load_tenant(self, request: Request, tenant_id: UUID) -> None:
        """Load tenant from database."""
        async with get_session_context() as session:
            result = await session.execute(
                select(Tenant).where(Tenant.id == tenant_id)
            )
            tenant = result.scalar_one_or_none()

            if not tenant:
                raise AuthorizationError(
                    message="Tenant not found",
                    details={"tenant_id": str(tenant_id)},
                )

            if tenant.status != "active":
                raise AuthorizationError(
                    message=f"Tenant is {tenant.status}",
                    details={"tenant_status": tenant.status},
                )

            request.state.tenant = tenant

            # Load partner if tenant belongs to one (B2B2B support)
            if tenant.partner_id:
                partner_result = await session.execute(
                    select(Partner).where(Partner.id == tenant.partner_id)
                )
                request.state.partner = partner_result.scalar_one_or_none()
            else:
                request.state.partner = None


def get_tenant(request: Request) -> Tenant:
    """Get tenant from request state.

    Use this as a dependency in route handlers.
    """
    tenant = getattr(request.state, "tenant", None)
    if not tenant:
        raise AuthorizationError("Tenant context not available")
    return tenant


def get_tenant_id(request: Request) -> UUID:
    """Get tenant ID from request state."""
    tenant_id = getattr(request.state, "tenant_id", None)
    if not tenant_id:
        raise AuthorizationError("Tenant context not available")
    return tenant_id


def get_user_id(request: Request) -> UUID | None:
    """Get user ID from request state.

    Returns None if user is not authenticated (e.g. API key access).
    """
    return getattr(request.state, "user_id", None)
