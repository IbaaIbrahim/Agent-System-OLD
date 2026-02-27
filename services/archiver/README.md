# Archiver Service

Write-behind service that moves agent events from Redis (hot storage) to PostgreSQL (cold storage), ensuring durable persistence of conversation history and job lifecycle data.

## What It Does

The archiver continuously reads event streams from Redis and persists them to PostgreSQL in batches. It handles two categories of events:

**Conversational events** are written to the `chat_messages` table:
- `message` — Full assistant/user messages with token counts
- `tool_call` — LLM tool invocation requests
- `tool_result` — Tool execution results
- `delta` events are streaming-only and deliberately skipped

**Lifecycle events** update the `jobs` table:
- `start` — Sets job status to RUNNING
- `complete` — Sets COMPLETED with token totals
- `error` — Sets FAILED with error details in metadata
- `cancelled` — Sets CANCELLED
- `suspended` — Records suspension info (pending tools, snapshot sequence)

## When It Runs

The archiver runs as a long-lived background process alongside the other services. It performs three activities on different cadences:

| Activity | Frequency | Description |
|----------|-----------|-------------|
| Stream reading | Continuous (~100ms loop) | Discovers `events:*` Redis streams via SCAN, reads entries using consumer groups |
| Buffer flushing | Every 5s or 100 events | Whichever threshold is hit first triggers a batch write to PostgreSQL |
| Stream cleanup | Every 1 hour | Deletes Redis streams whose last entry is older than 24 hours |

## How It Works

```
Redis streams (events:{job_id})
        │
        ▼
  RedisStreamReader          ← discovers streams, reads via consumer groups, ACKs entries
        │
        ▼
  PostgresWriter (buffer)    ← batches events in memory, flushes on size/time threshold
        │
        ▼
  PostgreSQL (jobs schema)   ← chat_messages table + jobs table updates
```

- **Consumer groups** ensure each event is processed exactly once, even with multiple archiver instances
- **Retry on failure** — if a batch write fails, events are returned to the buffer for the next flush
- **Graceful shutdown** — on SIGTERM/SIGINT, stops reading, flushes remaining buffer, then exits

## Configuration

All settings are via environment variables (inherits from base `Settings`):

| Variable | Default | Description |
|----------|---------|-------------|
| `CONSUMER_GROUP` | `archiver` | Redis consumer group name |
| `BATCH_SIZE` | `100` | Max events per flush |
| `FLUSH_INTERVAL` | `5` | Seconds between periodic flushes |
| `STREAM_RETENTION_HOURS` | `24` | Hours before old streams are deleted |
| `CLEANUP_INTERVAL` | `3600` | Seconds between cleanup runs |

## Running

```bash
make archiver
```
