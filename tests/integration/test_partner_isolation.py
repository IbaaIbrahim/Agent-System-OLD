"""Integration tests for cross-partner tenant isolation.

Verifies that:
- Partner A cannot see or manage Partner B's tenants
- Platform owner can see all tenants across all partners
- Non-authenticated requests are rejected

Prerequisites:
    - API Gateway running on localhost:8000
    - Database initialized with migrations (including 004_partners)
    - Infrastructure running (make infra)
"""

import uuid

import httpx
import pytest


class TestPartnerIsolation:
    """Test that partners are isolated from each other."""

    @pytest.fixture
    async def two_partners_with_tenants(
        self,
        admin_client: httpx.AsyncClient,
        http_client: httpx.AsyncClient,
    ) -> dict:
        """Create two partners, each with a tenant and API key.

        Returns:
            Dict with partner_a, partner_b details including
            partner_id, api_key, tenant_id for each.
        """
        result = {}

        for label in ("a", "b"):
            # Create partner
            partner_resp = await admin_client.post(
                "/api/admin/partners",
                json={
                    "name": f"Partner {label.upper()}",
                    "slug": f"partner-{label}-{uuid.uuid4().hex[:8]}",
                },
            )
            assert partner_resp.status_code == 200
            partner_id = partner_resp.json()["id"]

            # Generate partner API key
            key_resp = await admin_client.post(
                f"/api/admin/partners/{partner_id}/api-keys",
                json={"name": f"Partner {label.upper()} Key"},
            )
            assert key_resp.status_code == 200
            partner_api_key = key_resp.json()["api_key"]

            # Partner creates a tenant
            tenant_resp = await http_client.post(
                "/api/admin/tenants",
                json={
                    "name": f"Tenant of Partner {label.upper()}",
                    "slug": f"tenant-{label}-{uuid.uuid4().hex[:8]}",
                },
                headers={"Authorization": f"Bearer {partner_api_key}"},
            )
            assert tenant_resp.status_code == 200
            tenant_id = tenant_resp.json()["id"]

            result[f"partner_{label}"] = {
                "partner_id": partner_id,
                "api_key": partner_api_key,
                "tenant_id": tenant_id,
            }

        return result

    @pytest.mark.asyncio
    async def test_partner_a_cannot_see_partner_b_tenants(
        self,
        http_client: httpx.AsyncClient,
        two_partners_with_tenants: dict,
    ) -> None:
        """Partner A listing tenants should not include Partner B's tenants."""
        partner_a = two_partners_with_tenants["partner_a"]
        partner_b = two_partners_with_tenants["partner_b"]

        # Partner A lists tenants
        resp = await http_client.get(
            "/api/admin/tenants",
            headers={"Authorization": f"Bearer {partner_a['api_key']}"},
        )
        assert resp.status_code == 200
        tenants = resp.json()

        tenant_ids = [t["id"] for t in tenants]

        # Partner A should see their own tenant
        assert partner_a["tenant_id"] in tenant_ids

        # Partner A should NOT see Partner B's tenant
        assert partner_b["tenant_id"] not in tenant_ids

    @pytest.mark.asyncio
    async def test_partner_a_cannot_get_partner_b_tenant(
        self,
        http_client: httpx.AsyncClient,
        two_partners_with_tenants: dict,
    ) -> None:
        """Partner A should get 403 when trying to access Partner B's tenant."""
        partner_a = two_partners_with_tenants["partner_a"]
        partner_b = two_partners_with_tenants["partner_b"]

        resp = await http_client.get(
            f"/api/admin/tenants/{partner_b['tenant_id']}",
            headers={"Authorization": f"Bearer {partner_a['api_key']}"},
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_platform_owner_sees_all_tenants(
        self,
        admin_client: httpx.AsyncClient,
        two_partners_with_tenants: dict,
    ) -> None:
        """Platform owner should see tenants from all partners."""
        partner_a = two_partners_with_tenants["partner_a"]
        partner_b = two_partners_with_tenants["partner_b"]

        resp = await admin_client.get("/api/admin/tenants")
        assert resp.status_code == 200
        tenants = resp.json()

        tenant_ids = [t["id"] for t in tenants]

        assert partner_a["tenant_id"] in tenant_ids
        assert partner_b["tenant_id"] in tenant_ids

    @pytest.mark.asyncio
    async def test_platform_owner_filter_by_partner(
        self,
        admin_client: httpx.AsyncClient,
        two_partners_with_tenants: dict,
    ) -> None:
        """Platform owner can filter tenants by partner_id."""
        partner_a = two_partners_with_tenants["partner_a"]

        resp = await admin_client.get(
            "/api/admin/tenants",
            params={"partner_id": partner_a["partner_id"]},
        )
        assert resp.status_code == 200
        tenants = resp.json()

        # All returned tenants should belong to Partner A
        for tenant in tenants:
            assert tenant["partner_id"] == partner_a["partner_id"]

    @pytest.mark.asyncio
    async def test_unauthenticated_request_rejected(
        self,
        http_client: httpx.AsyncClient,
    ) -> None:
        """Request without auth should be rejected."""
        # Clear any default headers
        http_client.headers.pop("Authorization", None)

        resp = await http_client.get("/api/admin/tenants")
        assert resp.status_code == 401
