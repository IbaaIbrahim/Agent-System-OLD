# Live Session Manager Dockerfile
FROM python:3.12-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for caching
COPY services/live-session-manager/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy shared libraries
COPY libs/ /app/libs/

# Copy service code (rename to valid Python module name)
COPY services/live-session-manager/ /app/services/live_session_manager/

# Set Python path
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8003/health || exit 1

# Run the service
EXPOSE 8003
CMD ["uvicorn", "services.live_session_manager.src.main:app", "--host", "0.0.0.0", "--port", "8003"]
