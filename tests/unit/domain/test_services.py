"""Unit tests for domain services."""

import pytest

from ridestream.domain.entities import RideEvent
from ridestream.domain.errors import InvalidFareError, InvalidLocationError
from ridestream.domain.services import (
    EventAggregationService,
    FareCalculationService,
    RideService,
)
from ridestream.domain.value_objects import (
    Distance,
    DriverID,
    EventType,
    Location,
    Money,
    PassengerID,
    RideStatus,
)

# ── Fixtures ──────────────────────────────────────────────


@pytest.fixture
def pickup() -> Location:
    return Location(latitude=40.7128, longitude=-74.0060)


@pytest.fixture
def dropoff() -> Location:
    return Location(latitude=40.7580, longitude=-73.9855)


@pytest.fixture
def passenger() -> PassengerID:
    return PassengerID(value="passenger-1")


@pytest.fixture
def driver() -> DriverID:
    return DriverID(value="driver-1")


# ── RideService ───────────────────────────────────────────


class TestRideServiceCreate:
    def test_create_ride(self, passenger: PassengerID, pickup: Location, dropoff: Location) -> None:
        ride = RideService.create_ride(passenger, pickup, dropoff)
        assert ride.status == RideStatus.REQUESTED
        assert ride.passenger_id == passenger
        assert ride.pickup_location == pickup
        assert ride.dropoff_location == dropoff

    def test_create_ride_same_location_raises(
        self, passenger: PassengerID, pickup: Location
    ) -> None:
        with pytest.raises(InvalidLocationError, match="cannot be the same"):
            RideService.create_ride(passenger, pickup, pickup)


class TestRideServiceAccept:
    def test_accept_ride(
        self, passenger: PassengerID, pickup: Location, dropoff: Location, driver: DriverID
    ) -> None:
        ride = RideService.create_ride(passenger, pickup, dropoff)
        accepted = RideService.accept_ride(ride, driver)
        assert accepted.status == RideStatus.ACCEPTED
        assert accepted.driver_id == driver


class TestRideServiceStart:
    def test_start_ride(
        self, passenger: PassengerID, pickup: Location, dropoff: Location, driver: DriverID
    ) -> None:
        ride = RideService.create_ride(passenger, pickup, dropoff)
        accepted = RideService.accept_ride(ride, driver)
        started = RideService.start_ride(accepted)
        assert started.status == RideStatus.IN_PROGRESS


class TestRideServiceComplete:
    def test_complete_ride(
        self, passenger: PassengerID, pickup: Location, dropoff: Location, driver: DriverID
    ) -> None:
        ride = RideService.create_ride(passenger, pickup, dropoff)
        accepted = RideService.accept_ride(ride, driver)
        started = RideService.start_ride(accepted)
        completed = RideService.complete_ride(started, Distance(value_km=5.0), Money(cents=1500))
        assert completed.status == RideStatus.COMPLETED


class TestRideServiceCancel:
    def test_cancel_ride(self, passenger: PassengerID, pickup: Location, dropoff: Location) -> None:
        ride = RideService.create_ride(passenger, pickup, dropoff)
        cancelled = RideService.cancel_ride(ride, "changed mind")
        assert cancelled.status == RideStatus.CANCELLED
        assert cancelled.cancel_reason == "changed mind"


class TestRideServiceValidateLocation:
    def test_valid(self) -> None:
        assert RideService.validate_location(40.7128, -74.0060) is True

    def test_invalid_lat_high(self) -> None:
        with pytest.raises(InvalidLocationError, match="Latitude"):
            RideService.validate_location(91.0, 0.0)

    def test_invalid_lat_low(self) -> None:
        with pytest.raises(InvalidLocationError, match="Latitude"):
            RideService.validate_location(-91.0, 0.0)

    def test_invalid_lon_high(self) -> None:
        with pytest.raises(InvalidLocationError, match="Longitude"):
            RideService.validate_location(0.0, 181.0)

    def test_invalid_lon_low(self) -> None:
        with pytest.raises(InvalidLocationError, match="Longitude"):
            RideService.validate_location(0.0, -181.0)

    def test_boundary_valid(self) -> None:
        assert RideService.validate_location(90.0, 180.0) is True
        assert RideService.validate_location(-90.0, -180.0) is True


# ── FareCalculationService ────────────────────────────────


class TestFareCalculation:
    def test_basic_fare(self) -> None:
        fare = FareCalculationService.calculate_fare(
            distance=Distance(value_km=5.0),
            base_price=Money(cents=250),
            rate_per_km=Money(cents=150),
        )
        # 250 + ceil(5.0 * 150 * 1.0) = 250 + 750 = 1000
        assert fare.cents == 1000
        assert fare.currency == "USD"

    def test_fare_with_surge(self) -> None:
        fare = FareCalculationService.calculate_fare(
            distance=Distance(value_km=10.0),
            base_price=Money(cents=250),
            rate_per_km=Money(cents=150),
            surge_multiplier=2.0,
        )
        # 250 + ceil(10 * 150 * 2.0) = 250 + 3000 = 3250
        assert fare.cents == 3250

    def test_fare_zero_distance(self) -> None:
        fare = FareCalculationService.calculate_fare(
            distance=Distance(value_km=0.0),
            base_price=Money(cents=250),
            rate_per_km=Money(cents=150),
        )
        assert fare.cents == 250  # base only

    def test_surge_below_1_raises(self) -> None:
        with pytest.raises(InvalidFareError, match="must be >= 1.0"):
            FareCalculationService.calculate_fare(
                distance=Distance(value_km=5.0),
                base_price=Money(cents=250),
                rate_per_km=Money(cents=150),
                surge_multiplier=0.5,
            )

    def test_surge_above_5_raises(self) -> None:
        with pytest.raises(InvalidFareError, match="must be <= 5.0"):
            FareCalculationService.calculate_fare(
                distance=Distance(value_km=5.0),
                base_price=Money(cents=250),
                rate_per_km=Money(cents=150),
                surge_multiplier=6.0,
            )

    def test_surge_boundary_5(self) -> None:
        fare = FareCalculationService.calculate_fare(
            distance=Distance(value_km=1.0),
            base_price=Money(cents=100),
            rate_per_km=Money(cents=100),
            surge_multiplier=5.0,
        )
        # 100 + ceil(1.0 * 100 * 5.0) = 100 + 500 = 600
        assert fare.cents == 600


class TestSurgeMultiplier:
    def test_low_demand(self) -> None:
        assert FareCalculationService.calculate_surge_multiplier(0) == 1.0
        assert FareCalculationService.calculate_surge_multiplier(4) == 1.0

    def test_moderate_demand(self) -> None:
        assert FareCalculationService.calculate_surge_multiplier(5) == 1.25
        assert FareCalculationService.calculate_surge_multiplier(9) == 1.25

    def test_high_demand(self) -> None:
        assert FareCalculationService.calculate_surge_multiplier(10) == 1.5
        assert FareCalculationService.calculate_surge_multiplier(14) == 1.5

    def test_very_high_demand(self) -> None:
        assert FareCalculationService.calculate_surge_multiplier(15) == 2.0
        assert FareCalculationService.calculate_surge_multiplier(19) == 2.0

    def test_extreme_demand(self) -> None:
        assert FareCalculationService.calculate_surge_multiplier(20) == 3.0
        assert FareCalculationService.calculate_surge_multiplier(100) == 3.0


class TestEstimateFare:
    def test_estimate(self) -> None:
        pickup = Location(latitude=40.7128, longitude=-74.0060)
        dropoff = Location(latitude=40.7580, longitude=-73.9855)
        fare = FareCalculationService.estimate_fare(pickup, dropoff)
        assert fare.cents > 250  # more than base fare
        assert fare.currency == "USD"

    def test_estimate_custom_base(self) -> None:
        pickup = Location(latitude=40.0, longitude=-74.0)
        dropoff = Location(latitude=40.0, longitude=-74.0001)
        fare = FareCalculationService.estimate_fare(pickup, dropoff, base_cents=500)
        assert fare.cents >= 500


# ── EventAggregationService ───────────────────────────────


class TestEventAggregation:
    def _make_requested_event(self, ride_id: str = "ride-1") -> RideEvent:
        return RideEvent.create(
            ride_id=ride_id,
            event_type=EventType.RIDE_REQUESTED,
            data={
                "passenger_id": "p1",
                "pickup_lat": 40.7128,
                "pickup_lon": -74.0060,
                "dropoff_lat": 40.7580,
                "dropoff_lon": -73.9855,
            },
        )

    def test_single_requested_event(self) -> None:
        events = [self._make_requested_event()]
        ride = EventAggregationService.aggregate_events(events)
        assert ride.status == RideStatus.REQUESTED

    def test_requested_then_accepted(self) -> None:
        events = [
            self._make_requested_event(),
            RideEvent.create("ride-1", EventType.RIDE_ACCEPTED, data={"driver_id": "d1"}),
        ]
        ride = EventAggregationService.aggregate_events(events)
        assert ride.status == RideStatus.ACCEPTED
        assert ride.driver_id is not None

    def test_full_lifecycle(self) -> None:
        events = [
            self._make_requested_event(),
            RideEvent.create("ride-1", EventType.RIDE_ACCEPTED, data={"driver_id": "d1"}),
            RideEvent.create("ride-1", EventType.RIDE_STARTED),
            RideEvent.create(
                "ride-1",
                EventType.RIDE_COMPLETED,
                data={"distance_km": 5.0, "fare_cents": 1500},
            ),
        ]
        ride = EventAggregationService.aggregate_events(events)
        assert ride.status == RideStatus.COMPLETED
        assert ride.distance is not None
        assert ride.fare is not None

    def test_cancelled_ride(self) -> None:
        events = [
            self._make_requested_event(),
            RideEvent.create("ride-1", EventType.RIDE_CANCELLED, data={"reason": "no drivers"}),
        ]
        ride = EventAggregationService.aggregate_events(events)
        assert ride.status == RideStatus.CANCELLED

    def test_empty_events_raises(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            EventAggregationService.aggregate_events([])

    def test_wrong_first_event_raises(self) -> None:
        events = [RideEvent.create("ride-1", EventType.RIDE_ACCEPTED, data={"driver_id": "d1"})]
        with pytest.raises(ValueError, match="RIDE_REQUESTED"):
            EventAggregationService.aggregate_events(events)

    def test_gps_updates_ignored(self) -> None:
        events = [
            self._make_requested_event(),
            RideEvent.create("ride-1", EventType.GPS_UPDATE, data={"lat": 40.72, "lon": -74.01}),
            RideEvent.create("ride-1", EventType.GPS_UPDATE, data={"lat": 40.73, "lon": -74.00}),
        ]
        ride = EventAggregationService.aggregate_events(events)
        assert ride.status == RideStatus.REQUESTED  # unchanged by GPS
