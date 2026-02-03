# Testing Guide

## Overview

The project uses pytest for all tests, integrated with the Makefile for easy execution. Tests are organized into unit and integration categories.

## Test Structure

```
tests/
├── conftest.py                              # Shared fixtures (http_client, admin_client, tenant_data, partner_data, etc.)
├── unit/                                    # Unit tests (fast, no external deps) — 62 tests
│   ├── test_auth_utils.py                  # JWT tokens, API key generation/hashing/verification (8 tests)
│   ├── test_billing.py                     # Token estimation, credit checks, reservations, refunds (13 tests)
│   ├── test_internal_token.py              # Internal transaction token v2 creation/verification (10 tests)
│   ├── test_internal_token_v2.py           # Token v2 with partner_id, backward compatibility (5 tests)
│   ├── test_rate_limiting.py               # Tenant/user waterfall rate limiting (6 tests)
│   ├── test_partner_auth.py                # Partner key generation, extraction, JWT with partner_id (11 tests)
│   └── test_partner_rate_limiting.py       # Partner RPM waterfall, inheritance, legacy skip (6 tests)
└── integration/                             # Integration tests (require running services)
    ├── test_phase1_auth_flow.py            # Phase 1: admin → tenant → user → JWT auth flow
    ├── test_phase2_billing_flow.py         # Phase 2: job persistence, billing, rate limiting
    ├── test_partner_flow.py                # Phase 2.5: partner lifecycle (create → key → tenant → isolation)
    └── test_partner_isolation.py           # Phase 2.5: cross-partner isolation, platform owner visibility
```

## Running Tests

### Quick Commands

```bash
# Run all unit tests (62 tests, fast, no infrastructure needed)
make test-unit

# Run all integration tests (requires running services)
make test-int

# Run all tests
make test

# Run with coverage
make test-cov

# Run specific test categories
pytest tests/unit/test_partner_auth.py -v          # Partner auth tests only
pytest tests/unit/test_partner_rate_limiting.py -v  # Partner rate limiting only
pytest tests/unit/ -k "partner" -v                  # All partner-related unit tests
pytest tests/integration/test_partner_flow.py -v    # Partner integration tests
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

## Test Details by Phase

### Phase 1 Tests

#### Integration Tests (`test_phase1_auth_flow.py`)

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

#### Unit Tests (`test_auth_utils.py`)

Authentication utility tests (8 tests):

1. **JWT Token Creation** - Create valid JWT tokens
2. **JWT Token Decoding** - Decode and validate tokens
3. **Invalid Token Handling** - Reject malformed tokens
4. **API Key Generation** - Generate unique API keys with `sk-agent-` prefix
5. **API Key Uniqueness** - Different keys each time
6. **API Key Hashing** - Consistent SHA-256 hashing
7. **API Key Verification (valid)** - Verify correct key matches hash
8. **API Key Verification (invalid)** - Wrong key does not match

### Phase 2 Tests

#### Unit Tests (`test_billing.py`) — 13 tests

- Token estimation from message content length
- Credit balance check (sufficient, insufficient, exact)
- Credit reservation with atomic Redis DECRBY
- Race condition handling (restore balance on negative)
- Reservation release with refund difference

#### Unit Tests (`test_internal_token.py`) — 10 tests

- Valid JWT creation with internal secret
- Required payload fields (ver, job_id, tenant_id, credit_check_passed, limits, trace_id)
- Unique trace_id per token
- credit_check_passed encoding (true/false)
- Internal vs user JWT secret isolation
- 10-minute expiration TTL
- Expired token rejection
- Tampered token rejection

#### Unit Tests (`test_rate_limiting.py`) — 6 tests

- Tenant under limit passes
- Tenant over limit raises RateLimitError
- User with custom RPM limit enforced
- User inherits tenant limit when custom is None
- Tenant blocked skips user check (waterfall short-circuit)
- No user_id skips user-level check

### Phase 2.5 Tests (B2B2B Partners)

#### Unit Tests (`test_partner_auth.py`) — 11 tests

- Partner API key generation with `pk-agent-` prefix
- Partner key hash is valid SHA-256
- Partner key uniqueness
- Partner key verification (valid + invalid)
- Partner key hash consistency
- Extract partner key with Bearer prefix
- Extract raw partner key
- Tenant key extraction still works (backward compat)
- Invalid key format rejected
- Missing header rejected
- JWT with partner_id (creation, decoding, backward compat)

#### Unit Tests (`test_internal_token_v2.py`) — 5 tests

- Token version is 2
- Token encodes partner_id when provided
- Token has partner_id=None when not provided
- All standard fields preserved with partner_id
- Backward compatibility (no partner_id still valid)

#### Unit Tests (`test_partner_rate_limiting.py`) — 6 tests

- Partner under RPM limit passes to tenant check
- Partner over RPM limit raises RateLimitError with scope="partner"
- Tenant inherits partner RPM when tenant has no explicit limit
- Legacy tenant (no partner_id) skips partner check entirely
- Partner blocked skips tenant check (waterfall short-circuit)
- Partner with no RPM limit set skips partner RPM check

#### Integration Tests (`test_partner_flow.py`)

Partner lifecycle end-to-end:

1. **Super Admin Creates Partner** - Partner entity with slug, status, rate limits
2. **Super Admin Lists Partners** - List all partners
3. **Super Admin Gets Partner** - Get by ID
4. **Super Admin Updates Partner** - Update name, rate limits
5. **Super Admin Generates Partner API Key** - `pk-agent-*` key returned
6. **Full Partner-to-Tenant Flow** - Create partner → key → partner creates tenant → lists own tenants
7. **Partner Key Revocation** - Revoke partner API key
8. **Duplicate Slug Rejected** - Unique slug enforcement

#### Integration Tests (`test_partner_isolation.py`)

Cross-partner isolation:

1. **Partner A Cannot See Partner B Tenants** - List returns only own tenants
2. **Partner A Cannot Get Partner B Tenant** - Returns 403 on cross-partner access
3. **Platform Owner Sees All Tenants** - Super admin has full visibility
4. **Platform Owner Filter by Partner** - Filter tenants by partner_id
5. **Unauthenticated Request Rejected** - Returns 401

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

- **`api_base_url`** - API base URL (default: `http://localhost:8000`)
- **`master_admin_key`** - Master admin key from env
- **`http_client`** - Async HTTP client for API requests
- **`admin_client`** - HTTP client with master admin auth
- **`tenant_data`** - Sample tenant data (randomized slug)
- **`user_data`** - Sample user data (randomized external_id, email)
- **`partner_data`** - Sample partner data (randomized slug, contact_email)
- **`partner_client`** - HTTP client with placeholder partner API key

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
