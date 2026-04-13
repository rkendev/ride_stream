"""Configuration management for RideStream v2.

Loads settings from environment variables via pydantic-settings.
CloudProvider enum selects local vs AWS adapters.
"""

from __future__ import annotations

import logging
import sys
from enum import StrEnum

from pydantic import Field
from pydantic_settings import BaseSettings


class Environment(StrEnum):
    """Deployment environment."""

    LOCAL = "local"
    STAGING = "staging"
    PRODUCTION = "production"


class LogLevel(StrEnum):
    """Log level options."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class Config(BaseSettings):
    """Application configuration loaded from environment variables.

    All settings have sensible defaults for local development.
    Production values come from AWS Secrets Manager or env vars.
    """

    # Environment
    environment: Environment = Environment.LOCAL
    aws_region: str = "us-east-1"
    aws_profile: str | None = None

    # Kafka
    kafka_bootstrap_servers: str = "localhost:9092"

    # MinIO / S3
    minio_endpoint: str = "http://localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"

    # Hive Metastore
    hive_metastore_uri: str = "thrift://localhost:9083"

    # Trino
    trino_host: str = "localhost"
    trino_port: int = 8888
    trino_user: str = "trino"

    # dbt
    dbt_target: str = "trino_local"
    dbt_profiles_dir: str = "./dbt"

    # Logging
    log_level: LogLevel = LogLevel.INFO
    log_format: str = Field(default="text", pattern=r"^(text|json)$")

    model_config = {
        "env_prefix": "",
        "env_file": ".env.local",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }

    @property
    def is_local(self) -> bool:
        """Whether running in local development mode."""
        return self.environment == Environment.LOCAL

    @property
    def is_production(self) -> bool:
        """Whether running in production."""
        return self.environment == Environment.PRODUCTION


def setup_logging(config: Config) -> logging.Logger:
    """Configure structured logging based on config.

    Args:
        config: Application configuration.

    Returns:
        Configured root logger.
    """
    logger = logging.getLogger("ridestream")
    logger.setLevel(config.log_level.value)

    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(config.log_level.value)

        if config.log_format == "json":
            formatter = logging.Formatter(
                '{"timestamp":"%(asctime)s","level":"%(levelname)s",'
                '"logger":"%(name)s","message":"%(message)s"}'
            )
        else:
            formatter = logging.Formatter(
                "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )

        handler.setFormatter(formatter)
        logger.addHandler(handler)

    return logger
