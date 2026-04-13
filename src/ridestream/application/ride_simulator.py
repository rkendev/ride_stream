"""Ride simulator for generating realistic ride event streams.

Application layer — depends on ports (EventPublisher), never adapters.
Generates ride lifecycle sequences: REQUESTED → ACCEPTED → STARTED → COMPLETED.
"""

from __future__ import annotations

import logging
import random
from collections.abc import Iterator

from ridestream.application.generators import (
    DistanceCalculator,
    FareGenerator,
    LocationGenerator,
)
from ridestream.config import Config
from ridestream.domain.entities import Ride, RideEvent
from ridestream.domain.repositories import EventPublisher
from ridestream.domain.services import RideService
from ridestream.domain.value_objects import (
    DriverID,
    EventType,
    PassengerID,
)

logger = logging.getLogger(__name__)


class RideSimulator:
    """Generates and publishes simulated ride event streams.

    Args:
        event_publisher: Port for publishing events (Kafka/MSK).
        config: Application configuration.
        location_generator: Optional custom location generator.
        fare_generator: Optional custom fare generator.
    """

    def __init__(
        self,
        event_publisher: EventPublisher,
        config: Config,
        location_generator: LocationGenerator | None = None,
        fare_generator: FareGenerator | None = None,
    ) -> None:
        self._publisher = event_publisher
        self._config = config
        self._location_gen = location_generator or LocationGenerator()
        self._fare_gen = fare_generator or FareGenerator()

    def generate_ride(self) -> Ride:
        """Generate a new random ride.

        Returns:
            A Ride in REQUESTED state with random passenger, locations.
        """
        passenger = PassengerID()
        pickup, dropoff = self._location_gen.random_pair()
        return RideService.create_ride(passenger, pickup, dropoff)

    def simulate_lifecycle(self, ride: Ride) -> Iterator[RideEvent]:
        """Generate the full lifecycle of events for a ride.

        Produces events in order: REQUESTED → ACCEPTED → STARTED → COMPLETED.
        Each event includes relevant data payload.

        Args:
            ride: The ride to simulate.

        Yields:
            RideEvent instances in chronological order.
        """
        # 1. RIDE_REQUESTED
        requested_event = RideEvent.create(
            ride_id=str(ride.id),
            event_type=EventType.RIDE_REQUESTED,
            data={
                "passenger_id": str(ride.passenger_id),
                "pickup_lat": ride.pickup_location.latitude,
                "pickup_lon": ride.pickup_location.longitude,
                "dropoff_lat": ride.dropoff_location.latitude,
                "dropoff_lon": ride.dropoff_location.longitude,
            },
        )
        yield requested_event

        # 2. RIDE_ACCEPTED — passenger_id + driver_id propagated
        driver = DriverID()
        accepted_ride = ride.accept(driver)
        accepted_event = RideEvent.create(
            ride_id=str(ride.id),
            event_type=EventType.RIDE_ACCEPTED,
            data={
                "passenger_id": str(ride.passenger_id),
                "driver_id": str(driver),
            },
        )
        yield accepted_event

        # 3. RIDE_STARTED — passenger_id + driver_id propagated
        accepted_ride.start()  # validate state transition
        started_event = RideEvent.create(
            ride_id=str(ride.id),
            event_type=EventType.RIDE_STARTED,
            data={
                "passenger_id": str(ride.passenger_id),
                "driver_id": str(driver),
            },
        )
        yield started_event

        # 4. RIDE_COMPLETED — passenger_id + driver_id + fare + distance
        distance = DistanceCalculator.calculate(ride.pickup_location, ride.dropoff_location)
        surge = FareGenerator.random_surge()
        fare = self._fare_gen.calculate(distance, surge)

        completed_event = RideEvent.create(
            ride_id=str(ride.id),
            event_type=EventType.RIDE_COMPLETED,
            data={
                "passenger_id": str(ride.passenger_id),
                "driver_id": str(driver),
                "distance_km": distance.value_km,
                "fare_cents": fare.cents,
                "surge_multiplier": surge,
                "duration_seconds": random.randint(180, 3600),
            },
        )
        yield completed_event

    def run(self, num_rides: int = 100) -> list[Ride]:
        """Generate and publish multiple rides.

        Args:
            num_rides: Number of rides to simulate.

        Returns:
            List of generated rides.
        """
        rides: list[Ride] = []

        for i in range(num_rides):
            ride = self.generate_ride()
            rides.append(ride)

            for event in self.simulate_lifecycle(ride):
                self._publisher.publish(event)

            if (i + 1) % 100 == 0:
                logger.info("simulation_progress", extra={"rides_generated": i + 1})

        logger.info(
            "simulation_complete",
            extra={"total_rides": len(rides)},
        )
        return rides
