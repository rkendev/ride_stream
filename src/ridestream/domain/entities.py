"""Domain entities for RideStream v2.

Core aggregate roots: Ride (lifecycle state machine) and RideEvent (domain event).
All models use frozen=True (immutable after creation).
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

from ridestream.domain.errors import InvalidRideStateError
from ridestream.domain.value_objects import (
    Distance,
    DriverID,
    EventType,
    Location,
    Money,
    PassengerID,
    RideID,
    RideStatus,
    Timestamp,
    generate_event_id,
)

# Valid state transitions for the Ride state machine
_VALID_TRANSITIONS: dict[RideStatus, set[RideStatus]] = {
    RideStatus.REQUESTED: {RideStatus.ACCEPTED, RideStatus.CANCELLED},
    RideStatus.ACCEPTED: {RideStatus.IN_PROGRESS, RideStatus.CANCELLED},
    RideStatus.IN_PROGRESS: {RideStatus.COMPLETED, RideStatus.CANCELLED},
    RideStatus.COMPLETED: set(),
    RideStatus.CANCELLED: set(),
}


class RideEvent(BaseModel):
    """Domain event representing a state change in a ride.

    Args:
        event_id: Deterministic ID derived from ride_id + event_type + timestamp.
        ride_id: The ride this event belongs to.
        event_type: Type of event (RIDE_REQUESTED, RIDE_ACCEPTED, etc.).
        timestamp: When this event occurred.
        data: Additional event-specific payload.
    """

    model_config = ConfigDict(frozen=True)

    event_id: str
    ride_id: str
    event_type: EventType
    timestamp: Timestamp = Field(default_factory=Timestamp)
    data: dict[str, str | int | float | bool | None] = Field(default_factory=dict)

    @classmethod
    def create(
        cls,
        ride_id: str,
        event_type: EventType,
        data: dict[str, str | int | float | bool | None] | None = None,
        timestamp: Timestamp | None = None,
    ) -> RideEvent:
        """Factory method to create a RideEvent with deterministic ID.

        Args:
            ride_id: The ride identifier.
            event_type: The event type.
            data: Optional event payload.
            timestamp: Optional timestamp (defaults to now).

        Returns:
            A new RideEvent instance.
        """
        ts = timestamp or Timestamp()
        event_id = generate_event_id(ride_id, event_type.value, ts.to_iso())
        return cls(
            event_id=event_id,
            ride_id=ride_id,
            event_type=event_type,
            timestamp=ts,
            data=data or {},
        )


class Ride(BaseModel):
    """Aggregate root representing a ride in the system.

    Enforces state machine transitions. Once created, use accept(), start(),
    complete(), or cancel() to transition state — each returns a new Ride
    (immutable).

    Args:
        id: Unique ride identifier.
        passenger_id: The passenger who requested the ride.
        driver_id: The driver assigned (None until accepted).
        pickup_location: Where the passenger is picked up.
        dropoff_location: Where the passenger wants to go.
        status: Current ride state.
        distance: Actual distance traveled (set on completion).
        fare: Fare charged (set on completion).
        created_at: When the ride was requested.
        started_at: When the ride began (driver picked up passenger).
        completed_at: When the ride ended.
        cancel_reason: Reason for cancellation (if cancelled).
    """

    model_config = ConfigDict(frozen=True)

    id: RideID = Field(default_factory=RideID)
    passenger_id: PassengerID
    driver_id: DriverID | None = None
    pickup_location: Location
    dropoff_location: Location
    status: RideStatus = RideStatus.REQUESTED
    distance: Distance | None = None
    fare: Money | None = None
    created_at: Timestamp = Field(default_factory=Timestamp)
    started_at: Timestamp | None = None
    completed_at: Timestamp | None = None
    cancel_reason: str | None = None

    def _validate_transition(self, target: RideStatus) -> None:
        """Validate that the state transition is allowed.

        Args:
            target: The desired new state.

        Raises:
            InvalidRideStateError: If the transition is not allowed.
        """
        allowed = _VALID_TRANSITIONS.get(self.status, set())
        if target not in allowed:
            raise InvalidRideStateError(
                f"Cannot transition from {self.status.value} to {target.value}. "
                f"Allowed transitions: {[s.value for s in allowed]}"
            )

    def accept(self, driver_id: DriverID) -> Ride:
        """Accept the ride request (REQUESTED -> ACCEPTED).

        Args:
            driver_id: The driver accepting the ride.

        Returns:
            New Ride instance with ACCEPTED status.

        Raises:
            InvalidRideStateError: If ride is not in REQUESTED state.
        """
        self._validate_transition(RideStatus.ACCEPTED)
        return self.model_copy(
            update={
                "status": RideStatus.ACCEPTED,
                "driver_id": driver_id,
            }
        )

    def start(self) -> Ride:
        """Start the ride (ACCEPTED -> IN_PROGRESS).

        Returns:
            New Ride instance with IN_PROGRESS status.

        Raises:
            InvalidRideStateError: If ride is not in ACCEPTED state.
        """
        self._validate_transition(RideStatus.IN_PROGRESS)
        return self.model_copy(
            update={
                "status": RideStatus.IN_PROGRESS,
                "started_at": Timestamp(value=datetime.now(UTC)),
            }
        )

    def complete(self, distance: Distance, fare: Money) -> Ride:
        """Complete the ride (IN_PROGRESS -> COMPLETED).

        Args:
            distance: Total distance traveled.
            fare: Total fare charged.

        Returns:
            New Ride instance with COMPLETED status.

        Raises:
            InvalidRideStateError: If ride is not in IN_PROGRESS state.
        """
        self._validate_transition(RideStatus.COMPLETED)
        return self.model_copy(
            update={
                "status": RideStatus.COMPLETED,
                "distance": distance,
                "fare": fare,
                "completed_at": Timestamp(value=datetime.now(UTC)),
            }
        )

    def cancel(self, reason: str) -> Ride:
        """Cancel the ride (REQUESTED/ACCEPTED/IN_PROGRESS -> CANCELLED).

        Args:
            reason: Why the ride was cancelled.

        Returns:
            New Ride instance with CANCELLED status.

        Raises:
            InvalidRideStateError: If ride is already completed or cancelled.
        """
        self._validate_transition(RideStatus.CANCELLED)
        return self.model_copy(
            update={
                "status": RideStatus.CANCELLED,
                "cancel_reason": reason,
            }
        )

    @property
    def is_active(self) -> bool:
        """Whether the ride is currently active (not terminal)."""
        return self.status in {RideStatus.REQUESTED, RideStatus.ACCEPTED, RideStatus.IN_PROGRESS}

    @property
    def is_terminal(self) -> bool:
        """Whether the ride has reached a terminal state."""
        return self.status in {RideStatus.COMPLETED, RideStatus.CANCELLED}

    @property
    def estimated_distance_km(self) -> float:
        """Estimated distance from pickup to dropoff (haversine)."""
        return self.pickup_location.distance_to(self.dropoff_location)
