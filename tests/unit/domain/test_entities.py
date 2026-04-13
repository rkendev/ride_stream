"""Unit tests for domain entities (Ride, RideEvent)."""

import pytest
from pydantic import ValidationError

from ridestream.domain.entities import Ride, RideEvent
from ridestream.domain.errors import InvalidRideStateError
from ridestream.domain.value_objects import (
    Distance,
    DriverID,
    EventType,
    Location,
    Money,
    PassengerID,
    RideStatus,
    Timestamp,
)

# ── Fixtures ──────────────────────────────────────────────


@pytest.fixture
def pickup() -> Location:
    return Location(latitude=40.7128, longitude=-74.0060)


@pytest.fixture
def dropoff() -> Location:
    return Location(latitude=40.7580, longitude=-73.9855)


@pytest.fixture
def passenger_id() -> PassengerID:
    return PassengerID(value="passenger-1")


@pytest.fixture
def driver_id() -> DriverID:
    return DriverID(value="driver-1")


@pytest.fixture
def new_ride(passenger_id: PassengerID, pickup: Location, dropoff: Location) -> Ride:
    return Ride(
        passenger_id=passenger_id,
        pickup_location=pickup,
        dropoff_location=dropoff,
    )


# ── RideEvent ─────────────────────────────────────────────


class TestRideEvent:
    def test_create_event(self) -> None:
        event = RideEvent.create(
            ride_id="ride-123",
            event_type=EventType.RIDE_REQUESTED,
            data={"passenger_id": "p1"},
        )
        assert event.ride_id == "ride-123"
        assert event.event_type == EventType.RIDE_REQUESTED
        assert event.data["passenger_id"] == "p1"
        assert len(event.event_id) == 32

    def test_create_event_deterministic_id(self) -> None:
        ts = Timestamp()
        e1 = RideEvent.create("ride-1", EventType.RIDE_REQUESTED, timestamp=ts)
        e2 = RideEvent.create("ride-1", EventType.RIDE_REQUESTED, timestamp=ts)
        assert e1.event_id == e2.event_id

    def test_create_event_different_ids(self) -> None:
        ts = Timestamp()
        e1 = RideEvent.create("ride-1", EventType.RIDE_REQUESTED, timestamp=ts)
        e2 = RideEvent.create("ride-2", EventType.RIDE_REQUESTED, timestamp=ts)
        assert e1.event_id != e2.event_id

    def test_event_immutable(self) -> None:
        event = RideEvent.create("ride-1", EventType.RIDE_REQUESTED)
        with pytest.raises(ValidationError):
            event.ride_id = "changed"  # type: ignore[misc]

    def test_event_default_data(self) -> None:
        event = RideEvent.create("ride-1", EventType.GPS_UPDATE)
        assert event.data == {}


# ── Ride Creation ─────────────────────────────────────────


class TestRideCreation:
    def test_new_ride_status(self, new_ride: Ride) -> None:
        assert new_ride.status == RideStatus.REQUESTED

    def test_new_ride_no_driver(self, new_ride: Ride) -> None:
        assert new_ride.driver_id is None

    def test_new_ride_no_distance(self, new_ride: Ride) -> None:
        assert new_ride.distance is None

    def test_new_ride_no_fare(self, new_ride: Ride) -> None:
        assert new_ride.fare is None

    def test_new_ride_has_timestamp(self, new_ride: Ride) -> None:
        assert new_ride.created_at is not None

    def test_new_ride_is_active(self, new_ride: Ride) -> None:
        assert new_ride.is_active is True

    def test_new_ride_not_terminal(self, new_ride: Ride) -> None:
        assert new_ride.is_terminal is False

    def test_estimated_distance(self, new_ride: Ride) -> None:
        dist = new_ride.estimated_distance_km
        assert dist > 0

    def test_ride_immutable(self, new_ride: Ride) -> None:
        with pytest.raises(ValidationError):
            new_ride.status = RideStatus.ACCEPTED  # type: ignore[misc]


# ── State Transitions ─────────────────────────────────────


class TestRideAccept:
    def test_accept_from_requested(self, new_ride: Ride, driver_id: DriverID) -> None:
        accepted = new_ride.accept(driver_id)
        assert accepted.status == RideStatus.ACCEPTED
        assert accepted.driver_id == driver_id

    def test_accept_returns_new_instance(self, new_ride: Ride, driver_id: DriverID) -> None:
        accepted = new_ride.accept(driver_id)
        assert accepted is not new_ride
        assert new_ride.status == RideStatus.REQUESTED  # original unchanged

    def test_accept_from_accepted_raises(self, new_ride: Ride, driver_id: DriverID) -> None:
        accepted = new_ride.accept(driver_id)
        with pytest.raises(InvalidRideStateError, match="Cannot transition"):
            accepted.accept(DriverID(value="driver-2"))

    def test_accept_from_completed_raises(self, new_ride: Ride, driver_id: DriverID) -> None:
        completed = (
            new_ride.accept(driver_id).start().complete(Distance(value_km=5.0), Money(cents=1500))
        )
        with pytest.raises(InvalidRideStateError):
            completed.accept(driver_id)


class TestRideStart:
    def test_start_from_accepted(self, new_ride: Ride, driver_id: DriverID) -> None:
        started = new_ride.accept(driver_id).start()
        assert started.status == RideStatus.IN_PROGRESS
        assert started.started_at is not None

    def test_start_from_requested_raises(self, new_ride: Ride) -> None:
        with pytest.raises(InvalidRideStateError):
            new_ride.start()

    def test_start_from_in_progress_raises(self, new_ride: Ride, driver_id: DriverID) -> None:
        started = new_ride.accept(driver_id).start()
        with pytest.raises(InvalidRideStateError):
            started.start()


class TestRideComplete:
    def test_complete_from_in_progress(self, new_ride: Ride, driver_id: DriverID) -> None:
        completed = (
            new_ride.accept(driver_id).start().complete(Distance(value_km=5.0), Money(cents=1500))
        )
        assert completed.status == RideStatus.COMPLETED
        assert completed.distance is not None
        assert completed.distance.value_km == 5.0
        assert completed.fare is not None
        assert completed.fare.cents == 1500
        assert completed.completed_at is not None

    def test_complete_is_terminal(self, new_ride: Ride, driver_id: DriverID) -> None:
        completed = (
            new_ride.accept(driver_id).start().complete(Distance(value_km=5.0), Money(cents=1500))
        )
        assert completed.is_terminal is True
        assert completed.is_active is False

    def test_complete_from_requested_raises(self, new_ride: Ride) -> None:
        with pytest.raises(InvalidRideStateError):
            new_ride.complete(Distance(value_km=5.0), Money(cents=1500))

    def test_complete_from_accepted_raises(self, new_ride: Ride, driver_id: DriverID) -> None:
        accepted = new_ride.accept(driver_id)
        with pytest.raises(InvalidRideStateError):
            accepted.complete(Distance(value_km=5.0), Money(cents=1500))


class TestRideCancel:
    def test_cancel_from_requested(self, new_ride: Ride) -> None:
        cancelled = new_ride.cancel("changed mind")
        assert cancelled.status == RideStatus.CANCELLED
        assert cancelled.cancel_reason == "changed mind"

    def test_cancel_from_accepted(self, new_ride: Ride, driver_id: DriverID) -> None:
        cancelled = new_ride.accept(driver_id).cancel("driver unavailable")
        assert cancelled.status == RideStatus.CANCELLED

    def test_cancel_from_in_progress(self, new_ride: Ride, driver_id: DriverID) -> None:
        cancelled = new_ride.accept(driver_id).start().cancel("emergency")
        assert cancelled.status == RideStatus.CANCELLED

    def test_cancel_from_completed_raises(self, new_ride: Ride, driver_id: DriverID) -> None:
        completed = (
            new_ride.accept(driver_id).start().complete(Distance(value_km=5.0), Money(cents=1500))
        )
        with pytest.raises(InvalidRideStateError):
            completed.cancel("too late")

    def test_cancel_from_cancelled_raises(self, new_ride: Ride) -> None:
        cancelled = new_ride.cancel("first")
        with pytest.raises(InvalidRideStateError):
            cancelled.cancel("second")

    def test_cancel_is_terminal(self, new_ride: Ride) -> None:
        cancelled = new_ride.cancel("test")
        assert cancelled.is_terminal is True
        assert cancelled.is_active is False


# ── Full Lifecycle ────────────────────────────────────────


class TestRideLifecycle:
    def test_full_happy_path(self, new_ride: Ride, driver_id: DriverID) -> None:
        accepted = new_ride.accept(driver_id)
        started = accepted.start()
        completed = started.complete(Distance(value_km=10.0), Money(cents=2500))

        assert completed.status == RideStatus.COMPLETED
        assert completed.driver_id == driver_id
        assert completed.distance is not None
        assert completed.distance.value_km == 10.0
        assert completed.fare is not None
        assert completed.fare.cents == 2500
        assert completed.started_at is not None
        assert completed.completed_at is not None

    def test_original_unchanged_after_transitions(
        self, new_ride: Ride, driver_id: DriverID
    ) -> None:
        _ = new_ride.accept(driver_id)
        assert new_ride.status == RideStatus.REQUESTED
        assert new_ride.driver_id is None
