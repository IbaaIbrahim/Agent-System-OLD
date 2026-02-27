FROM python:3.11-slim

# Prevents Python from buffering stdout/stderr (useful for container logs)
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install dependencies
COPY services/auth-broker/requirements.txt .
RUN apt-get update && apt-get install -y --no-install-recommends gcc build-essential \
    && pip install --no-cache-dir -r requirements.txt \
    && apt-get remove -y gcc build-essential \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

# Copy service code
COPY services/auth-broker /app

# Expose the port (docker-compose controls host mapping)
EXPOSE 7003

# Default command runs the Python entrypoint which uses PORT env var
CMD ["python", "main.py"]

