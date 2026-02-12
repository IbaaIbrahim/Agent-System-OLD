"""WebSocket Gateway configuration."""

from functools import lru_cache

from pydantic import Field

from libs.common.config import Settings


class WebSocketGatewayConfig(Settings):
    """WebSocket Gateway specific configuration."""

    # WebSocket settings
    websocket_gateway_port: int = Field(default=8002, ge=1, le=65535)

    # Connection limits
    max_connections_per_tenant: int = Field(default=50, ge=1)
    max_connections_per_session: int = Field(default=3, ge=1)

    # Heartbeat / keepalive
    ws_ping_interval: int = Field(default=20, description="Seconds between pings")
    ws_ping_timeout: int = Field(default=10, description="Seconds to wait for pong")

    # Message limits
    max_message_size_bytes: int = Field(
        default=5 * 1024 * 1024,  # 5MB (for screen frames)
        description="Max WebSocket message size",
    )

    # Live Session Manager URL (internal)
    live_session_manager_url: str = Field(
        default="http://localhost:8003",
        description="URL for the Live Session Manager service",
    )


@lru_cache
def get_config() -> WebSocketGatewayConfig:
    """Get cached WebSocket Gateway configuration."""
    return WebSocketGatewayConfig()
