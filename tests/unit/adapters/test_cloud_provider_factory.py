"""Unit tests for CloudProvider factory."""

from __future__ import annotations

import pytest

from ridestream.adapters.cloud_provider_factory import CloudProviderFactory
from ridestream.adapters.local.kafka_event_bus import KafkaEventPublisher, KafkaEventStore
from ridestream.adapters.local.minio_storage import S3RideRepository
from ridestream.adapters.local.trino_query_engine import TrinoQueryEngine
from ridestream.config import Config, Environment

# ── Fixtures ──────────────────────────────────────────────


@pytest.fixture
def local_config() -> Config:
    return Config(
        environment=Environment.LOCAL,
        kafka_bootstrap_servers="localhost:9092",
        minio_endpoint="http://localhost:9000",
        trino_host="localhost",
        trino_port=8888,
    )


@pytest.fixture
def prod_config() -> Config:
    return Config(
        environment=Environment.PRODUCTION,
        kafka_bootstrap_servers="b-1.msk.kafka.us-east-1.amazonaws.com:9092",
        trino_host="trino-prod",
        trino_port=443,
    )


@pytest.fixture
def local_factory(local_config: Config) -> CloudProviderFactory:
    return CloudProviderFactory(local_config)


@pytest.fixture
def prod_factory(prod_config: Config) -> CloudProviderFactory:
    return CloudProviderFactory(prod_config)


# ── Local Environment ─────────────────────────────────────


class TestLocalFactory:
    def test_creates_kafka_publisher(self, local_factory: CloudProviderFactory) -> None:
        publisher = local_factory.create_event_publisher()
        assert isinstance(publisher, KafkaEventPublisher)
        assert publisher._bootstrap_servers == "localhost:9092"

    def test_creates_kafka_store(self, local_factory: CloudProviderFactory) -> None:
        store = local_factory.create_event_store()
        assert isinstance(store, KafkaEventStore)

    def test_creates_s3_repository(self, local_factory: CloudProviderFactory) -> None:
        repo = local_factory.create_ride_repository()
        assert isinstance(repo, S3RideRepository)
        assert repo._bucket == "bronze"

    def test_creates_trino_engine(self, local_factory: CloudProviderFactory) -> None:
        engine = local_factory.create_query_engine()
        assert isinstance(engine, TrinoQueryEngine)
        assert engine._host == "localhost"
        assert engine._port == 8888

    def test_creates_hive_catalog(self, local_factory: CloudProviderFactory) -> None:
        from ridestream.adapters.local.hive_catalog import HiveCatalogAdapter

        catalog = local_factory.create_catalog()
        assert isinstance(catalog, HiveCatalogAdapter)


# ── Production Environment ────────────────────────────────


class TestProdFactory:
    def test_creates_msk_publisher(self, prod_factory: CloudProviderFactory) -> None:
        from ridestream.adapters.aws.msk_event_bus import MSKEventPublisher

        publisher = prod_factory.create_event_publisher()
        assert isinstance(publisher, MSKEventPublisher)

    def test_creates_msk_store(self, prod_factory: CloudProviderFactory) -> None:
        from ridestream.adapters.aws.msk_event_bus import MSKEventStore

        store = prod_factory.create_event_store()
        assert isinstance(store, MSKEventStore)

    def test_creates_s3_repository_no_endpoint(self, prod_factory: CloudProviderFactory) -> None:
        repo = prod_factory.create_ride_repository()
        assert isinstance(repo, S3RideRepository)

    def test_creates_athena_engine(self, prod_factory: CloudProviderFactory) -> None:
        from ridestream.adapters.aws.athena_query_engine import AthenaQueryEngine

        engine = prod_factory.create_query_engine()
        assert isinstance(engine, AthenaQueryEngine)

    def test_creates_glue_catalog(self, prod_factory: CloudProviderFactory) -> None:
        from ridestream.adapters.aws.glue_catalog import GlueCatalogAdapter

        catalog = prod_factory.create_catalog()
        assert isinstance(catalog, GlueCatalogAdapter)


# ── Config Properties ─────────────────────────────────────


class TestConfigProperties:
    def test_is_local(self, local_config: Config) -> None:
        assert local_config.is_local is True
        assert local_config.is_production is False

    def test_is_production(self, prod_config: Config) -> None:
        assert prod_config.is_local is False
        assert prod_config.is_production is True
