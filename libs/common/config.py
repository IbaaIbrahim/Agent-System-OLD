"""Configuration management using Pydantic Settings."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Environment
    environment: Literal["development", "staging", "production"] = "development"
    debug: bool = False

    # Database
    database_url: str = Field(
        default="postgresql+asyncpg://agent:agent_secret@localhost:5432/agent_db",
        description="PostgreSQL connection URL",
    )
    db_pool_size: int = Field(default=10, ge=1, le=100)
    db_max_overflow: int = Field(default=20, ge=0, le=100)
    db_pool_timeout: int = Field(default=30, ge=1)

    # Redis
    redis_url: str = Field(
        default="redis://localhost:6379/0",
        description="Redis connection URL",
    )
    redis_pool_size: int = Field(default=10, ge=1, le=100)

    # Kafka
    kafka_bootstrap_servers: str = Field(
        default="localhost:9092",
        description="Kafka bootstrap servers",
    )
    kafka_consumer_group: str = Field(default="agent-system")

    # LLM Providers
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    default_llm_provider: Literal["anthropic", "openai"] = "anthropic"
    anthropic_default_model: str = "claude-sonnet-4-20250514"
    openai_default_model: str = "gpt-4-turbo-preview"
    llm_timeout: int = Field(default=120, ge=10, description="Timeout for LLM provider calls in seconds")

    # Authentication
    jwt_secret: str = Field(
        default="change_in_production",
        min_length=16,
        description="Secret key for user JWT tokens",
    )
    jwt_algorithm: str = "HS256"
    jwt_expiration: int = Field(default=3600, ge=60)

    # Platform Admin Authentication
    master_admin_key: str = Field(
        default="change_in_production_use_long_random_string",
        min_length=32,
        description="Master admin key for platform owner (create tenants, manage system)",
    )

    # Internal Service Authentication
    internal_jwt_secret: str = Field(
        default="change_in_production_internal_secret_different_from_jwt",
        min_length=32,
        description="Secret key for internal transaction tokens (Kafka payloads)",
    )

    # One-Time Token for Stream-Edge
    ott_ttl_seconds: int = Field(
        default=60,
        ge=10,
        le=300,
        description="TTL in seconds for stream one-time tokens",
    )

    # One-Time Token for file download links (openable URLs)
    file_download_ott_ttl_seconds: int = Field(
        default=300,
        ge=60,
        le=3600,
        description="TTL in seconds for file download one-time tokens",
    )

    # Rate Limiting
    rate_limit_rpm: int = Field(default=60, ge=1)
    rate_limit_tpm: int = Field(default=100000, ge=1)

    # Service Ports
    api_gateway_port: int = Field(default=8000, ge=1, le=65535)
    stream_edge_port: int = Field(default=8001, ge=1, le=65535)
    websocket_gateway_port: int = Field(default=8002, ge=1, le=65535)

    # File storage persistence
    file_storage_persist: bool = False  # Write uploaded files to disk
    file_storage_path: str = "./file_uploads"  # Directory for persistent files

    # Knowledge Base / Vector Search
    milvus_host: str = Field(default="localhost", description="Milvus server host")
    milvus_port: int = Field(default=19530, ge=1, le=65535)
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = Field(default=1536, ge=128, le=3072)
    kb_max_content_length: int = Field(default=50000, ge=1000)

    # Logging
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    log_format: Literal["json", "text"] = "json"

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, v: str) -> str:
        if not v.startswith(("postgresql://", "postgresql+asyncpg://")):
            raise ValueError("Database URL must be a PostgreSQL connection string")
        return v

    @field_validator("redis_url")
    @classmethod
    def validate_redis_url(cls, v: str) -> str:
        if not v.startswith("redis://"):
            raise ValueError("Redis URL must start with redis://")
        return v

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def is_development(self) -> bool:
        return self.environment == "development"


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
