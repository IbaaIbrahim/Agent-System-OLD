# Testing Guide

## Overview

The project uses pytest for all tests, integrated with the Makefile for easy execution. Tests are organized into unit and integration categories.

## Test Structure

```
tests/
├── conftest.py                              # Shared fixtures
├── unit/                                    # Unit tests (fast, no external deps)
│   ├── test_auth_utils.py                  # Auth utility tests
│   └── ...
└── integration/                             # Integration tests (require services)
    ├── test_phase1_auth_flow.py            # Phase 1 complete flow
    └── ...
```

## Running Tests

### Quick Commands

```bash
# Run Phase 1 tests only
make test-phase1

# Run all integration tests
make test-int

# Run all unit tests
make test-unit

# Run all tests
make test

# Run with coverage
make test-cov
```

### Individual Test Files

```bash
# Run specific test file
pytest tests/integration/test_phase1_auth_flow.py -v

# Run specific test class
pytest tests/integration/test_phase1_auth_flow.py::TestPhase1AuthenticationFlow -v

# Run specific test method
pytest tests/integration/test_phase1_auth_flow.py::TestPhase1AuthenticationFlow::test_health_check -v
```

## Phase 1 Tests

### Integration Tests (`test_phase1_auth_flow.py`)

Complete authentication workflow tests:

1. **Health Check** - Verify API Gateway is running
2. **Master Admin Create Tenant** - Platform owner creates tenant
3. **Master Admin List Tenants** - List all tenants
4. **Master Admin Generate API Key** - Create tenant API key
5. **Tenant Create User** - Create virtual user with tenant API key
6. **User Upsert Logic** - Verify duplicate handling
7. **Token Exchange (External ID)** - Get JWT via external_id
8. **Token Exchange (UUID)** - Get JWT via user UUID
9. **JWT Authentication** - Use JWT to access endpoints
10. **Tenant Isolation** - Verify cross-tenant access prevention
11. **API Key Authentication** - Verify tenant API key works
12. **Invalid Master Admin Key** - Reject invalid admin keys
13. **Missing Authorization** - Reject requests without auth

### Unit Tests (`test_auth_utils.py`)

Authentication utility tests:

1. **JWT Token Creation** - Create valid JWT tokens
2. **JWT Token Decoding** - Decode and validate tokens
3. **Invalid Token Handling** - Reject malformed tokens
4. **API Key Generation** - Generate unique API keys
5. **API Key Hashing** - Consistent hashing
6. **API Key Verification** - Verify keys against hashes

## Test Configuration

### Environment Variables

Tests use the following environment variables from `.env`:

```bash
# API Gateway URL for integration tests
TEST_API_BASE_URL=http://localhost:8000

# Master admin key (must match server config)
MASTER_ADMIN_KEY=your-admin-key
```

### Pytest Configuration (`pyproject.toml`)

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"          # Auto-detect async tests
testpaths = ["tests"]          # Test directory
addopts = "-v --tb=short"      # Verbose with short tracebacks
```

## Fixtures

### Shared Fixtures (`conftest.py`)

Available to all tests:

- **`http_client`** - Async HTTP client for API requests
- **`admin_client`** - HTTP client with master admin auth
- **`tenant_data`** - Sample tenant data (randomized)
- **`user_data`** - Sample user data (randomized)

### Usage Example

```python
async def test_create_tenant(
    admin_client: httpx.AsyncClient,
    tenant_data: dict,
) -> None:
    """Test creating a tenant."""
    response = await admin_client.post("/api/admin/tenants", json=tenant_data)
    assert response.status_code == 200
```

## Prerequisites for Integration Tests

Integration tests require running services:

```bash
# 1. Start infrastructure
make infra

# 2. Run migrations
make migrate

# 3. Start API Gateway (in separate terminal)
make api

# 4. Run tests
make test-int
```

## Continuous Integration

Tests can be integrated into CI/CD pipelines:

```yaml
# Example GitHub Actions workflow
- name: Run tests
  run: |
    make infra
    make migrate
    make api &
    sleep 10
    make test-cov
```

## Coverage Reports

Generate coverage reports:

```bash
# Run tests with coverage
make test-cov

# View HTML report
open htmlcov/index.html
```

Coverage includes:
- `libs/` - Shared libraries
- `services/` - All services

## Writing New Tests

### Unit Test Example

```python
# tests/unit/test_example.py
def test_simple_function() -> None:
    """Test a simple function."""
    result = my_function(42)
    assert result == 84
```

### Integration Test Example

```python
# tests/integration/test_example.py
async def test_api_endpoint(http_client: httpx.AsyncClient) -> None:
    """Test an API endpoint."""
    response = await http_client.get("/api/endpoint")
    assert response.status_code == 200
```

### Async Test Notes

- Tests are automatically detected as async (no decorator needed)
- Use `async def` for async tests
- Use `await` for async operations
- Fixtures can be async or sync

## Best Practices

1. **Isolation** - Each test should be independent
2. **Cleanup** - Use fixtures for setup/teardown
3. **Randomization** - Use random data to avoid conflicts
4. **Descriptive Names** - Test names should describe what they test
5. **Assertions** - One concept per test, multiple assertions OK
6. **Speed** - Keep unit tests fast (<1s each)
7. **Integration Tests** - Test complete workflows

## Troubleshooting

### Tests Can't Connect to API

```bash
# Ensure API Gateway is running
make api

# Check if port 8000 is available
lsof -i :8000
```

### Database Errors

```bash
# Reset database
make migrate-reset

# Verify database connection
make shell-db
```

### Redis Errors

```bash
# Check Redis is running
docker ps | grep redis

# Test Redis connection
make shell-redis
# Then: PING (should return PONG)
```

### Fixture Not Found

- Ensure `conftest.py` is in the `tests/` directory
- Check fixture is properly imported
- Verify fixture name matches usage

## Alternative: Standalone Script

For debugging or manual testing, use the standalone script:

```bash
python scripts/test_phase1_auth.py
```

Benefits:
- Colored output
- Detailed reporting
- Step-by-step execution
- Easier to debug

Use cases:
- Manual testing during development
- Debugging specific scenarios
- Demonstration purposes
- Non-pytest environments
