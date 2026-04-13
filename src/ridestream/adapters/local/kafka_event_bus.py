"""Local Kafka event bus adapter — kafka-python for Docker Kafka.

Implements EventPublisher and EventStore ports.
Connects to local Kafka broker (Docker cp-kafka on port 9092).

Event payloads are FLAT (top-level fields) to match the dbt bronze source
schema in dbt/models/staging/_sources.yml.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from kafka import KafkaConsumer, KafkaProducer
from kafka.errors import KafkaError, KafkaTimeoutError, NoBrokersAvailable

from ridestream.adapters.errors import EventBusError
from ridestream.domain.entities import RideEvent
from ridestream.domain.repositories import EventPublisher, EventStore
from ridestream.domain.value_objects import RideID

logger = logging.getLogger(__name__)

DEFAULT_TOPIC = "ride.events.raw"
DLQ_TOPIC = "ride.events.dlq"


def _flatten_event_payload(event: RideEvent) -> dict[str, Any]:
    """Flatten a RideEvent into the bronze schema (top-level fields only).

    Maps the domain event.data dict into the columns defined in
    dbt/models/staging/_sources.yml for the bronze.ride_events source:

    event_id, ride_id, event_type, timestamp, rider_id, driver_id,
    latitude, longitude, fare_cents, distance_meters

    Args:
        event: The domain event.

    Returns:
        Flat dict ready for JSON serialization to Kafka.
    """
    data = event.data or {}

    # rider_id: renamed from passenger_id for dbt schema compatibility
    rider_id = data.get("passenger_id") or data.get("rider_id")

    # driver_id: null until RIDE_ACCEPTED
    driver_id = data.get("driver_id")

    # latitude/longitude: prefer pickup for requested, dropoff for completed
    pickup_lat = data.get("pickup_lat")
    pickup_lon = data.get("pickup_lon")
    dropoff_lat = data.get("dropoff_lat")
    dropoff_lon = data.get("dropoff_lon")
    latitude = data.get("latitude", pickup_lat if pickup_lat is not None else dropoff_lat)
    longitude = data.get("longitude", pickup_lon if pickup_lon is not None else dropoff_lon)

    # fare_cents: 0 for non-completed events
    fare_cents = int(data.get("fare_cents", 0) or 0)

    # distance_meters: derived from distance_km if present, else 0
    distance_km = data.get("distance_km")
    if distance_km is not None:
        distance_meters = int(float(distance_km) * 1000)
    else:
        distance_meters = int(data.get("distance_meters", 0) or 0)

    surge_multiplier = float(data.get("surge_multiplier", 1.0) or 1.0)

    return {
        "event_id": event.event_id,
        "ride_id": str(event.ride_id),
        "event_type": event.event_type.value,
        "timestamp": event.timestamp.to_iso(),
        "rider_id": rider_id,
        "driver_id": driver_id,
        "latitude": latitude,
        "longitude": longitude,
        "fare_cents": fare_cents,
        "distance_meters": distance_meters,
        "surge_multiplier": surge_multiplier,
    }


class KafkaEventPublisher(EventPublisher):
    """Publishes ride events to local Kafka.

    Args:
        bootstrap_servers: Kafka broker address (default: localhost:9092).
        topic: Target topic (default: ride.events.raw).
        dlq_topic: Dead-letter topic (default: ride.events.dlq).
        max_retries: Max publish retries with exponential backoff.
        timeout_ms: Producer send timeout in milliseconds.
    """

    def __init__(
        self,
        bootstrap_servers: str = "localhost:9092",
        topic: str = DEFAULT_TOPIC,
        dlq_topic: str = DLQ_TOPIC,
        max_retries: int = 3,
        timeout_ms: int = 10000,
    ) -> None:
        self._bootstrap_servers = bootstrap_servers
        self._topic = topic
        self._dlq_topic = dlq_topic
        self._max_retries = max_retries
        self._timeout_ms = timeout_ms
        self._producer: KafkaProducer | None = None

    def _get_producer(self) -> KafkaProducer:
        if self._producer is None:
            try:
                self._producer = KafkaProducer(
                    bootstrap_servers=self._bootstrap_servers,
                    value_serializer=lambda v: json.dumps(v).encode("utf-8"),
                    key_serializer=lambda k: k.encode("utf-8") if k else None,
                    acks="all",
                    retries=self._max_retries,
                    request_timeout_ms=self._timeout_ms,
                )
            except NoBrokersAvailable as e:
                raise EventBusError(
                    f"No Kafka brokers available at {self._bootstrap_servers}"
                ) from e
        return self._producer

    def publish(self, event: RideEvent) -> None:
        """Publish a ride event to Kafka with retry and DLQ routing.

        The payload is flattened to match the bronze dbt source schema
        (top-level rider_id/driver_id/latitude/longitude/fare_cents/distance_meters).

        Args:
            event: The ride event to publish.

        Raises:
            EventBusError: If publishing fails after all retries.
        """
        payload = _flatten_event_payload(event)

        for attempt in range(1, self._max_retries + 1):
            try:
                producer = self._get_producer()
                future = producer.send(
                    self._topic,
                    key=str(event.ride_id),
                    value=payload,
                )
                future.get(timeout=self._timeout_ms / 1000)
                logger.info(
                    "event_published",
                    extra={
                        "event_id": event.event_id,
                        "topic": self._topic,
                        "attempt": attempt,
                    },
                )
                return
            except KafkaTimeoutError:
                logger.warning(
                    "event_publish_timeout",
                    extra={"event_id": event.event_id, "attempt": attempt},
                )
                if attempt < self._max_retries:
                    time.sleep(2**attempt)
                else:
                    self._route_to_dlq(payload, "timeout")
                    raise EventBusError(
                        f"Publish timeout after {self._max_retries} retries"
                    ) from None
            except KafkaError as e:
                logger.error(
                    "event_publish_failed",
                    extra={
                        "event_id": event.event_id,
                        "attempt": attempt,
                        "error": str(e),
                    },
                )
                if attempt < self._max_retries:
                    time.sleep(2**attempt)
                else:
                    self._route_to_dlq(payload, str(e))
                    raise EventBusError(f"Publish failed: {e}") from e

    def _route_to_dlq(self, payload: dict[str, Any], reason: str) -> None:
        """Send failed event to dead letter queue."""
        try:
            producer = self._get_producer()
            dlq_payload = {
                **payload,
                "_error": reason,
                "_dlq_timestamp": time.time(),
            }
            producer.send(self._dlq_topic, value=dlq_payload)
            producer.flush(timeout=5)
            logger.error(
                "event_routed_to_dlq",
                extra={"ride_id": payload.get("ride_id"), "reason": reason},
            )
        except KafkaError:
            logger.critical("dlq_routing_failed", extra={"reason": reason})

    def close(self) -> None:
        """Flush and close the producer."""
        if self._producer:
            self._producer.flush()
            self._producer.close()
            self._producer = None


class KafkaEventStore(EventStore):
    """Event sourcing store backed by local Kafka.

    Args:
        bootstrap_servers: Kafka broker address.
        topic: Topic to read/write events.
        group_id: Consumer group ID (None for ad-hoc reads).
    """

    def __init__(
        self,
        bootstrap_servers: str = "localhost:9092",
        topic: str = DEFAULT_TOPIC,
        group_id: str = "ridestream-event-store",
    ) -> None:
        self._bootstrap_servers = bootstrap_servers
        self._topic = topic
        self._group_id = group_id
        self._publisher = KafkaEventPublisher(
            bootstrap_servers=bootstrap_servers,
            topic=topic,
        )

    def append(self, event: RideEvent) -> None:
        """Append an event to the store (publishes to Kafka).

        Args:
            event: The event to store.

        Raises:
            EventBusError: If append fails.
        """
        self._publisher.publish(event)

    def get_events_for_ride(self, ride_id: RideID) -> list[RideEvent]:
        """Read all events for a ride from Kafka.

        Consumes from beginning, filters by ride_id, returns ordered list.
        Reconstructs RideEvent objects from the flattened payload.

        Args:
            ride_id: The ride to fetch events for.

        Returns:
            Chronologically ordered events for this ride.

        Raises:
            EventBusError: If read fails.
        """
        try:
            consumer = KafkaConsumer(
                self._topic,
                bootstrap_servers=self._bootstrap_servers,
                group_id=None,
                auto_offset_reset="earliest",
                value_deserializer=lambda v: json.loads(v.decode("utf-8")),
                consumer_timeout_ms=5000,
            )
        except NoBrokersAvailable as e:
            raise EventBusError("No Kafka brokers available") from e

        events: list[RideEvent] = []
        try:
            from ridestream.domain.value_objects import EventType, Timestamp

            for message in consumer:
                payload = message.value
                if payload.get("ride_id") != str(ride_id):
                    continue

                # Reconstruct nested data dict from flat payload
                reconstructed_data: dict[str, Any] = {
                    k: v
                    for k, v in payload.items()
                    if k
                    not in {
                        "event_id",
                        "ride_id",
                        "event_type",
                        "timestamp",
                        "_error",
                        "_dlq_timestamp",
                    }
                    and v is not None
                }

                events.append(
                    RideEvent(
                        event_id=payload["event_id"],
                        ride_id=payload["ride_id"],
                        event_type=EventType(payload["event_type"]),
                        timestamp=Timestamp(value=payload["timestamp"]),
                        data=reconstructed_data,
                    )
                )
        except KafkaError as e:
            raise EventBusError(f"Failed to read events: {e}") from e
        finally:
            consumer.close()

        return events

    def close(self) -> None:
        """Close the underlying publisher."""
        self._publisher.close()
