"""CloudProvider factory — selects correct adapters based on environment.

Routes config.environment (local/staging/production) to the correct
adapter implementations. Application layer uses this factory to get
adapters without knowing which concrete implementation is used.
"""

from __future__ import annotations

from ridestream.config import Config
from ridestream.domain.repositories import (
    CatalogAdapter,
    EventPublisher,
    EventStore,
    QueryEngine,
    RideRepository,
)


class CloudProviderFactory:
    """Factory for creating environment-specific adapters.

    Args:
        config: Application configuration.
    """

    def __init__(self, config: Config) -> None:
        self._config = config

    def create_event_publisher(self) -> EventPublisher:
        """Create an EventPublisher for the current environment.

        Returns:
            KafkaEventPublisher (local) or MSKEventPublisher (aws).
        """
        if self._config.is_local:
            from ridestream.adapters.local.kafka_event_bus import KafkaEventPublisher

            return KafkaEventPublisher(
                bootstrap_servers=self._config.kafka_bootstrap_servers,
            )
        else:
            from ridestream.adapters.aws.msk_event_bus import MSKEventPublisher

            return MSKEventPublisher(
                bootstrap_servers=self._config.kafka_bootstrap_servers,
            )

    def create_event_store(self) -> EventStore:
        """Create an EventStore for the current environment.

        Returns:
            KafkaEventStore (local) or MSKEventStore (aws).
        """
        if self._config.is_local:
            from ridestream.adapters.local.kafka_event_bus import KafkaEventStore

            return KafkaEventStore(
                bootstrap_servers=self._config.kafka_bootstrap_servers,
            )
        else:
            from ridestream.adapters.aws.msk_event_bus import MSKEventStore

            return MSKEventStore(
                bootstrap_servers=self._config.kafka_bootstrap_servers,
            )

    def create_ride_repository(self) -> RideRepository:
        """Create a RideRepository for the current environment.

        Returns:
            S3RideRepository (both local and aws, different client config).
        """
        from ridestream.adapters.local.minio_storage import S3RideRepository, create_s3_client

        if self._config.is_local:
            client = create_s3_client(
                endpoint_url=self._config.minio_endpoint,
                access_key=self._config.minio_access_key,
                secret_key=self._config.minio_secret_key,
            )
        else:
            client = create_s3_client(endpoint_url=None)

        return S3RideRepository(s3_client=client, bucket="bronze", prefix="rides/")

    def create_catalog(self) -> CatalogAdapter:
        """Create a CatalogAdapter for the current environment.

        Returns:
            HiveCatalogAdapter (local) or GlueCatalogAdapter (aws).
        """
        if self._config.is_local:
            from ridestream.adapters.local.hive_catalog import HiveCatalogAdapter

            engine = self.create_query_engine()
            return HiveCatalogAdapter(query_engine=engine)
        else:
            from ridestream.adapters.aws.glue_catalog import create_glue_catalog

            return create_glue_catalog()

    def create_query_engine(self) -> QueryEngine:
        """Create a QueryEngine for the current environment.

        Returns:
            TrinoQueryEngine (local) or AthenaQueryEngine (aws).
        """
        if self._config.is_local:
            from ridestream.adapters.local.trino_query_engine import TrinoQueryEngine

            return TrinoQueryEngine(
                host=self._config.trino_host,
                port=self._config.trino_port,
                user=self._config.trino_user,
            )
        else:
            from ridestream.adapters.aws.athena_query_engine import create_athena_engine

            return create_athena_engine()
