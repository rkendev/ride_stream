"""Integration test fixtures for RideStream v2.

Connects to REAL Docker containers (rs2-kafka, rs2-minio, rs2-trino, etc.).
Requires Docker stack to be running: `make docker-up` before running tests.
"""

from __future__ import annotations

import json
import subprocess

import boto3
import pytest
from kafka import KafkaConsumer, KafkaProducer

# ── Constants ─────────────────────────────────────────────

KAFKA_BOOTSTRAP = "localhost:9092"
KAFKA_TOPIC = "ride.events.raw"
KAFKA_DLQ_TOPIC = "ride.events.dlq"
MINIO_ENDPOINT = "http://localhost:9000"
MINIO_ACCESS_KEY = "minioadmin"
MINIO_SECRET_KEY = "minioadmin"
TRINO_HOST = "localhost"
TRINO_PORT = 8888
BRONZE_BUCKET = "bronze"


# ── Docker Stack Health ───────────────────────────────────


def _check_container(name: str) -> bool:
    """Check if a Docker container is healthy."""
    try:
        result = subprocess.run(
            ["docker", "inspect", name, "--format", "{{.State.Health.Status}}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return "healthy" in result.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


@pytest.fixture(scope="session", autouse=True)
def docker_stack_healthy():
    """Verify Docker stack is running and healthy before integration tests."""
    required = ["rs2-kafka", "rs2-minio", "rs2-trino", "rs2-hive-metastore"]
    for container in required:
        if not _check_container(container):
            pytest.skip(f"Docker container {container} not healthy. Run 'make docker-up' first.")


# ── Kafka Fixtures ────────────────────────────────────────


@pytest.fixture(scope="session")
def kafka_producer():
    """Kafka producer connected to rs2-kafka."""
    producer = KafkaProducer(
        bootstrap_servers=KAFKA_BOOTSTRAP,
        value_serializer=lambda v: json.dumps(v).encode("utf-8"),
        key_serializer=lambda k: k.encode("utf-8") if k else None,
        acks="all",
        request_timeout_ms=10000,
    )
    yield producer
    producer.flush()
    producer.close()


@pytest.fixture
def kafka_consumer_factory():
    """Factory that creates Kafka consumers for specific topics."""
    consumers: list[KafkaConsumer] = []

    def _create(topic: str, group_id: str = "integration-test") -> KafkaConsumer:
        consumer = KafkaConsumer(
            topic,
            bootstrap_servers=KAFKA_BOOTSTRAP,
            group_id=group_id,
            auto_offset_reset="earliest",
            value_deserializer=lambda v: json.loads(v.decode("utf-8")),
            consumer_timeout_ms=5000,
        )
        consumers.append(consumer)
        return consumer

    yield _create

    for c in consumers:
        c.close()


# ── MinIO/S3 Fixtures ────────────────────────────────────


@pytest.fixture(scope="session")
def s3_client():
    """S3 client connected to rs2-minio."""
    return boto3.client(
        "s3",
        endpoint_url=MINIO_ENDPOINT,
        aws_access_key_id=MINIO_ACCESS_KEY,
        aws_secret_access_key=MINIO_SECRET_KEY,
        region_name="us-east-1",
    )


# ── Trino Fixtures ────────────────────────────────────────


@pytest.fixture(scope="session")
def trino_engine():
    """Trino query engine connected to rs2-trino."""
    from ridestream.adapters.local.trino_query_engine import TrinoQueryEngine

    return TrinoQueryEngine(
        host=TRINO_HOST,
        port=TRINO_PORT,
        user="trino",
        catalog="hive",
        schema="default",
    )
