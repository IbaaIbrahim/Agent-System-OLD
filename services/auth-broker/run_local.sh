#!/bin/bash

# Load environment variables from .env
export $(cat .env | xargs)

# Start the auth-broker FastAPI app
echo "Starting Auth Broker on port ${PORT:-8001}..."
python main.py -r
