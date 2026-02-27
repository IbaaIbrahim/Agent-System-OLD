# Windows Development Guide

This guide covers running the Agent System on Windows.

## Prerequisites

1. **Windows 10/11** with PowerShell or Command Prompt
2. **Docker Desktop** for Windows (required for infrastructure services)
3. **Python 3.12+** with virtual environment
4. **Node.js** (for frontend development)
5. **Make for Windows** - Download from [equinusocio/vcvarsall](https://github.com/equinusocio/vcvarsall) or use WSL

## Quick Start

### Option 1: Using dev.bat (Recommended for pure Windows)

```powershell
# Start infrastructure services
dev.bat up

# Run API Gateway (in separate terminal)
dev.bat api

# Run Stream Edge (in separate terminal)
dev.bat stream

# Run Orchestrator (in separate terminal)
dev.bat orchestrator

# Run Tool Workers (in separate terminal)
dev.bat workers

# Run Frontend (in separate terminal)
dev.bat frontend
```

### Option 2: Using Make on Windows

If you have Make installed on Windows:

```powershell
# Start infrastructure services
make up

# Run API Gateway (in separate terminal)
make api

# Run other services similarly
make stream
make orchestrator
make workers
make archiver
make frontend
```

### Option 3: Using WSL (Windows Subsystem for Linux)

If you have WSL with Ubuntu or another Linux distribution:

```powershell
# Start WSL and run commands
wsl make up
wsl make api
# etc.
```

## Common Commands

### Infrastructure

```powershell
# Using dev.bat
dev.bat up          # Start all Docker services
dev.bat down        # Stop all Docker services
dev.bat logs        # Tail logs from all services
dev.bat clean       # Remove containers, volumes, and images

# Using make
make up
make down
make logs
make clean
```

### Running Services

```powershell
# Using dev.bat
dev.bat api          # API Gateway (port 8000)
dev.bat stream       # Stream Edge (port 8001)
dev.bat orchestrator # Orchestrator
dev.bat workers      # Tool Workers
dev.bat archiver     # Archiver
dev.bat ws           # WebSocket Gateway (port 8002)
dev.bat live-session # Live Session Manager
dev.bat auth-broker  # Auth Broker
dev.bat frontend     # Frontend (port 3000)

# Using make
make api
make stream
make orchestrator
make workers
# etc.
```

### Database

```powershell
# Run migrations
dev.bat migrate

# Using make
make migrate
```

### Testing

```powershell
# Run all tests
dev.bat test

# Run unit tests only
dev.bat test-unit

# Run integration tests
dev.bat test-int

# Using make
make test
make test-unit
make test-int
```

### Code Quality

```powershell
# Run linters
dev.bat lint

# Format code
dev.bat format

# Run type checking
dev.bat typecheck

# Using make
make lint
make format
make typecheck
```

## Windows-Specific Notes

### Path Separators

Windows uses backslashes (`\`) for paths, while Linux uses forward slashes (`/`). The Makefile and dev.bat handle this automatically.

### Environment Variables

On Windows, environment variables are set differently:

```powershell
# Windows
set PYTHONPATH=%CD%

# Linux/macOS
export PYTHONPATH=$(PWD)
```

The Makefile and dev.bat handle this automatically.

### Shell Differences

- **Windows**: Uses `cmd.exe` or PowerShell
- **Linux/macOS**: Uses `bash` or `zsh`

The Makefile detects the OS and uses appropriate commands.

### File Watchers

For development with hot reload, you can install `watchfiles`:

```powershell
pip install watchfiles
```

This enables auto-reload for services like orchestrator, workers, and archiver.

## Troubleshooting

### Make not found on Windows

**Solution**: Use `dev.bat` instead, which is a native Windows batch script.

### PYTHONPATH errors

**Solution**: The Makefile and dev.bat set `PYTHONPATH` automatically. If you still see errors, try:

```powershell
# Using dev.bat (automatic)
dev.bat api

# Or manually
set PYTHONPATH=%CD%
uvicorn services.api-gateway.src.main:app --reload --port 8000 --host localhost
```

### Docker Desktop not running

**Solution**: Start Docker Desktop from the Start menu before running infrastructure commands.

### Port conflicts

If services fail to start due to port conflicts:

- **API Gateway**: Default port 8000
- **Stream Edge**: Default port 8001
- **WebSocket Gateway**: Default port 8002
- **Frontend**: Default port 3000

You can change these ports in the `.env` file by setting `API_HOST` or modifying the service configurations.

## Development Workflow

1. **Start infrastructure** (once):
   ```powershell
   dev.bat up
   ```

2. **Run migrations** (once or after schema changes):
   ```powershell
   dev.bat migrate
   ```

3. **Start services** (each in separate terminal):
   ```powershell
   # Terminal 1
   dev.bat api

   # Terminal 2
   dev.bat stream

   # Terminal 3
   dev.bat orchestrator

   # Terminal 4
   dev.bat workers

   # Terminal 5
   dev.bat archiver

   # Terminal 6 (for frontend)
   dev.bat frontend
   ```

4. **Stop infrastructure** (when done):
   ```powershell
   dev.bat down
   ```

## Additional Resources

- [Main README](./README.md) - General project documentation
- [CLAUDE.md](./CLAUDE.md) - Architecture and development guide
- [AGENTS.md](./AGENTS.md) - Agent system documentation
