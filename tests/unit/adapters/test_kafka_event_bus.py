"""Unit tests for Kafka event bus adapters."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from kafka.errors import KafkaTimeoutError, NoBrokersAvailable

from ridestream.adapters.errors import EventBusError
from ridestream.adapters.local.kafka_event_bus import (
    DEFAULT_TOPIC,
    DLQ_TOPIC,
    KafkaEventPublisher,
    KafkaEventStore,
)
from ridestream.domain.entities import RideEvent
from ridestream.domain.value_objects import EventType, RideID, Timestamp

# ── Fixtures ──────────────────────────────────────────────


@pytest.fixture
def mock_producer() -> MagicMock:
    producer = MagicMock()
    future = MagicMock()
    future.get.return_value = None  # successful send
    producer.send.return_value = future
    return producer


@pytest.fixture
def publisher(mock_producer: MagicMock) -> KafkaEventPublisher:
    pub = KafkaEventPublisher(bootstrap_servers="localhost:9092")
    pub._producer = mock_producer
    return pub


@pytest.fixture
def sample_event() -> RideEvent:
    return RideEvent.create(
        ride_id="ride-001",
        event_type=EventType.RIDE_REQUESTED,
        data={"passenger_id": "p1", "pickup_lat": 40.7, "pickup_lon": -74.0},
    )


@pytest.fixture
def sample_timestamp() -> Timestamp:
    return Timestamp()


# ── KafkaEventPublisher ──────────────────────────────────


class TestKafkaEventPublisherInit:
    def test_default_config(self) -> None:
        pub = KafkaEventPublisher()
        assert pub._bootstrap_servers == "localhost:9092"
        assert pub._topic == DEFAULT_TOPIC
        assert pub._max_retries == 3
        assert pub._producer is None

    def test_custom_config(self) -> None:
        pub = KafkaEventPublisher(
            bootstrap_servers="kafka:29092",
            topic="custom-topic",
            max_retries=5,
            timeout_ms=5000,
        )
        assert pub._bootstrap_servers == "kafka:29092"
        assert pub._topic == "custom-topic"
        assert pub._max_retries == 5

    @patch("ridestream.adapters.local.kafka_event_bus.KafkaProducer")
    def test_no_brokers_raises_event_bus_error(self, mock_cls: MagicMock) -> None:
        mock_cls.side_effect = NoBrokersAvailable()
        pub = KafkaEventPublisher()
        with pytest.raises(EventBusError, match="No Kafka brokers"):
            pub._get_producer()


class TestKafkaEventPublisherPublish:
    def test_publish_success(
        self, publisher: KafkaEventPublisher, mock_producer: MagicMock, sample_event: RideEvent
    ) -> None:
        publisher.publish(sample_event)

        mock_producer.send.assert_called_once()
        call_args = mock_producer.send.call_args
        assert call_args[0][0] == DEFAULT_TOPIC
        assert call_args[1]["key"] == "ride-001"
        payload = call_args[1]["value"]
        assert payload["event_id"] == sample_event.event_id
        assert payload["ride_id"] == "ride-001"
        assert payload["event_type"] == "ride_requested"

    def test_publish_timeout_retries(
        self, publisher: KafkaEventPublisher, mock_producer: MagicMock, sample_event: RideEvent
    ) -> None:
        future = MagicMock()
        future.get.side_effect = KafkaTimeoutError()
        mock_producer.send.return_value = future
        publisher._max_retries = 1

        with pytest.raises(EventBusError, match="timeout"):
            publisher.publish(sample_event)

    def test_publish_timeout_routes_to_dlq(
        self, publisher: KafkaEventPublisher, mock_producer: MagicMock, sample_event: RideEvent
    ) -> None:
        future = MagicMock()
        future.get.side_effect = KafkaTimeoutError()
        mock_producer.send.return_value = future
        publisher._max_retries = 1

        with pytest.raises(EventBusError):
            publisher.publish(sample_event)

        # DLQ send should have been attempted (2 sends total: 1 original + 1 DLQ)
        assert mock_producer.send.call_count >= 2
        dlq_call = mock_producer.send.call_args_list[-1]
        assert dlq_call[0][0] == DLQ_TOPIC

    def test_publish_payload_structure_is_flat(
        self, publisher: KafkaEventPublisher, mock_producer: MagicMock, sample_event: RideEvent
    ) -> None:
        """Payload must be FLAT (top-level fields) to match dbt bronze schema."""
        publisher.publish(sample_event)
        payload = mock_producer.send.call_args[1]["value"]

        # Required top-level fields per _sources.yml
        assert "event_id" in payload
        assert "ride_id" in payload
        assert "event_type" in payload
        assert "timestamp" in payload
        assert "rider_id" in payload
        assert "driver_id" in payload
        assert "latitude" in payload
        assert "longitude" in payload
        assert "fare_cents" in payload
        assert "distance_meters" in payload
        # No nested 'data' field — must be flat
        assert "data" not in payload

    def test_publish_maps_passenger_id_to_rider_id(
        self, publisher: KafkaEventPublisher, mock_producer: MagicMock, sample_event: RideEvent
    ) -> None:
        """Sample event uses passenger_id; bronze schema requires rider_id."""
        publisher.publish(sample_event)
        payload = mock_producer.send.call_args[1]["value"]
        assert payload["rider_id"] == "p1"

    def test_publish_uses_pickup_coords_as_latitude_longitude(
        self, publisher: KafkaEventPublisher, mock_producer: MagicMock, sample_event: RideEvent
    ) -> None:
        publisher.publish(sample_event)
        payload = mock_producer.send.call_args[1]["value"]
        assert payload["latitude"] == 40.7
        assert payload["longitude"] == -74.0

    def test_publish_converts_distance_km_to_meters(
        self, publisher: KafkaEventPublisher, mock_producer: MagicMock
    ) -> None:
        event = RideEvent.create(
            ride_id="ride-002",
            event_type=EventType.RIDE_COMPLETED,
            data={
                "distance_km": 5.5,
                "fare_cents": 1500,
                "driver_id": "d1",
            },
        )
        publisher.publish(event)
        payload = mock_producer.send.call_args[1]["value"]
        assert payload["distance_meters"] == 5500
        assert payload["fare_cents"] == 1500
        assert payload["driver_id"] == "d1"

    def test_publish_null_fields_for_requested_event(
        self, publisher: KafkaEventPublisher, mock_producer: MagicMock, sample_event: RideEvent
    ) -> None:
        publisher.publish(sample_event)
        payload = mock_producer.send.call_args[1]["value"]
        # REQUESTED event has no driver — must be null
        assert payload["driver_id"] is None
        assert payload["fare_cents"] == 0
        assert payload["distance_meters"] == 0

    def test_close_flushes_producer(
        self, publisher: KafkaEventPublisher, mock_producer: MagicMock
    ) -> None:
        publisher.close()
        mock_producer.flush.assert_called_once()
        mock_producer.close.assert_called_once()
        assert publisher._producer is None

    def test_close_noop_when_no_producer(self) -> None:
        pub = KafkaEventPublisher()
        pub.close()  # should not raise


# ── KafkaEventStore ───────────────────────────────────────


class TestKafkaEventStoreInit:
    def test_default_config(self) -> None:
        store = KafkaEventStore()
        assert store._topic == DEFAULT_TOPIC
        assert store._group_id == "ridestream-event-store"

    def test_custom_config(self) -> None:
        store = KafkaEventStore(
            bootstrap_servers="kafka:29092",
            topic="custom",
            group_id="my-group",
        )
        assert store._bootstrap_servers == "kafka:29092"


class TestKafkaEventStoreAppend:
    def test_append_delegates_to_publisher(self) -> None:
        store = KafkaEventStore()
        store._publisher = MagicMock()
        event = RideEvent.create("ride-1", EventType.RIDE_REQUESTED)

        store.append(event)

        store._publisher.publish.assert_called_once_with(event)

    def test_append_propagates_errors(self) -> None:
        store = KafkaEventStore()
        store._publisher = MagicMock()
        store._publisher.publish.side_effect = EventBusError("fail")
        event = RideEvent.create("ride-1", EventType.RIDE_REQUESTED)

        with pytest.raises(EventBusError):
            store.append(event)


class TestKafkaEventStoreRead:
    @patch("ridestream.adapters.local.kafka_event_bus.KafkaConsumer")
    def test_no_brokers_raises_event_bus_error(self, mock_consumer_cls: MagicMock) -> None:
        mock_consumer_cls.side_effect = NoBrokersAvailable()
        store = KafkaEventStore()

        with pytest.raises(EventBusError, match="No Kafka brokers"):
            store.get_events_for_ride(RideID(value="ride-1"))

    @patch("ridestream.adapters.local.kafka_event_bus.KafkaConsumer")
    def test_filters_by_ride_id(self, mock_consumer_cls: MagicMock) -> None:
        ts = Timestamp()
        msg1 = MagicMock()
        msg1.value = {
            "event_id": "e1",
            "ride_id": "ride-1",
            "event_type": "ride_requested",
            "timestamp": ts.to_iso(),
            "rider_id": "p1",
            "driver_id": None,
            "latitude": 40.7,
            "longitude": -74.0,
            "fare_cents": 0,
            "distance_meters": 0,
        }
        msg2 = MagicMock()
        msg2.value = {
            "event_id": "e2",
            "ride_id": "ride-2",
            "event_type": "ride_requested",
            "timestamp": ts.to_iso(),
            "rider_id": "p2",
            "driver_id": None,
            "latitude": 40.8,
            "longitude": -74.1,
            "fare_cents": 0,
            "distance_meters": 0,
        }
        mock_consumer = MagicMock()
        mock_consumer.__iter__ = MagicMock(return_value=iter([msg1, msg2]))
        mock_consumer_cls.return_value = mock_consumer

        store = KafkaEventStore()
        events = store.get_events_for_ride(RideID(value="ride-1"))

        assert len(events) == 1
        assert events[0].ride_id == "ride-1"

    @patch("ridestream.adapters.local.kafka_event_bus.KafkaConsumer")
    def test_returns_empty_for_unknown_ride(self, mock_consumer_cls: MagicMock) -> None:
        mock_consumer = MagicMock()
        mock_consumer.__iter__ = MagicMock(return_value=iter([]))
        mock_consumer_cls.return_value = mock_consumer

        store = KafkaEventStore()
        events = store.get_events_for_ride(RideID(value="nonexistent"))

        assert events == []

    def test_close_delegates_to_publisher(self) -> None:
        store = KafkaEventStore()
        store._publisher = MagicMock()
        store.close()
        store._publisher.close.assert_called_once()


# ── MSK Adapters ──────────────────────────────────────────


class TestMSKAdapters:
    def test_msk_publisher_extends_kafka(self) -> None:
        from ridestream.adapters.aws.msk_event_bus import MSKEventPublisher

        pub = MSKEventPublisher(bootstrap_servers="b-1.msk.kafka:9092")
        assert pub._bootstrap_servers == "b-1.msk.kafka:9092"
        assert pub._max_retries == 5
        assert pub._timeout_ms == 30000

    def test_msk_event_store_extends_kafka(self) -> None:
        from ridestream.adapters.aws.msk_event_bus import MSKEventStore

        store = MSKEventStore(bootstrap_servers="b-1.msk.kafka:9092")
        assert store._bootstrap_servers == "b-1.msk.kafka:9092"
        assert store._group_id == "ridestream-event-store-prod"

    def test_factory_creates_publisher(self) -> None:
        from ridestream.adapters.aws.msk_event_bus import create_event_publisher

        pub = create_event_publisher("b-1.msk:9092")
        assert isinstance(pub, KafkaEventPublisher)

    def test_factory_creates_store(self) -> None:
        from ridestream.adapters.aws.msk_event_bus import create_event_store

        store = create_event_store("b-1.msk:9092")
        assert isinstance(store, KafkaEventStore)
