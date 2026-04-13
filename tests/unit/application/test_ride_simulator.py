"""Unit tests for ride simulator."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from ridestream.application.generators import LocationGenerator
from ridestream.application.ride_simulator import RideSimulator
from ridestream.config import Config, Environment
from ridestream.domain.entities import Ride, RideEvent
from ridestream.domain.value_objects import EventType, RideStatus

# ── Fixtures ──────────────────────────────────────────────


@pytest.fixture
def mock_publisher() -> MagicMock:
    return MagicMock()


@pytest.fixture
def config() -> Config:
    return Config(environment=Environment.LOCAL)


@pytest.fixture
def simulator(mock_publisher: MagicMock, config: Config) -> RideSimulator:
    return RideSimulator(
        event_publisher=mock_publisher,
        config=config,
    )


# ── generate_ride ─────────────────────────────────────────


class TestGenerateRide:
    def test_returns_ride(self, simulator: RideSimulator) -> None:
        ride = simulator.generate_ride()
        assert isinstance(ride, Ride)

    def test_ride_is_requested(self, simulator: RideSimulator) -> None:
        ride = simulator.generate_ride()
        assert ride.status == RideStatus.REQUESTED

    def test_ride_has_locations(self, simulator: RideSimulator) -> None:
        ride = simulator.generate_ride()
        assert ride.pickup_location is not None
        assert ride.dropoff_location is not None
        assert ride.pickup_location != ride.dropoff_location

    def test_ride_has_passenger(self, simulator: RideSimulator) -> None:
        ride = simulator.generate_ride()
        assert ride.passenger_id is not None

    def test_generate_1000_rides(self, simulator: RideSimulator) -> None:
        rides = [simulator.generate_ride() for _ in range(1000)]
        assert len(rides) == 1000
        ids = {str(r.id) for r in rides}
        assert len(ids) == 1000  # all unique

    def test_custom_location_generator(self, mock_publisher: MagicMock, config: Config) -> None:
        gen = LocationGenerator(center_lat=40.0, center_lon=-74.0)
        sim = RideSimulator(mock_publisher, config, location_generator=gen)
        ride = sim.generate_ride()
        assert 39.9 < ride.pickup_location.latitude < 40.1


# ── simulate_lifecycle ────────────────────────────────────


class TestSimulateLifecycle:
    def test_produces_four_events(self, simulator: RideSimulator) -> None:
        ride = simulator.generate_ride()
        events = list(simulator.simulate_lifecycle(ride))
        assert len(events) == 4

    def test_event_order(self, simulator: RideSimulator) -> None:
        ride = simulator.generate_ride()
        events = list(simulator.simulate_lifecycle(ride))
        assert events[0].event_type == EventType.RIDE_REQUESTED
        assert events[1].event_type == EventType.RIDE_ACCEPTED
        assert events[2].event_type == EventType.RIDE_STARTED
        assert events[3].event_type == EventType.RIDE_COMPLETED

    def test_all_events_same_ride_id(self, simulator: RideSimulator) -> None:
        ride = simulator.generate_ride()
        events = list(simulator.simulate_lifecycle(ride))
        ride_id = str(ride.id)
        assert all(e.ride_id == ride_id for e in events)

    def test_requested_event_has_locations(self, simulator: RideSimulator) -> None:
        ride = simulator.generate_ride()
        events = list(simulator.simulate_lifecycle(ride))
        data = events[0].data
        assert "pickup_lat" in data
        assert "pickup_lon" in data
        assert "dropoff_lat" in data
        assert "dropoff_lon" in data
        assert "passenger_id" in data

    def test_accepted_event_has_driver(self, simulator: RideSimulator) -> None:
        ride = simulator.generate_ride()
        events = list(simulator.simulate_lifecycle(ride))
        assert "driver_id" in events[1].data

    def test_completed_event_has_metrics(self, simulator: RideSimulator) -> None:
        ride = simulator.generate_ride()
        events = list(simulator.simulate_lifecycle(ride))
        data = events[3].data
        assert "distance_km" in data
        assert "fare_cents" in data
        assert "surge_multiplier" in data
        assert "duration_seconds" in data

    def test_completed_event_positive_fare(self, simulator: RideSimulator) -> None:
        ride = simulator.generate_ride()
        events = list(simulator.simulate_lifecycle(ride))
        assert events[3].data["fare_cents"] > 0  # type: ignore[operator]

    def test_events_are_ride_events(self, simulator: RideSimulator) -> None:
        ride = simulator.generate_ride()
        events = list(simulator.simulate_lifecycle(ride))
        assert all(isinstance(e, RideEvent) for e in events)

    def test_event_ids_unique(self, simulator: RideSimulator) -> None:
        ride = simulator.generate_ride()
        events = list(simulator.simulate_lifecycle(ride))
        ids = [e.event_id for e in events]
        assert len(set(ids)) == 4


# ── run ───────────────────────────────────────────────────


class TestRun:
    def test_run_publishes_events(
        self, simulator: RideSimulator, mock_publisher: MagicMock
    ) -> None:
        rides = simulator.run(num_rides=5)
        assert len(rides) == 5
        # 4 events per ride * 5 rides = 20 publish calls
        assert mock_publisher.publish.call_count == 20

    def test_run_returns_rides(self, simulator: RideSimulator) -> None:
        rides = simulator.run(num_rides=10)
        assert len(rides) == 10
        assert all(isinstance(r, Ride) for r in rides)

    def test_run_zero_rides(self, simulator: RideSimulator) -> None:
        rides = simulator.run(num_rides=0)
        assert rides == []

    def test_run_1000_rides(self, simulator: RideSimulator, mock_publisher: MagicMock) -> None:
        rides = simulator.run(num_rides=1000)
        assert len(rides) == 1000
        assert mock_publisher.publish.call_count == 4000

    def test_published_events_are_ride_events(
        self, simulator: RideSimulator, mock_publisher: MagicMock
    ) -> None:
        simulator.run(num_rides=1)
        for call in mock_publisher.publish.call_args_list:
            event = call[0][0]
            assert isinstance(event, RideEvent)
