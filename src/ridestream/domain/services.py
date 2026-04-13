"""Domain services for RideStream v2.

Pure business logic — static methods only, no external dependencies.
Services operate on domain entities and value objects.
"""

from __future__ import annotations

import math

from ridestream.domain.entities import Ride, RideEvent
from ridestream.domain.errors import InvalidFareError, InvalidLocationError
from ridestream.domain.value_objects import (
    Distance,
    DriverID,
    EventType,
    Location,
    Money,
    PassengerID,
)


class RideService:
    """Manages ride lifecycle operations.

    All methods are static — no instance state, no external dependencies.
    State transitions are delegated to Ride entity methods.
    """

    @staticmethod
    def create_ride(
        passenger_id: PassengerID,
        pickup: Location,
        dropoff: Location,
    ) -> Ride:
        """Create a new ride request.

        Args:
            passenger_id: The passenger requesting the ride.
            pickup: Pickup location.
            dropoff: Dropoff location.

        Returns:
            A new Ride in REQUESTED state.

        Raises:
            InvalidLocationError: If pickup equals dropoff.
        """
        if pickup == dropoff:
            raise InvalidLocationError("Pickup and dropoff locations cannot be the same")
        return Ride(
            passenger_id=passenger_id,
            pickup_location=pickup,
            dropoff_location=dropoff,
        )

    @staticmethod
    def accept_ride(ride: Ride, driver_id: DriverID) -> Ride:
        """Accept a ride request.

        Args:
            ride: The ride to accept.
            driver_id: The driver accepting.

        Returns:
            New Ride in ACCEPTED state.

        Raises:
            InvalidRideStateError: If ride is not in REQUESTED state.
        """
        return ride.accept(driver_id)

    @staticmethod
    def start_ride(ride: Ride) -> Ride:
        """Start a ride (driver picked up passenger).

        Args:
            ride: The ride to start.

        Returns:
            New Ride in IN_PROGRESS state.

        Raises:
            InvalidRideStateError: If ride is not in ACCEPTED state.
        """
        return ride.start()

    @staticmethod
    def complete_ride(ride: Ride, distance: Distance, fare: Money) -> Ride:
        """Complete a ride.

        Args:
            ride: The ride to complete.
            distance: Actual distance traveled.
            fare: Fare to charge.

        Returns:
            New Ride in COMPLETED state.

        Raises:
            InvalidRideStateError: If ride is not in IN_PROGRESS state.
        """
        return ride.complete(distance, fare)

    @staticmethod
    def cancel_ride(ride: Ride, reason: str) -> Ride:
        """Cancel a ride.

        Args:
            ride: The ride to cancel.
            reason: Cancellation reason.

        Returns:
            New Ride in CANCELLED state.

        Raises:
            InvalidRideStateError: If ride is already terminal.
        """
        return ride.cancel(reason)

    @staticmethod
    def validate_location(lat: float, lon: float) -> bool:
        """Validate coordinate bounds.

        Args:
            lat: Latitude (-90 to 90).
            lon: Longitude (-180 to 180).

        Returns:
            True if valid.

        Raises:
            InvalidLocationError: If coordinates are out of range.
        """
        if not (-90 <= lat <= 90):
            raise InvalidLocationError(f"Latitude out of range: {lat}")
        if not (-180 <= lon <= 180):
            raise InvalidLocationError(f"Longitude out of range: {lon}")
        return True


class FareCalculationService:
    """Calculates ride fares.

    Pure calculation logic — no external dependencies.
    """

    @staticmethod
    def calculate_fare(
        distance: Distance,
        base_price: Money,
        rate_per_km: Money,
        surge_multiplier: float = 1.0,
    ) -> Money:
        """Calculate total fare for a ride.

        Formula: base_price + (distance_km * rate_per_km * surge_multiplier)

        Args:
            distance: Distance traveled.
            base_price: Base fare (e.g., 250 cents = $2.50).
            rate_per_km: Per-kilometer rate (e.g., 150 cents = $1.50/km).
            surge_multiplier: Surge pricing multiplier (1.0 = no surge).

        Returns:
            Total fare in cents.

        Raises:
            InvalidFareError: If surge_multiplier is invalid.
        """
        if surge_multiplier < 1.0:
            raise InvalidFareError(f"Surge multiplier must be >= 1.0, got {surge_multiplier}")
        if surge_multiplier > 5.0:
            raise InvalidFareError(f"Surge multiplier must be <= 5.0, got {surge_multiplier}")

        distance_cost_cents = int(
            math.ceil(distance.value_km * rate_per_km.cents * surge_multiplier)
        )
        total_cents = base_price.cents + distance_cost_cents
        return Money(cents=total_cents, currency=base_price.currency)

    @staticmethod
    def calculate_surge_multiplier(demand_level: int) -> float:
        """Calculate surge pricing multiplier from demand level.

        Args:
            demand_level: Current demand (0-20+).

        Returns:
            Surge multiplier (1.0 to 3.0).
        """
        if demand_level < 5:
            return 1.0
        elif demand_level < 10:
            return 1.25
        elif demand_level < 15:
            return 1.5
        elif demand_level < 20:
            return 2.0
        else:
            return 3.0

    @staticmethod
    def estimate_fare(pickup: Location, dropoff: Location, base_cents: int = 250) -> Money:
        """Estimate fare based on haversine distance.

        Args:
            pickup: Pickup location.
            dropoff: Dropoff location.
            base_cents: Base fare in cents.

        Returns:
            Estimated fare.
        """
        distance_km = pickup.distance_to(dropoff)
        rate_per_km_cents = 150  # $1.50/km
        total = base_cents + int(math.ceil(distance_km * rate_per_km_cents))
        return Money(cents=total)


class EventAggregationService:
    """Reconstructs ride state from a sequence of events.

    Used for event sourcing — replays events to rebuild current state.
    """

    @staticmethod
    def aggregate_events(events: list[RideEvent]) -> Ride:
        """Reconstruct a Ride from its event history.

        Events must be in chronological order. The first event must be
        RIDE_REQUESTED (which creates the ride).

        Args:
            events: Ordered list of ride events.

        Returns:
            Ride in its current state after replaying all events.

        Raises:
            InvalidRideStateError: If events are inconsistent.
            ValueError: If events list is empty or first event is not RIDE_REQUESTED.
        """
        if not events:
            raise ValueError("Cannot aggregate empty event list")

        first = events[0]
        if first.event_type != EventType.RIDE_REQUESTED:
            raise ValueError(f"First event must be RIDE_REQUESTED, got {first.event_type.value}")

        # Extract ride creation data from first event
        ride = Ride(
            passenger_id=PassengerID(value=first.data.get("passenger_id", first.ride_id)),  # type: ignore[arg-type]
            pickup_location=Location(
                latitude=float(first.data.get("pickup_lat", 0.0)),  # type: ignore[arg-type]
                longitude=float(first.data.get("pickup_lon", 0.0)),  # type: ignore[arg-type]
            ),
            dropoff_location=Location(
                latitude=float(first.data.get("dropoff_lat", 0.0)),  # type: ignore[arg-type]
                longitude=float(first.data.get("dropoff_lon", 0.0)),  # type: ignore[arg-type]
            ),
        )

        # Replay remaining events
        for event in events[1:]:
            if event.event_type == EventType.RIDE_ACCEPTED:
                driver_id_str = str(event.data.get("driver_id", ""))
                ride = ride.accept(DriverID(value=driver_id_str))
            elif event.event_type == EventType.RIDE_STARTED:
                ride = ride.start()
            elif event.event_type == EventType.RIDE_COMPLETED:
                distance = Distance(value_km=float(event.data.get("distance_km", 0.0)))  # type: ignore[arg-type]
                fare = Money(cents=int(event.data.get("fare_cents", 0)))  # type: ignore[arg-type]
                ride = ride.complete(distance, fare)
            elif event.event_type == EventType.RIDE_CANCELLED:
                reason = str(event.data.get("reason", "unknown"))
                ride = ride.cancel(reason)
            # GPS_UPDATE events don't change ride state

        return ride
