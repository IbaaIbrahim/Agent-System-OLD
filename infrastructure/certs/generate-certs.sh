#!/bin/bash
set -e

# Directory where certs will be stored
CERT_DIR="$(dirname "$0")"
KEY_FILE="$CERT_DIR/key.pem"
CERT_FILE="$CERT_DIR/cert.pem"

# Check if certs already exist
if [ -f "$KEY_FILE" ] && [ -f "$CERT_FILE" ]; then
    echo "Certificates already exist in $CERT_DIR"
    exit 0
fi

echo "Generating self-signed certificates in $CERT_DIR..."

# Generate a self-signed certificate
openssl req -x509 -newkey rsa:4096 -keyout "$KEY_FILE" -out "$CERT_FILE" \
    -days 365 -nodes -subj "/CN=localhost" \
    -addext "subjectAltName=DNS:localhost,DNS:api-gateway,IP:127.0.0.1"

echo "Certificates generated successfully."
chmod 644 "$CERT_FILE"
chmod 600 "$KEY_FILE"
