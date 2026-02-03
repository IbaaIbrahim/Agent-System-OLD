"""Shared pytest fixtures for all tests."""

import os
from collections.abc import AsyncGenerator

import httpx
import pytest
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Test configuration
TEST_API_BASE_URL = os.getenv("TEST_API_BASE_URL", "http://localhost:8000")
TEST_MASTER_ADMIN_KEY = os.getenv(
    "MASTER_ADMIN_KEY",
    "change_in_production_use_long_random_string_min_32_chars",
)


@pytest.fixture
def api_base_url() -> str:
    """Get API base URL for tests."""
    return TEST_API_BASE_URL


@pytest.fixture
def master_admin_key() -> str:
    """Get master admin key for tests."""
    return TEST_MASTER_ADMIN_KEY


@pytest.fixture
async def http_client() -> AsyncGenerator[httpx.AsyncClient, None]:
    """Async HTTP client for API requests."""
    async with httpx.AsyncClient(
        base_url=TEST_API_BASE_URL,
        timeout=30.0,
    ) as client:
        yield client


@pytest.fixture
async def admin_client(
    http_client: httpx.AsyncClient,
    master_admin_key: str,
) -> httpx.AsyncClient:
    """HTTP client with master admin authentication."""
    http_client.headers.update({"Authorization": f"Bearer {master_admin_key}"})
    return http_client


@pytest.fixture
async def tenant_data() -> dict:
    """Sample tenant data for tests."""
    import uuid

    return {
        "name": "Test Tenant",
        "slug": f"test-tenant-{uuid.uuid4().hex[:8]}",
        "rate_limit_rpm": 100,
        "rate_limit_tpm": 10000,
    }


@pytest.fixture
async def user_data() -> dict:
    """Sample user data for tests."""
    import uuid

    return {
        "external_id": f"user_{uuid.uuid4().hex[:8]}",
        "email": f"test-{uuid.uuid4().hex[:8]}@example.com",
        "name": "Test User",
        "role": "member",
        "custom_rpm_limit": 50,
    }


@pytest.fixture
async def partner_data() -> dict:
    """Sample partner data for tests."""
    import uuid

    return {
        "name": "Test Partner",
        "slug": f"test-partner-{uuid.uuid4().hex[:8]}",
        "contact_email": f"partner-{uuid.uuid4().hex[:8]}@example.com",
        "rate_limit_rpm": 500,
        "rate_limit_tpm": 50000,
    }


@pytest.fixture
async def partner_client(
    http_client: httpx.AsyncClient,
) -> httpx.AsyncClient:
    """HTTP client with a placeholder partner API key.

    Integration tests should create a real partner + key first,
    then update this client's Authorization header.
    """
    http_client.headers.update({"Authorization": "Bearer pk-agent-test-placeholder"})
    return http_client
