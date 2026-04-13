"""Unit tests for quality gates."""

from __future__ import annotations

import pytest

from ridestream.adapters.quality_gates import (
    DataQualityChecker,
    DuplicateDetector,
    SchemaValidator,
    ValidationError,
)
from ridestream.domain.entities import Ride, RideEvent
from ridestream.domain.value_objects import (
    EventType,
    Location,
    PassengerID,
    RideID,
)

# ── Fixtures ──────────────────────────────────────────────


@pytest.fixture
def valid_event() -> RideEvent:
    return RideEvent.create(
        ride_id="ride-001",
        event_type=EventType.RIDE_REQUESTED,
        data={"passenger_id": "p1"},
    )


@pytest.fixture
def valid_ride() -> Ride:
    return Ride(
        id=RideID(value="ride-001"),
        passenger_id=PassengerID(value="p1"),
        pickup_location=Location(latitude=40.7128, longitude=-74.0060),
        dropoff_location=Location(latitude=40.7580, longitude=-73.9855),
    )


# ── SchemaValidator ───────────────────────────────────────


class TestSchemaValidatorEvent:
    def test_valid_event_passes(self, valid_event: RideEvent) -> None:
        SchemaValidator.validate_event(valid_event)  # should not raise

    def test_all_event_types_valid(self) -> None:
        for event_type in EventType:
            event = RideEvent.create("ride-1", event_type)
            SchemaValidator.validate_event(event)  # should not raise


class TestSchemaValidatorPayload:
    def test_valid_payload(self) -> None:
        payload = {
            "event_id": "e1",
            "ride_id": "r1",
            "event_type": "ride_requested",
            "timestamp": "2024-01-01T00:00:00Z",
        }
        SchemaValidator.validate_event_payload(payload)  # should not raise

    def test_missing_event_id_raises(self) -> None:
        payload = {"ride_id": "r1", "event_type": "ride_requested", "timestamp": "2024-01-01"}
        with pytest.raises(ValidationError, match="Missing required fields"):
            SchemaValidator.validate_event_payload(payload)

    def test_missing_ride_id_raises(self) -> None:
        payload = {"event_id": "e1", "event_type": "ride_requested", "timestamp": "2024-01-01"}
        with pytest.raises(ValidationError, match="Missing required fields"):
            SchemaValidator.validate_event_payload(payload)

    def test_missing_multiple_fields(self) -> None:
        payload = {"event_id": "e1"}
        with pytest.raises(ValidationError, match="Missing required fields"):
            SchemaValidator.validate_event_payload(payload)

    def test_invalid_event_type_raises(self) -> None:
        payload = {
            "event_id": "e1",
            "ride_id": "r1",
            "event_type": "invalid_type",
            "timestamp": "2024-01-01",
        }
        with pytest.raises(ValidationError, match="Invalid event_type"):
            SchemaValidator.validate_event_payload(payload)

    def test_empty_dict_raises(self) -> None:
        with pytest.raises(ValidationError, match="Missing required fields"):
            SchemaValidator.validate_event_payload({})


# ── DataQualityChecker ────────────────────────────────────


class TestDataQualityChecker:
    def test_valid_ride_passes(self, valid_ride: Ride) -> None:
        DataQualityChecker.validate_ride(valid_ride)  # should not raise

    def test_valid_ride_boundary_coords(self) -> None:
        ride = Ride(
            passenger_id=PassengerID(value="p1"),
            pickup_location=Location(latitude=90.0, longitude=180.0),
            dropoff_location=Location(latitude=-90.0, longitude=-180.0),
        )
        DataQualityChecker.validate_ride(ride)  # should not raise


# ── DuplicateDetector ─────────────────────────────────────


class TestDuplicateDetector:
    def test_first_event_not_duplicate(self, valid_event: RideEvent) -> None:
        detector = DuplicateDetector()
        assert not detector.is_duplicate(valid_event)

    def test_second_event_is_duplicate(self, valid_event: RideEvent) -> None:
        detector = DuplicateDetector()
        detector.mark_seen(valid_event)
        assert detector.is_duplicate(valid_event)

    def test_different_events_not_duplicate(self) -> None:
        detector = DuplicateDetector()
        e1 = RideEvent.create("ride-1", EventType.RIDE_REQUESTED)
        e2 = RideEvent.create("ride-2", EventType.RIDE_REQUESTED)
        detector.mark_seen(e1)
        assert not detector.is_duplicate(e2)

    def test_check_and_mark_new_returns_true(self, valid_event: RideEvent) -> None:
        detector = DuplicateDetector()
        assert detector.check_and_mark(valid_event) is True

    def test_check_and_mark_duplicate_returns_false(self, valid_event: RideEvent) -> None:
        detector = DuplicateDetector()
        detector.check_and_mark(valid_event)
        assert detector.check_and_mark(valid_event) is False

    def test_count_tracks_seen(self) -> None:
        detector = DuplicateDetector()
        assert detector.count == 0
        e1 = RideEvent.create("ride-1", EventType.RIDE_REQUESTED)
        e2 = RideEvent.create("ride-2", EventType.RIDE_ACCEPTED)
        detector.mark_seen(e1)
        detector.mark_seen(e2)
        assert detector.count == 2

    def test_clear_resets(self, valid_event: RideEvent) -> None:
        detector = DuplicateDetector()
        detector.mark_seen(valid_event)
        assert detector.count == 1
        detector.clear()
        assert detector.count == 0
        assert not detector.is_duplicate(valid_event)

    def test_idempotent_mark(self, valid_event: RideEvent) -> None:
        detector = DuplicateDetector()
        detector.mark_seen(valid_event)
        detector.mark_seen(valid_event)  # no error
        assert detector.count == 1
