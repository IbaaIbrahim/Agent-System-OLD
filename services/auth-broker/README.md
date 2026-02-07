# Auth Broker

A minimal FastAPI application for testing the Flowdit AI Ecosystem authentication flow. It acts as a token broker that uses a hardcoded API key to fetch access tokens from the gateway on behalf of client applications.

## Purpose

This is a **prototype/testing-only** service that:
- Holds a static API key internally
- Fetches fresh access tokens from the Flowdit gateway on each request
- Returns tokens to client applications for use with gateway APIs

In a real application, the chat library would call this broker to get tokens instead of managing API keys directly.

## Quick Start

### Prerequisites
- Python 3.10+
- [Hatch](https://hatch.pypa.io/) (for project management)

### Setup

```bash
# Navigate to the auth-broker directory
cd auth-broker

# Create the Hatch environment (one-time)
hatch env create
```

### Run

```bash
# Option 1: Using Hatch
hatch run python main.py

# Option 2: Using the run script
./run.sh

# Option 3: Using the script entry point
hatch run auth-broker
```

The app will start on the port specified in `.env` (default: **8001**).

## Configuration

Edit [.env](.env) to configure:

```env
GATEWAY_URL=http://localhost:8000  # Gateway base URL
API_KEY=test-api-key-for-testing   # Hardcoded API key
PORT=8001                           # Port to listen on
```

## API Endpoints

### Health Check

```bash
GET /health
```

**Response:**
```json
{
  "status": "ok"
}
```

### Request Token

```bash
POST /request-token
```

**Request Body:** (empty or any JSON)

**Response (on success):**
```json
{
  "access_token": "eyJhbGc...",
  "token_type": "bearer",
  "expires_in": 3600,
  "expires_at": "2026-01-25T14:30:00Z"
}
```

**Response (on error):**
```json
{
  "error": "Failed to get token from gateway",
  "detail": "Connection refused"
}
```

## Example Usage

### Check Health

```bash
curl http://localhost:8001/health
```

### Get Access Token

```bash
curl -X POST http://localhost:8001/request-token
```

### Use Token with Gateway

```bash
# Get token from auth-broker
TOKEN=$(curl -s -X POST http://localhost:8001/request-token | jq -r '.access_token')

# Use token with gateway
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/v1/jobs
```

## How It Works

1. Client application calls `POST /request-token` to auth-broker
2. Auth-broker calls `POST /v1/auth/token_from_api_key` on gateway with hardcoded API key header
3. Gateway validates API key and returns access token
4. Auth-broker returns token to client application
5. Client application uses token for subsequent gateway requests

## Development

### Project Structure

```
auth-broker/
├── main.py              # FastAPI application
├── pyproject.toml       # Hatch/project configuration
├── requirements.txt     # Legacy dependency list
├── .env                 # Configuration (hardcoded API key)
├── run.sh               # Startup script
└── README.md            # This file
```

### Install/Update Dependencies

```bash
# Hatch automatically manages dependencies from pyproject.toml
hatch env create
```

### Run with Auto-reload (Development)

```bash
hatch run uvicorn main:app --reload --port 8001
```

## Notes

- **No validation:** This is a prototype—input validation is minimal
- **Fresh tokens only:** Tokens are fetched on every request (no caching)
- **No error recovery:** Errors from the gateway are passed through as-is
- **Static API key:** The API key is hardcoded in `.env` for testing purposes only

## Integration with Chat Library

Once deployed, client applications would:

1. Know the auth-broker URL (e.g., `http://localhost:8001`)
2. Call `POST /request-token` before making gateway requests
3. Extract and use the `access_token` in the `Authorization` header

Example (pseudo-code):

```javascript
const brokerUrl = "http://localhost:8001";
const gatewayUrl = "http://localhost:8000";

// Get token from auth-broker
const response = await fetch(`${brokerUrl}/request-token`, {
  method: "POST"
});
const { access_token } = await response.json();

// Use token with gateway
const jobResponse = await fetch(`${gatewayUrl}/v1/jobs`, {
  headers: { Authorization: `Bearer ${access_token}` }
});
```
