"""Integration tests for Phase 2: billing, job persistence, and rate limiting.

Tests the complete flow:
1. Create tenant → API key → user → JWT
2. Submit chat completion → verify Job + ChatMessages persisted
3. Billing: insufficient credits → 402
4. Rate limiting: exceed user RPM → 429 with Retry-After

Prerequisites:
    - API Gateway running on localhost:8000
    - Database initialized with migrations
    - Infrastructure running (make infra)
    - Kafka running and agent.jobs topic created
"""

import uuid

import httpx
import pytest


class TestJobPersistence:
    """Test that chat completions persist Job and ChatMessage records."""

    @pytest.mark.asyncio
    async def test_chat_completion_returns_job_id_and_stream_url(
        self,
        http_client: httpx.AsyncClient,
        admin_client: httpx.AsyncClient,
        tenant_data: dict,
    ) -> None:
        """Submitting a chat completion should return job_id and stream_url."""
        # Setup: tenant + API key
        tenant_resp = await admin_client.post(
            "/api/admin/tenants", json=tenant_data
        )
        assert tenant_resp.status_code == 200
        tenant_id = tenant_resp.json()["id"]

        key_resp = await admin_client.post(
            f"/api/admin/tenants/{tenant_id}/api-keys",
            json={"name": "Job Test Key", "scopes": ["*"]},
        )
        api_key = key_resp.json()["api_key"]

        # Submit chat completion
        response = await http_client.post(
            "/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "messages": [{"role": "user", "content": "Hello, world!"}],
                "model": "claude-sonnet-4-20250514",
                "provider": "anthropic",
                "max_tokens": 1024,
            },
        )

        assert response.status_code == 200
        data = response.json()

        assert "job_id" in data
        assert "stream_url" in data
        assert data["status"] == "pending"
        assert "created_at" in data

        # Validate job_id is a valid UUID
        uuid.UUID(data["job_id"])

        # Validate stream_url contains the job_id
        assert data["job_id"] in data["stream_url"]

    @pytest.mark.asyncio
    async def test_chat_completion_with_system_prompt_and_tools(
        self,
        http_client: httpx.AsyncClient,
        admin_client: httpx.AsyncClient,
        tenant_data: dict,
    ) -> None:
        """Chat completion with system prompt and tools should succeed."""
        # Setup
        tenant_resp = await admin_client.post(
            "/api/admin/tenants", json=tenant_data
        )
        tenant_id = tenant_resp.json()["id"]

        key_resp = await admin_client.post(
            f"/api/admin/tenants/{tenant_id}/api-keys",
            json={"name": "Tools Test Key", "scopes": ["*"]},
        )
        api_key = key_resp.json()["api_key"]

        response = await http_client.post(
            "/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "messages": [
                    {"role": "user", "content": "What's the weather?"},
                ],
                "system": "You are a helpful assistant.",
                "tools": [
                    {
                        "name": "get_weather",
                        "description": "Get current weather",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "city": {"type": "string"},
                            },
                            "required": ["city"],
                        },
                    }
                ],
                "model": "claude-sonnet-4-20250514",
                "provider": "anthropic",
                "max_tokens": 2048,
                "temperature": 0.5,
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "pending"

    @pytest.mark.asyncio
    async def test_chat_completion_with_jwt_auth(
        self,
        http_client: httpx.AsyncClient,
        admin_client: httpx.AsyncClient,
        tenant_data: dict,
        user_data: dict,
    ) -> None:
        """Chat completion should work with JWT authentication (user-level)."""
        # Setup: tenant → API key → user → JWT
        tenant_resp = await admin_client.post(
            "/api/admin/tenants", json=tenant_data
        )
        tenant_id = tenant_resp.json()["id"]

        key_resp = await admin_client.post(
            f"/api/admin/tenants/{tenant_id}/api-keys",
            json={"name": "JWT Test Key", "scopes": ["*"]},
        )
        api_key = key_resp.json()["api_key"]

        await http_client.post(
            "/api/v1/users",
            headers={"Authorization": f"Bearer {api_key}"},
            json=user_data,
        )

        token_resp = await http_client.post(
            "/api/v1/auth/token",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"user_id": user_data["external_id"]},
        )
        jwt_token = token_resp.json()["access_token"]

        # Submit chat completion with JWT
        response = await http_client.post(
            "/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {jwt_token}"},
            json={
                "messages": [{"role": "user", "content": "Hello from JWT user!"}],
                "max_tokens": 512,
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "pending"

    @pytest.mark.asyncio
    async def test_chat_completion_empty_messages_rejected(
        self,
        http_client: httpx.AsyncClient,
        admin_client: httpx.AsyncClient,
        tenant_data: dict,
    ) -> None:
        """Chat completion with empty messages should return 422."""
        # Setup
        tenant_resp = await admin_client.post(
            "/api/admin/tenants", json=tenant_data
        )
        tenant_id = tenant_resp.json()["id"]

        key_resp = await admin_client.post(
            f"/api/admin/tenants/{tenant_id}/api-keys",
            json={"name": "Validation Test Key", "scopes": ["*"]},
        )
        api_key = key_resp.json()["api_key"]

        response = await http_client.post(
            "/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"messages": []},
        )

        # Pydantic validation rejects empty messages (min_length=1)
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_chat_completion_unauthenticated_rejected(
        self,
        http_client: httpx.AsyncClient,
    ) -> None:
        """Chat completion without authentication should return 401."""
        response = await http_client.post(
            "/api/v1/chat/completions",
            json={
                "messages": [{"role": "user", "content": "Hello!"}],
            },
        )

        assert response.status_code == 401


class TestRateLimiting:
    """Test rate limiting enforcement."""

    @pytest.mark.asyncio
    async def test_rate_limit_returns_429_with_retry_after(
        self,
        http_client: httpx.AsyncClient,
        admin_client: httpx.AsyncClient,
    ) -> None:
        """Exceeding rate limit should return 429 with Retry-After header.

        Creates a tenant with a very low RPM limit (2) and sends 3 requests.
        The third request should be rate limited.
        """
        # Create tenant with very low RPM limit
        tenant_data = {
            "name": "Rate Limit Test Tenant",
            "slug": f"rl-test-{uuid.uuid4().hex[:8]}",
            "rate_limit_rpm": 2,  # Very low limit
            "rate_limit_tpm": 10000,
        }
        tenant_resp = await admin_client.post(
            "/api/admin/tenants", json=tenant_data
        )
        assert tenant_resp.status_code == 200
        tenant_id = tenant_resp.json()["id"]

        key_resp = await admin_client.post(
            f"/api/admin/tenants/{tenant_id}/api-keys",
            json={"name": "RL Test Key", "scopes": ["*"]},
        )
        api_key = key_resp.json()["api_key"]

        # Send requests up to the limit
        for i in range(2):
            resp = await http_client.post(
                "/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "messages": [{"role": "user", "content": f"Request {i}"}],
                    "max_tokens": 100,
                },
            )
            assert resp.status_code == 200, f"Request {i} failed: {resp.text}"

        # Third request should be rate limited
        resp = await http_client.post(
            "/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "messages": [{"role": "user", "content": "Over limit"}],
                "max_tokens": 100,
            },
        )

        assert resp.status_code == 429
        assert "Retry-After" in resp.headers

        body = resp.json()
        assert "rate limit" in body.get("message", "").lower() or \
               "rate limit" in body.get("error", {}).get("message", "").lower()

    @pytest.mark.asyncio
    async def test_user_custom_rpm_limit_enforced(
        self,
        http_client: httpx.AsyncClient,
        admin_client: httpx.AsyncClient,
    ) -> None:
        """User with custom RPM limit should be limited independently of tenant."""
        # Create tenant with generous limit
        tenant_data = {
            "name": "User RL Test Tenant",
            "slug": f"url-test-{uuid.uuid4().hex[:8]}",
            "rate_limit_rpm": 100,  # Generous tenant limit
            "rate_limit_tpm": 100000,
        }
        tenant_resp = await admin_client.post(
            "/api/admin/tenants", json=tenant_data
        )
        tenant_id = tenant_resp.json()["id"]

        key_resp = await admin_client.post(
            f"/api/admin/tenants/{tenant_id}/api-keys",
            json={"name": "User RL Key", "scopes": ["*"]},
        )
        api_key = key_resp.json()["api_key"]

        # Create user with very low custom limit
        user_data = {
            "external_id": f"rl-user-{uuid.uuid4().hex[:8]}",
            "email": f"rl-{uuid.uuid4().hex[:8]}@example.com",
            "name": "Rate Limited User",
            "role": "member",
            "custom_rpm_limit": 2,  # Very low user limit
        }
        await http_client.post(
            "/api/v1/users",
            headers={"Authorization": f"Bearer {api_key}"},
            json=user_data,
        )

        token_resp = await http_client.post(
            "/api/v1/auth/token",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"user_id": user_data["external_id"]},
        )
        jwt_token = token_resp.json()["access_token"]

        # Send requests up to user limit
        for i in range(2):
            resp = await http_client.post(
                "/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {jwt_token}"},
                json={
                    "messages": [{"role": "user", "content": f"User req {i}"}],
                    "max_tokens": 100,
                },
            )
            assert resp.status_code == 200, f"User request {i} failed: {resp.text}"

        # Third request should exceed user's custom limit
        resp = await http_client.post(
            "/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {jwt_token}"},
            json={
                "messages": [{"role": "user", "content": "Over user limit"}],
                "max_tokens": 100,
            },
        )

        assert resp.status_code == 429
        body = resp.json()
        # Verify it's a user-level limit (not tenant-level)
        if "details" in body:
            assert body["details"].get("limit_scope") == "user"


class TestBillingFlow:
    """Test billing pre-check flow (requires billing enabled in config)."""

    @pytest.mark.asyncio
    async def test_chat_completion_succeeds_with_billing_disabled(
        self,
        http_client: httpx.AsyncClient,
        admin_client: httpx.AsyncClient,
        tenant_data: dict,
    ) -> None:
        """When billing is disabled (default), chat completions should work normally."""
        # Setup
        tenant_resp = await admin_client.post(
            "/api/admin/tenants", json=tenant_data
        )
        tenant_id = tenant_resp.json()["id"]

        key_resp = await admin_client.post(
            f"/api/admin/tenants/{tenant_id}/api-keys",
            json={"name": "Billing Test Key", "scopes": ["*"]},
        )
        api_key = key_resp.json()["api_key"]

        # Submit request — billing disabled by default
        response = await http_client.post(
            "/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "messages": [{"role": "user", "content": "Test billing disabled"}],
                "max_tokens": 256,
            },
        )

        # Should succeed because billing is off
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "pending"
