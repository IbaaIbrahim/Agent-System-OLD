# Stream-Edge One-Time Token (OTT) Security

**Status**: Implemented
**Date**: 2024-01-06
**Related Phase**: Phase 3.5 (Security Enhancement)

---

## Problem Statement

Prior to this enhancement, the Stream-Edge SSE endpoint was open to anyone with a `job_id`:

```
GET /api/v1/events/{job_id}  # INSECURE — no authentication
```

**Vulnerability**: Any client who knew or guessed a `job_id` could connect to the SSE stream and receive all agent events for that job, including potentially sensitive information (user messages, tool results, internal state).

This violated the principle of defense-in-depth — while job creation required authentication, job streaming did not.

---

## Solution: One-Time Tokens (OTT)

Stream-Edge now requires a **short-lived, single-use JWT token** to establish SSE connections.

### Token Lifecycle

1. **Creation** (API Gateway):
   - When chat completion is created, API Gateway generates an OTT signed with `internal_jwt_secret`
   - OTT contains: `job_id`, `tenant_id`, `user_id`, `partner_id`, `jti` (nonce), `exp` (60s TTL)
   - Response includes both `stream_url` (with embedded token) and `stream_token` (explicit)

2. **Validation** (Stream-Edge):
   - Client connects: `GET /api/v1/stream?token={ott}`
   - Stream-Edge verifies JWT signature and expiry
   - Checks Redis: `SETNX ott:{jti}` to enforce one-time use
   - If consumed AND no `Last-Event-ID` header → reject (401)
   - If consumed AND `Last-Event-ID` present → allow (browser reconnect)

3. **Expiration**:
   - Token expires after 60 seconds (configurable via `ott_ttl_seconds`)
   - Redis key auto-expires after TTL
   - Prevents token reuse for new connections

### Browser Reconnection Handling

**Edge case**: Modern browsers automatically reconnect SSE streams on disconnect, sending the `Last-Event-ID` header.

**Solution**: If token is already consumed BUT `Last-Event-ID` header is present, skip one-time check and allow connection. This preserves the native SSE reconnection behavior while maintaining security for initial connections.

---

## Implementation

### Files Modified

| File | Changes |
|------|---------|
| `libs/common/config.py` | Added `ott_ttl_seconds` field (default 60s, range 10-300s) |
| `libs/common/auth.py` | Added `StreamOTTPayload`, `create_stream_ott()`, `verify_stream_ott()` |
| `libs/common/__init__.py` | Exported new OTT functions |
| `services/api-gateway/src/routers/chat.py` | Generate OTT in response, embed in `stream_url`, add `stream_token` field |
| `services/stream-edge/src/routers/events.py` | New `GET /stream?token=` endpoint with OTT validation + Redis one-time check |
| `frontend/apps/existing-client/src/services/api.ts` | Added `stream_token` to `ChatCompletionResponse` interface |

### Files Created

| File | Purpose |
|------|---------|
| `tests/unit/test_stream_ott.py` | 8 unit tests covering OTT creation, verification, expiry, tampering, wrong purpose |

### Token Structure

```json
{
  "purpose": "stream_ott",
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "tenant_id": "550e8400-e29b-41d4-a716-446655440001",
  "user_id": "550e8400-e29b-41d4-a716-446655440002",
  "partner_id": "550e8400-e29b-41d4-a716-446655440003",
  "jti": "abc123_random_nonce",
  "iat": 1704499200,
  "exp": 1704499260
}
```

**Signing**: HMAC-SHA256 with `internal_jwt_secret` (same secret as internal transaction tokens)

---

## API Changes

### Chat Completion Response (API Gateway)

**Before**:
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "stream_url": "http://localhost:8001/api/v1/events/550e8400-e29b-41d4-a716-446655440000",
  "status": "pending",
  "created_at": "2024-01-06T12:00:00Z"
}
```

**After**:
```json
{
  "job_id": "550e8400-e29b-41d4-a716-446655440000",
  "stream_url": "http://localhost:8001/api/v1/stream?token=eyJhbGc...",
  "stream_token": "eyJhbGc...",
  "status": "pending",
  "created_at": "2024-01-06T12:00:00Z"
}
```

### Stream Endpoint (Stream-Edge)

**New (Authenticated)**:
```
GET /api/v1/stream?token={ott}
```

**Old (Deprecated)**:
```
GET /api/v1/events/{job_id}  # Still works but logs deprecation warning
```

The old endpoint remains for backward compatibility but should be removed in a future release.

---

## Security Properties

| Property | Enforcement |
|----------|-------------|
| **Authentication** | JWT signature verified against `internal_jwt_secret` |
| **Authorization** | Token scoped to specific `job_id`, `tenant_id`, `user_id` |
| **Expiration** | 60-second TTL prevents long-term token reuse |
| **Single-use** | Redis SETNX ensures token used only once per initial connection |
| **Replay protection** | Consumed tokens cannot establish new streams |
| **Browser reconnect** | `Last-Event-ID` header allows legitimate reconnections |

---

## Configuration

Environment variables:

```bash
# .env
INTERNAL_JWT_SECRET=your-secret-key-at-least-32-chars
OTT_TTL_SECONDS=60  # Optional, defaults to 60
```

**Production recommendation**: Use a strong, randomly-generated secret (32+ characters). Rotate `INTERNAL_JWT_SECRET` periodically (requires coordinated restart of API Gateway and Stream-Edge).

---

## Testing

**Unit tests** (`tests/unit/test_stream_ott.py`):
- ✅ `test_create_ott_returns_jwt` — verify token structure and claims
- ✅ `test_create_ott_optional_fields_none` — user_id/partner_id nullable
- ✅ `test_ott_jti_uniqueness` — each token has unique nonce
- ✅ `test_ott_expiry_within_ttl` — expiry matches configured TTL
- ✅ `test_verify_ott_valid` — roundtrip create → verify
- ✅ `test_verify_ott_expired` — reject expired tokens
- ✅ `test_verify_ott_wrong_purpose` — reject tokens with wrong purpose claim
- ✅ `test_verify_ott_tampered` — reject tokens signed with wrong secret

**Manual testing**:
```bash
# Create a job
curl -X POST http://localhost:8000/api/v1/chat/completions \
  -H "Authorization: Bearer sk-agent-..." \
  -H "Content-Type: application/json" \
  -d '{"messages": [{"role": "user", "content": "Hello"}]}'

# Extract stream_url from response
# Connect to SSE stream (first connection — should succeed)
curl -N "http://localhost:8001/api/v1/stream?token=eyJhbGc..."

# Try to reuse token (second connection — should fail with 401)
curl -N "http://localhost:8001/api/v1/stream?token=eyJhbGc..."
```

---

## Future Enhancements

1. **Token rotation**: Allow clients to request a new OTT before the current one expires (for very long-running streams)
2. **Scope restrictions**: Add `allowed_event_types` claim to limit what events a token can access
3. **Audit logging**: Log all OTT validations (success/failure) for security monitoring
4. **Rate limiting**: Per-token connection attempt rate limiting to prevent token brute-forcing

---

## References

- JWT Best Practices: https://datatracker.ietf.org/doc/html/rfc8725
- SSE Reconnection: https://html.spec.whatwg.org/multipage/server-sent-events.html#the-last-event-id-header
- Redis SETNX: https://redis.io/commands/setnx/
