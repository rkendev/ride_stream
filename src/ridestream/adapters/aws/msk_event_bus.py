"""AWS MSK Kafka event bus adapter.

Production adapter using MSK (same kafka-python client, different config).
Uses IAM auth and TLS in production.
"""

from __future__ import annotations

from ridestream.adapters.local.kafka_event_bus import KafkaEventPublisher, KafkaEventStore
from ridestream.domain.repositories import EventPublisher, EventStore


class MSKEventPublisher(KafkaEventPublisher):
    """MSK-backed event publisher for AWS production.

    Extends KafkaEventPublisher with MSK-specific defaults.
    In production, bootstrap_servers would be the MSK broker endpoints.
    """

    def __init__(
        self,
        bootstrap_servers: str,
        topic: str = "ride.events.raw",
        max_retries: int = 5,
        timeout_ms: int = 30000,
    ) -> None:
        super().__init__(
            bootstrap_servers=bootstrap_servers,
            topic=topic,
            max_retries=max_retries,
            timeout_ms=timeout_ms,
        )


class MSKEventStore(KafkaEventStore):
    """MSK-backed event store for AWS production."""

    def __init__(
        self,
        bootstrap_servers: str,
        topic: str = "ride.events.raw",
        group_id: str = "ridestream-event-store-prod",
    ) -> None:
        super().__init__(
            bootstrap_servers=bootstrap_servers,
            topic=topic,
            group_id=group_id,
        )


def create_event_publisher(bootstrap_servers: str) -> EventPublisher:
    """Factory for MSK event publisher."""
    return MSKEventPublisher(bootstrap_servers=bootstrap_servers)


def create_event_store(bootstrap_servers: str) -> EventStore:
    """Factory for MSK event store."""
    return MSKEventStore(bootstrap_servers=bootstrap_servers)
