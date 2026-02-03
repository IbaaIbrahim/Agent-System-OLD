"""Integration tests for the multi-partner (B2B2B) flow.

Tests the complete partner lifecycle:
1. Super admin creates partner
2. Super admin generates partner API key
3. Partner creates tenant (via partner API key)
4. Partner generates tenant API key
5. Tenant creates virtual user
6. Token exchange for JWT
7. JWT authentication

Prerequisites:
    - API Gateway running on localhost:8000
    - Database initialized with migrations (including 004_partners)
    - Infrastructure running (make infra)
"""

import httpx
import pytest


class TestPartnerLifecycleFlow:
    """Test the complete partner lifecycle."""

    @pytest.mark.asyncio
    async def test_super_admin_creates_partner(
        self,
        admin_client: httpx.AsyncClient,
        partner_data: dict,
    ) -> None:
        """Super admin can create a new partner."""
        response = await admin_client.post(
            "/api/admin/partners", json=partner_data
        )

        assert response.status_code == 200
        partner = response.json()

        assert partner["name"] == partner_data["name"]
        assert partner["slug"] == partner_data["slug"]
        assert partner["status"] == "active"
        assert "id" in partner
        assert "created_at" in partner

    @pytest.mark.asyncio
    async def test_super_admin_lists_partners(
        self,
        admin_client: httpx.AsyncClient,
    ) -> None:
        """Super admin can list all partners."""
        response = await admin_client.get("/api/admin/partners")

        assert response.status_code == 200
        partners = response.json()
        assert isinstance(partners, list)

    @pytest.mark.asyncio
    async def test_super_admin_gets_partner(
        self,
        admin_client: httpx.AsyncClient,
        partner_data: dict,
    ) -> None:
        """Super admin can get partner by ID."""
        # Create partner
        create_resp = await admin_client.post(
            "/api/admin/partners", json=partner_data
        )
        partner_id = create_resp.json()["id"]

        # Get partner
        response = await admin_client.get(f"/api/admin/partners/{partner_id}")

        assert response.status_code == 200
        partner = response.json()
        assert partner["id"] == partner_id
        assert partner["name"] == partner_data["name"]

    @pytest.mark.asyncio
    async def test_super_admin_updates_partner(
        self,
        admin_client: httpx.AsyncClient,
        partner_data: dict,
    ) -> None:
        """Super admin can update partner settings."""
        # Create partner
        create_resp = await admin_client.post(
            "/api/admin/partners", json=partner_data
        )
        partner_id = create_resp.json()["id"]

        # Update partner
        response = await admin_client.put(
            f"/api/admin/partners/{partner_id}",
            json={"name": "Updated Partner Name", "rate_limit_rpm": 999},
        )

        assert response.status_code == 200
        partner = response.json()
        assert partner["name"] == "Updated Partner Name"
        assert partner["rate_limit_rpm"] == 999

    @pytest.mark.asyncio
    async def test_super_admin_generates_partner_api_key(
        self,
        admin_client: httpx.AsyncClient,
        partner_data: dict,
    ) -> None:
        """Super admin can generate partner API key."""
        # Create partner
        create_resp = await admin_client.post(
            "/api/admin/partners", json=partner_data
        )
        partner_id = create_resp.json()["id"]

        # Generate API key
        response = await admin_client.post(
            f"/api/admin/partners/{partner_id}/api-keys",
            json={"name": "Production Key"},
        )

        assert response.status_code == 200
        key_resp = response.json()

        assert key_resp["api_key"].startswith("pk-agent-")
        assert key_resp["key_info"]["name"] == "Production Key"
        assert key_resp["key_info"]["is_active"] is True

    @pytest.mark.asyncio
    async def test_full_partner_to_tenant_flow(
        self,
        admin_client: httpx.AsyncClient,
        http_client: httpx.AsyncClient,
        partner_data: dict,
        tenant_data: dict,
    ) -> None:
        """Full flow: create partner → partner key → partner creates tenant."""
        import uuid

        # 1. Super admin creates partner
        partner_resp = await admin_client.post(
            "/api/admin/partners", json=partner_data
        )
        assert partner_resp.status_code == 200
        partner_id = partner_resp.json()["id"]

        # 2. Super admin generates partner API key
        key_resp = await admin_client.post(
            f"/api/admin/partners/{partner_id}/api-keys",
            json={"name": "Partner Production Key"},
        )
        assert key_resp.status_code == 200
        partner_api_key = key_resp.json()["api_key"]

        # 3. Partner creates tenant using their API key
        partner_headers = {"Authorization": f"Bearer {partner_api_key}"}
        unique_tenant = {
            **tenant_data,
            "slug": f"partner-tenant-{uuid.uuid4().hex[:8]}",
        }

        tenant_resp = await http_client.post(
            "/api/admin/tenants",
            json=unique_tenant,
            headers=partner_headers,
        )
        assert tenant_resp.status_code == 200
        tenant = tenant_resp.json()
        assert tenant["partner_id"] == partner_id

        # 4. Partner lists only their tenants
        list_resp = await http_client.get(
            "/api/admin/tenants",
            headers=partner_headers,
        )
        assert list_resp.status_code == 200
        tenants = list_resp.json()
        # All returned tenants should belong to this partner
        for t in tenants:
            assert t["partner_id"] == partner_id

    @pytest.mark.asyncio
    async def test_partner_key_revocation(
        self,
        admin_client: httpx.AsyncClient,
        partner_data: dict,
    ) -> None:
        """Super admin can revoke a partner API key."""
        # Create partner + key
        partner_resp = await admin_client.post(
            "/api/admin/partners", json=partner_data
        )
        partner_id = partner_resp.json()["id"]

        key_resp = await admin_client.post(
            f"/api/admin/partners/{partner_id}/api-keys",
            json={"name": "Revocable Key"},
        )
        key_id = key_resp.json()["key_info"]["id"]

        # Revoke
        revoke_resp = await admin_client.delete(
            f"/api/admin/partner-api-keys/{key_id}"
        )
        assert revoke_resp.status_code == 200
        assert revoke_resp.json()["status"] == "revoked"

    @pytest.mark.asyncio
    async def test_duplicate_partner_slug_rejected(
        self,
        admin_client: httpx.AsyncClient,
        partner_data: dict,
    ) -> None:
        """Creating a partner with a duplicate slug should fail."""
        # Create first partner
        resp1 = await admin_client.post("/api/admin/partners", json=partner_data)
        assert resp1.status_code == 200

        # Attempt duplicate slug
        resp2 = await admin_client.post("/api/admin/partners", json=partner_data)
        assert resp2.status_code in (400, 422)
