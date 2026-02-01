"""Integration tests for Phase 1 authentication flow.

Tests the complete authentication workflow:
1. Master admin creates tenant
2. Master admin generates API key
3. Tenant creates virtual user
4. Token exchange for JWT
5. JWT authentication

Prerequisites:
    - API Gateway running on localhost:8000
    - Database initialized with migrations
    - Infrastructure running (make infra)
"""

import httpx
import pytest


class TestPhase1AuthenticationFlow:
    """Test the complete Phase 1 authentication flow."""

    @pytest.mark.asyncio
    async def test_health_check(self, http_client: httpx.AsyncClient) -> None:
        """Test API Gateway health check."""
        response = await http_client.get("/health")
        assert response.status_code == 200

        data = response.json()
        assert data["status"] in ("healthy", "degraded")
        assert data["service"] == "api-gateway"
        assert "dependencies" in data

    @pytest.mark.asyncio
    async def test_master_admin_create_tenant(
        self,
        admin_client: httpx.AsyncClient,
        tenant_data: dict,
    ) -> None:
        """Test master admin can create a tenant."""
        response = await admin_client.post("/api/admin/tenants", json=tenant_data)

        assert response.status_code == 200
        tenant = response.json()

        assert tenant["name"] == tenant_data["name"]
        assert tenant["slug"] == tenant_data["slug"]
        assert tenant["status"] == "active"
        assert tenant["rate_limit_rpm"] == tenant_data["rate_limit_rpm"]
        assert tenant["rate_limit_tpm"] == tenant_data["rate_limit_tpm"]
        assert "id" in tenant
        assert "created_at" in tenant

    @pytest.mark.asyncio
    async def test_master_admin_list_tenants(
        self,
        admin_client: httpx.AsyncClient,
    ) -> None:
        """Test master admin can list tenants."""
        response = await admin_client.get("/api/admin/tenants")

        assert response.status_code == 200
        tenants = response.json()
        assert isinstance(tenants, list)

    @pytest.mark.asyncio
    async def test_master_admin_generate_api_key(
        self,
        admin_client: httpx.AsyncClient,
        tenant_data: dict,
    ) -> None:
        """Test master admin can generate API key for tenant."""
        # Create tenant first
        tenant_response = await admin_client.post(
            "/api/admin/tenants", json=tenant_data
        )
        assert tenant_response.status_code == 200
        tenant = tenant_response.json()
        tenant_id = tenant["id"]

        # Generate API key
        api_key_data = {"name": "Test API Key", "scopes": ["*"]}
        response = await admin_client.post(
            f"/api/admin/tenants/{tenant_id}/api-keys",
            json=api_key_data,
        )

        assert response.status_code == 200
        data = response.json()

        assert "api_key" in data
        assert data["api_key"].startswith("sk-agent-")
        assert "key_info" in data

        key_info = data["key_info"]
        assert key_info["name"] == api_key_data["name"]
        assert key_info["scopes"] == api_key_data["scopes"]
        assert key_info["is_active"] is True
        assert key_info["tenant_id"] == tenant_id

    @pytest.mark.asyncio
    async def test_tenant_create_user(
        self,
        http_client: httpx.AsyncClient,
        admin_client: httpx.AsyncClient,
        tenant_data: dict,
        user_data: dict,
    ) -> None:
        """Test tenant can create virtual user with API key."""
        # Setup: Create tenant and API key
        tenant_response = await admin_client.post(
            "/api/admin/tenants", json=tenant_data
        )
        tenant = tenant_response.json()
        tenant_id = tenant["id"]

        api_key_response = await admin_client.post(
            f"/api/admin/tenants/{tenant_id}/api-keys",
            json={"name": "Test Key", "scopes": ["*"]},
        )
        tenant_api_key = api_key_response.json()["api_key"]

        # Test: Create user with tenant API key
        response = await http_client.post(
            "/api/v1/users",
            headers={"Authorization": f"Bearer {tenant_api_key}"},
            json=user_data,
        )

        assert response.status_code == 200
        user = response.json()

        assert user["external_id"] == user_data["external_id"]
        assert user["email"] == user_data["email"]
        assert user["name"] == user_data["name"]
        assert user["role"] == user_data["role"]
        assert user["custom_rpm_limit"] == user_data["custom_rpm_limit"]
        assert user["tenant_id"] == tenant_id
        assert user["is_active"] is True
        assert "id" in user

    @pytest.mark.asyncio
    async def test_user_upsert_logic(
        self,
        http_client: httpx.AsyncClient,
        admin_client: httpx.AsyncClient,
        tenant_data: dict,
        user_data: dict,
    ) -> None:
        """Test user creation with upsert logic (duplicate external_id returns existing)."""
        # Setup
        tenant_response = await admin_client.post(
            "/api/admin/tenants", json=tenant_data
        )
        tenant_id = tenant_response.json()["id"]

        api_key_response = await admin_client.post(
            f"/api/admin/tenants/{tenant_id}/api-keys",
            json={"name": "Test Key", "scopes": ["*"]},
        )
        tenant_api_key = api_key_response.json()["api_key"]

        # Create user first time
        first_response = await http_client.post(
            "/api/v1/users",
            headers={"Authorization": f"Bearer {tenant_api_key}"},
            json=user_data,
        )
        assert first_response.status_code == 200
        first_user = first_response.json()

        # Try to create same user again (different data but same external_id)
        modified_data = {**user_data, "email": "different@example.com", "name": "Different Name"}
        second_response = await http_client.post(
            "/api/v1/users",
            headers={"Authorization": f"Bearer {tenant_api_key}"},
            json=modified_data,
        )

        assert second_response.status_code == 200
        second_user = second_response.json()

        # Should return same user (original data)
        assert second_user["id"] == first_user["id"]
        assert second_user["email"] == user_data["email"]  # Original email
        assert second_user["name"] == user_data["name"]  # Original name
        assert second_user["external_id"] == user_data["external_id"]

    @pytest.mark.asyncio
    async def test_token_exchange_by_external_id(
        self,
        http_client: httpx.AsyncClient,
        admin_client: httpx.AsyncClient,
        tenant_data: dict,
        user_data: dict,
    ) -> None:
        """Test token exchange using external_id."""
        # Setup
        tenant_response = await admin_client.post(
            "/api/admin/tenants", json=tenant_data
        )
        tenant_id = tenant_response.json()["id"]

        api_key_response = await admin_client.post(
            f"/api/admin/tenants/{tenant_id}/api-keys",
            json={"name": "Test Key", "scopes": ["*"]},
        )
        tenant_api_key = api_key_response.json()["api_key"]

        user_response = await http_client.post(
            "/api/v1/users",
            headers={"Authorization": f"Bearer {tenant_api_key}"},
            json=user_data,
        )
        user = user_response.json()

        # Test: Exchange token using external_id
        response = await http_client.post(
            "/api/v1/auth/token",
            headers={"Authorization": f"Bearer {tenant_api_key}"},
            json={"user_id": user_data["external_id"]},
        )

        assert response.status_code == 200
        token_data = response.json()

        assert "access_token" in token_data
        assert token_data["token_type"] == "Bearer"
        assert token_data["expires_in"] == 3600
        assert token_data["user_id"] == user["id"]
        assert token_data["tenant_id"] == tenant_id
        assert "scopes" in token_data
        assert "job:create" in token_data["scopes"]
        assert "stream:read" in token_data["scopes"]

    @pytest.mark.asyncio
    async def test_token_exchange_by_uuid(
        self,
        http_client: httpx.AsyncClient,
        admin_client: httpx.AsyncClient,
        tenant_data: dict,
        user_data: dict,
    ) -> None:
        """Test token exchange using user UUID."""
        # Setup
        tenant_response = await admin_client.post(
            "/api/admin/tenants", json=tenant_data
        )
        tenant_id = tenant_response.json()["id"]

        api_key_response = await admin_client.post(
            f"/api/admin/tenants/{tenant_id}/api-keys",
            json={"name": "Test Key", "scopes": ["*"]},
        )
        tenant_api_key = api_key_response.json()["api_key"]

        user_response = await http_client.post(
            "/api/v1/users",
            headers={"Authorization": f"Bearer {tenant_api_key}"},
            json=user_data,
        )
        user = user_response.json()

        # Test: Exchange token using UUID
        response = await http_client.post(
            "/api/v1/auth/token",
            headers={"Authorization": f"Bearer {tenant_api_key}"},
            json={"user_id": user["id"]},
        )

        assert response.status_code == 200
        token_data = response.json()

        assert token_data["user_id"] == user["id"]
        assert token_data["tenant_id"] == tenant_id

    @pytest.mark.asyncio
    async def test_jwt_authentication(
        self,
        http_client: httpx.AsyncClient,
        admin_client: httpx.AsyncClient,
        tenant_data: dict,
        user_data: dict,
    ) -> None:
        """Test JWT token can be used to access protected endpoints."""
        # Setup: Create tenant, API key, user, and exchange for JWT
        tenant_response = await admin_client.post(
            "/api/admin/tenants", json=tenant_data
        )
        tenant_id = tenant_response.json()["id"]

        api_key_response = await admin_client.post(
            f"/api/admin/tenants/{tenant_id}/api-keys",
            json={"name": "Test Key", "scopes": ["*"]},
        )
        tenant_api_key = api_key_response.json()["api_key"]

        await http_client.post(
            "/api/v1/users",
            headers={"Authorization": f"Bearer {tenant_api_key}"},
            json=user_data,
        )

        token_response = await http_client.post(
            "/api/v1/auth/token",
            headers={"Authorization": f"Bearer {tenant_api_key}"},
            json={"user_id": user_data["external_id"]},
        )
        jwt_token = token_response.json()["access_token"]

        # Test: Use JWT to access protected endpoint
        response = await http_client.get(
            "/api/v1/users",
            headers={"Authorization": f"Bearer {jwt_token}"},
        )

        assert response.status_code == 200
        users = response.json()
        assert isinstance(users, list)
        assert len(users) >= 1

    @pytest.mark.asyncio
    async def test_tenant_isolation(
        self,
        http_client: httpx.AsyncClient,
        admin_client: httpx.AsyncClient,
        tenant_data: dict,
        user_data: dict,
    ) -> None:
        """Test that JWT tokens cannot access other tenants' admin endpoints."""
        # Setup
        tenant_response = await admin_client.post(
            "/api/admin/tenants", json=tenant_data
        )
        tenant_id = tenant_response.json()["id"]

        api_key_response = await admin_client.post(
            f"/api/admin/tenants/{tenant_id}/api-keys",
            json={"name": "Test Key", "scopes": ["*"]},
        )
        tenant_api_key = api_key_response.json()["api_key"]

        await http_client.post(
            "/api/v1/users",
            headers={"Authorization": f"Bearer {tenant_api_key}"},
            json=user_data,
        )

        token_response = await http_client.post(
            "/api/v1/auth/token",
            headers={"Authorization": f"Bearer {tenant_api_key}"},
            json={"user_id": user_data["external_id"]},
        )
        jwt_token = token_response.json()["access_token"]

        # Test: Try to access admin endpoint with JWT (should fail)
        response = await http_client.get(
            f"/api/admin/tenants/{tenant_id}",
            headers={"Authorization": f"Bearer {jwt_token}"},
        )

        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_api_key_authentication(
        self,
        http_client: httpx.AsyncClient,
        admin_client: httpx.AsyncClient,
        tenant_data: dict,
    ) -> None:
        """Test tenant API key authentication works."""
        # Setup
        tenant_response = await admin_client.post(
            "/api/admin/tenants", json=tenant_data
        )
        tenant_id = tenant_response.json()["id"]

        api_key_response = await admin_client.post(
            f"/api/admin/tenants/{tenant_id}/api-keys",
            json={"name": "Test Key", "scopes": ["*"]},
        )
        tenant_api_key = api_key_response.json()["api_key"]

        # Test: Use API key to access tenant endpoints
        response = await http_client.get(
            "/api/v1/users",
            headers={"Authorization": f"Bearer {tenant_api_key}"},
        )

        assert response.status_code == 200
        users = response.json()
        assert isinstance(users, list)

    @pytest.mark.asyncio
    async def test_invalid_master_admin_key(
        self,
        http_client: httpx.AsyncClient,
        tenant_data: dict,
    ) -> None:
        """Test that invalid master admin key is rejected."""
        response = await http_client.post(
            "/api/admin/tenants",
            headers={"Authorization": "Bearer invalid_key"},
            json=tenant_data,
        )

        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_missing_authorization_header(
        self,
        http_client: httpx.AsyncClient,
    ) -> None:
        """Test that requests without auth header are rejected."""
        response = await http_client.get("/api/v1/users")

        assert response.status_code == 401
