# WebSocket Gateway Dockerfile
FROM python:3.12-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy and install requirements
# NOTE: Uses service-specific requirements. If Docker DNS fails during build,
# temporarily switch to "COPY requirements.txt ." to use cached root requirements
# (the lazy libs/messaging/__init__.py ensures missing deps won't crash at import).
COPY services/websocket-gateway/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy shared libraries
COPY libs/ /app/libs/

# Copy service code (rename to valid Python module name)
COPY services/websocket-gateway/ /app/services/websocket_gateway/

# Set Python path
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8002/health || exit 1

# Run the service
EXPOSE 8002
CMD ["uvicorn", "services.websocket_gateway.src.main:app", "--host", "0.0.0.0", "--port", "8002"]
