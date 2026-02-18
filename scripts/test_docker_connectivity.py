#!/usr/bin/env python3
"""Test script to verify Docker container can reach external APIs (OpenAI, Anthropic)."""

import asyncio
import socket
import sys
from typing import Any

import httpx


async def test_dns_resolution(hostname: str) -> tuple[bool, str]:
    """Test DNS resolution for a hostname."""
    try:
        ip = socket.gethostbyname(hostname)
        return True, ip
    except socket.gaierror as e:
        return False, str(e)


async def test_http_connectivity(url: str, timeout: int = 10) -> tuple[bool, str, int | None]:
    """Test HTTP connectivity to a URL."""
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            # Use HEAD request to avoid downloading full response
            response = await client.head(url, follow_redirects=True)
            return True, f"HTTP {response.status_code}", response.status_code
    except httpx.TimeoutException:
        return False, "Timeout", None
    except httpx.ConnectError as e:
        return False, f"Connection error: {e}", None
    except Exception as e:
        return False, f"Error: {e}", None


async def main() -> None:
    """Run connectivity tests."""
    print("=" * 60)
    print("Docker Container External Connectivity Test")
    print("=" * 60)
    print()

    # Test DNS resolution
    print("Testing DNS Resolution:")
    print("-" * 60)
    test_hosts = [
        "api.openai.com",
        "api.anthropic.com",
        "google.com",  # Control test
    ]

    dns_results: dict[str, tuple[bool, str]] = {}
    for host in test_hosts:
        success, result = await test_dns_resolution(host)
        dns_results[host] = (success, result)
        status = "✓" if success else "✗"
        print(f"{status} {host}: {result}")

    print()

    # Test HTTP connectivity
    print("Testing HTTP Connectivity:")
    print("-" * 60)
    test_urls = [
        ("OpenAI API", "https://api.openai.com/v1/models"),
        ("Anthropic API", "https://api.anthropic.com/v1/messages"),
        ("Google", "https://www.google.com"),  # Control test
    ]

    http_results: dict[str, tuple[bool, str, int | None]] = {}
    for name, url in test_urls:
        success, result, status_code = await test_http_connectivity(url)
        http_results[name] = (success, result, status_code)
        status = "✓" if success else "✗"
        print(f"{status} {name} ({url}): {result}")

    print()
    print("=" * 60)
    print("Summary:")
    print("-" * 60)

    # Check if DNS is working
    dns_ok = all(result[0] for result in dns_results.values())
    if not dns_ok:
        print("✗ DNS Resolution: FAILED")
        print("  → Check DNS configuration in docker-compose.yml")
        print("  → Verify Docker daemon DNS settings")
    else:
        print("✓ DNS Resolution: OK")

    # Check if HTTP connectivity is working
    http_ok = all(result[0] for result in http_results.values())
    if not http_ok:
        print("✗ HTTP Connectivity: FAILED")
        print("  → Check firewall rules")
        print("  → Verify network configuration")
        print("  → Check for proxy settings")
    else:
        print("✓ HTTP Connectivity: OK")

    # Specific API tests
    openai_ok = http_results.get("OpenAI API", (False, "", None))[0]
    anthropic_ok = http_results.get("Anthropic API", (False, "", None))[0]

    if not openai_ok:
        print("✗ OpenAI API: NOT REACHABLE")
    else:
        print("✓ OpenAI API: REACHABLE")

    if not anthropic_ok:
        print("✗ Anthropic API: NOT REACHABLE")
    else:
        print("✓ Anthropic API: REACHABLE")

    print("=" * 60)

    # Exit with error code if critical tests failed
    if not dns_ok or not openai_ok or not anthropic_ok:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
