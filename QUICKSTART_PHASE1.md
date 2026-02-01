# Phase 1 Quick Start Guide

## 🚀 Getting Started

### 1. Set Up Environment

Copy and update the environment file:
```bash
cp .env.example .env
```

**Important:** Update these values in `.env`:
```bash
MASTER_ADMIN_KEY=your-secure-random-key-min-32-characters
INTERNAL_JWT_SECRET=another-secure-random-key-min-32-characters
JWT_SECRET=yet-another-secure-random-key-min-32-chars
```

### 2. Start Infrastructure

```bash
# Start PostgreSQL, Redis, Kafka
make infra

# Run database migrations
make migrate
```

### 3. Start API Gateway

```bash
# In a separate terminal
make api
```

The API will be available at `http://localhost:8000`

### 4. Test Phase 1

```bash
# Run Phase 1 integration tests
make test-phase1

# Or run all integration tests
make test-int

# Alternative: Run standalone test script with colored output
python scripts/test_phase1_auth.py
```

---

## 📖 API Usage Examples

### 1. Create a Tenant (Platform Owner)

```bash
curl -X POST http://localhost:8000/api/admin/tenants \
  -H "Authorization: Bearer ${MASTER_ADMIN_KEY}" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Acme Corp",
    "slug": "acme-corp",
    "rate_limit_rpm": 100,
    "rate_limit_tpm": 10000
  }'
```

Response:
```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "name": "Acme Corp",
  "slug": "acme-corp",
  "status": "active",
  "rate_limit_rpm": 100,
  "rate_limit_tpm": 10000,
  ...
}
```

### 2. Generate API Key for Tenant

```bash
curl -X POST http://localhost:8000/api/admin/tenants/{tenant_id}/api-keys \
  -H "Authorization: Bearer ${MASTER_ADMIN_KEY}" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Production API Key",
    "scopes": ["*"]
  }'
```

Response:
```json
{
  "api_key": "sk-agent-abc123...",  // ⚠️ Save this! Shown only once
  "key_info": {
    "id": "...",
    "tenant_id": "...",
    "name": "Production API Key",
    "scopes": ["*"],
    ...
  }
}
```

### 3. Create Virtual User (Tenant)

```bash
TENANT_API_KEY="sk-agent-abc123..."

curl -X POST http://localhost:8000/api/v1/users \
  -H "Authorization: Bearer ${TENANT_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{
    "external_id": "user_12345",
    "email": "user@example.com",
    "name": "John Doe",
    "role": "member",
    "custom_rpm_limit": 50
  }'
```

Response:
```json
{
  "id": "...",
  "tenant_id": "...",
  "external_id": "user_12345",
  "email": "user@example.com",
  "name": "John Doe",
  "role": "member",
  "custom_rpm_limit": 50,
  ...
}
```

### 4. Exchange Token for JWT

```bash
curl -X POST http://localhost:8000/api/v1/auth/token \
  -H "Authorization: Bearer ${TENANT_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "user_12345"
  }'
```

Response:
```json
{
  "access_token": "eyJhbGc...",
  "token_type": "Bearer",
  "expires_in": 3600,
  "user_id": "...",
  "tenant_id": "...",
  "scopes": ["job:create", "stream:read"]
}
```

### 5. Use JWT to Access Protected Endpoints

```bash
USER_JWT="eyJhbGc..."

curl -X GET http://localhost:8000/api/v1/users \
  -H "Authorization: Bearer ${USER_JWT}"
```

---

## 🔐 Authentication Flow

```
┌─────────────────────────────────────────────────────────┐
│ Platform Owner (Master Admin)                           │
│   → Uses: MASTER_ADMIN_KEY                             │
│   → Can: Create tenants, manage platform               │
└─────────────────────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────┐
│ Tenant Backend (API Key)                                │
│   → Uses: sk-agent-xxx (from admin)                    │
│   → Can: Create users, manage tenant resources         │
└─────────────────────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────┐
│ End User (JWT Token)                                    │
│   → Uses: JWT from token exchange                      │
│   → Can: Create jobs, stream events                    │
└─────────────────────────────────────────────────────────┘
```

---

## 🛠️ Development Commands

```bash
# Infrastructure
make infra              # Start postgres, redis, kafka
make up                 # Start all services
make down               # Stop all services
make clean              # Remove containers, volumes

# API Gateway
make api                # Run API Gateway (dev mode)

# Database
make migrate            # Run migrations
make migrate-new        # Create new migration
make migrate-reset      # Reset database
make shell-db           # Open psql shell

# Testing
make test               # Run all tests
make test-unit          # Unit tests only
make test-int           # Integration tests
```

---

## 📊 API Documentation

When running in debug mode, interactive API docs are available at:
- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`

---

## 🔍 Troubleshooting

### API Gateway won't start
```bash
# Check if port 8000 is in use
lsof -i :8000

# Check database connection
make shell-db
```

### Authentication errors
```bash
# Verify environment variables
grep "MASTER_ADMIN_KEY" .env

# Check migration status
make shell-db
# In psql: \dt tenants.*
```

### Database migration errors
```bash
# Reset and re-run migrations
make migrate-reset
```

---

## 📚 Next Steps

1. **Test the APIs** using the examples above
2. **Review the test script** at `scripts/test_phase1_auth.py`
3. **Read the completion doc** at `docs/phase1_completion.md`
4. **Prepare for Phase 2** - Billing & Enhanced Rate Limiting

---

## 🎯 Key Files Reference

| Component | File |
|-----------|------|
| Admin Endpoints | [services/api-gateway/src/routers/admin.py](services/api-gateway/src/routers/admin.py) |
| User Endpoints | [services/api-gateway/src/routers/users.py](services/api-gateway/src/routers/users.py) |
| Auth Endpoints | [services/api-gateway/src/routers/auth.py](services/api-gateway/src/routers/auth.py) |
| API Key Cache | [services/api-gateway/src/services/api_key_cache.py](services/api-gateway/src/services/api_key_cache.py) |
| Auth Middleware | [services/api-gateway/src/middleware/auth.py](services/api-gateway/src/middleware/auth.py) |
| User Model | [libs/db/models.py](libs/db/models.py) |
| Config | [libs/common/config.py](libs/common/config.py) |
| Test Script | [scripts/test_phase1_auth.py](scripts/test_phase1_auth.py) |

---

**Ready to start?** Run `make infra && make migrate && make api` then visit http://localhost:8000/docs
