"""Integration test: Simulator → Kafka (real produce/consume).

Exercises the full produce path via RideSimulator + KafkaEventPublisher,
then consumes the produced messages and asserts on real payloads.
"""

from __future__ import annotations

import uuid

import pytest

from ridestream.adapters.local.kafka_event_bus import KafkaEventPublisher
from ridestream.application.ride_simulator import RideSimulator
from ridestream.config import Config, Environment
from tests.integration.conftest import KAFKA_BOOTSTRAP, KAFKA_DLQ_TOPIC, KAFKA_TOPIC

pytestmark = pytest.mark.integration


class TestSimulatorToKafka:
    def test_simulator_produces_full_ride_lifecycle(self, kafka_consumer_factory) -> None:
        """Simulate N rides; verify 4×N events landed in ride.events.raw with correct flat schema."""
        config = Config(environment=Environment.LOCAL)
        publisher = KafkaEventPublisher(
            bootstrap_servers=KAFKA_BOOTSTRAP,
            topic=KAFKA_TOPIC,
        )

        group = f"sim-test-{uuid.uuid4().hex[:8]}"
        consumer = kafka_consumer_factory(KAFKA_TOPIC, group_id=group)

        num_rides = 3
        try:
            simulator = RideSimulator(event_publisher=publisher, config=config)
            rides = simulator.run(num_rides=num_rides)
            assert len(rides) == num_rides
            ride_ids_produced = {str(r.id) for r in rides}
        finally:
            publisher.close()

        # Consume and filter to messages from this test
        received: list[dict] = []
        for msg in consumer:
            if msg.value.get("ride_id") in ride_ids_produced:
                received.append(msg.value)

        # 4 events per ride (REQUESTED → ACCEPTED → STARTED → COMPLETED)
        assert (
            len(received) >= num_rides * 4
        ), f"Expected >= {num_rides * 4} events, got {len(received)}"

        # Assert required flat fields (dbt bronze schema compliance)
        for payload in received:
            assert "event_id" in payload
            assert "ride_id" in payload
            assert "event_type" in payload
            assert "timestamp" in payload
            assert "rider_id" in payload
            assert "latitude" in payload
            assert "longitude" in payload
            # No nested data field
            assert "data" not in payload

        # All 4 event types present
        event_types = {p["event_type"] for p in received}
        assert "ride_requested" in event_types
        assert "ride_accepted" in event_types
        assert "ride_started" in event_types
        assert "ride_completed" in event_types

        # Unique ride_ids match what we produced
        ride_ids_received = {p["ride_id"] for p in received}
        assert ride_ids_received >= ride_ids_produced

    def test_dlq_only_contains_error_messages(self, kafka_consumer_factory) -> None:
        """DLQ topic structure check: if anything is there, it must have _error marker."""
        group = f"dlq-check-{uuid.uuid4().hex[:8]}"
        consumer = kafka_consumer_factory(KAFKA_DLQ_TOPIC, group_id=group)

        # Produce a known-good event (should NOT go to DLQ)
        config = Config(environment=Environment.LOCAL)
        publisher = KafkaEventPublisher(bootstrap_servers=KAFKA_BOOTSTRAP, topic=KAFKA_TOPIC)
        try:
            simulator = RideSimulator(event_publisher=publisher, config=config)
            rides = simulator.run(num_rides=1)
            ride_id = str(rides[0].id)
        finally:
            publisher.close()

        # Read any DLQ messages — no message should have the ride_id we just produced
        # (because the produce succeeded — no DLQ routing)
        dlq_messages = list(consumer)
        dlq_ride_ids = {msg.value.get("ride_id") for msg in dlq_messages}
        assert ride_id not in dlq_ride_ids

        # Any existing DLQ messages must follow DLQ schema (have _error field)
        for msg in dlq_messages:
            assert "_error" in msg.value

    def test_completed_event_has_fare_and_distance(self, kafka_consumer_factory) -> None:
        """RIDE_COMPLETED events have non-zero flat fare_cents and distance_meters."""
        config = Config(environment=Environment.LOCAL)
        publisher = KafkaEventPublisher(bootstrap_servers=KAFKA_BOOTSTRAP, topic=KAFKA_TOPIC)
        group = f"completed-{uuid.uuid4().hex[:8]}"
        consumer = kafka_consumer_factory(KAFKA_TOPIC, group_id=group)

        try:
            simulator = RideSimulator(event_publisher=publisher, config=config)
            rides = simulator.run(num_rides=2)
            ride_ids = {str(r.id) for r in rides}
        finally:
            publisher.close()

        completed = [
            msg.value
            for msg in consumer
            if msg.value.get("ride_id") in ride_ids
            and msg.value.get("event_type") == "ride_completed"
        ]

        assert len(completed) == 2
        for evt in completed:
            assert evt["fare_cents"] > 0
            assert evt["distance_meters"] > 0
            assert evt["driver_id"] is not None
