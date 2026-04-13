"""Quality gates for data validation at adapter boundaries.

Validates events before Kafka publish and rides before S3 persist.
Fail-fast: invalid data is rejected with clear error messages.
"""

from __future__ import annotations

from ridestream.domain.entities import Ride, RideEvent
from ridestream.domain.value_objects import EventType


class ValidationError(Exception):
    """Raised when data fails quality validation."""


class SchemaValidator:
    """Validates RideEvent payloads against expected schema."""

    REQUIRED_FIELDS = {"event_id", "ride_id", "event_type", "timestamp"}
    VALID_EVENT_TYPES = {e.value for e in EventType}

    @classmethod
    def validate_event(cls, event: RideEvent) -> None:
        """Validate a RideEvent has all required fields and valid types.

        Args:
            event: The event to validate.

        Raises:
            ValidationError: If validation fails.
        """
        if not event.event_id:
            raise ValidationError("event_id is required")
        if not event.ride_id:
            raise ValidationError("ride_id is required")
        if event.event_type.value not in cls.VALID_EVENT_TYPES:
            raise ValidationError(f"Invalid event_type: {event.event_type}")

    @classmethod
    def validate_event_payload(cls, payload: dict[str, object]) -> None:
        """Validate a raw event dict before serialization.

        Args:
            payload: Raw event dictionary.

        Raises:
            ValidationError: If required fields are missing.
        """
        missing = cls.REQUIRED_FIELDS - set(payload.keys())
        if missing:
            raise ValidationError(f"Missing required fields: {missing}")

        event_type = payload.get("event_type")
        if event_type not in cls.VALID_EVENT_TYPES:
            raise ValidationError(f"Invalid event_type: {event_type}")


class DataQualityChecker:
    """Validates data invariants before persistence."""

    @staticmethod
    def validate_ride(ride: Ride) -> None:
        """Validate ride data invariants.

        Args:
            ride: The ride to validate.

        Raises:
            ValidationError: If any invariant is violated.
        """
        if not ride.passenger_id.value:
            raise ValidationError("passenger_id is required")

        lat = ride.pickup_location.latitude
        lon = ride.pickup_location.longitude
        if not (-90 <= lat <= 90):
            raise ValidationError(f"Invalid pickup latitude: {lat}")
        if not (-180 <= lon <= 180):
            raise ValidationError(f"Invalid pickup longitude: {lon}")

        lat = ride.dropoff_location.latitude
        lon = ride.dropoff_location.longitude
        if not (-90 <= lat <= 90):
            raise ValidationError(f"Invalid dropoff latitude: {lat}")
        if not (-180 <= lon <= 180):
            raise ValidationError(f"Invalid dropoff longitude: {lon}")

        if ride.distance is not None and ride.distance.value_km < 0:
            raise ValidationError(f"Negative distance: {ride.distance.value_km}")

        if ride.fare is not None and ride.fare.cents < 0:
            raise ValidationError(f"Negative fare: {ride.fare.cents}")


class DuplicateDetector:
    """Detects duplicate events using an in-memory set.

    For production, this would be backed by Redis or a database.
    """

    def __init__(self) -> None:
        self._seen: set[str] = set()

    def is_duplicate(self, event: RideEvent) -> bool:
        """Check if an event has been seen before.

        Uses event_id for deduplication.

        Args:
            event: The event to check.

        Returns:
            True if this event was already processed.
        """
        return event.event_id in self._seen

    def mark_seen(self, event: RideEvent) -> None:
        """Mark an event as processed.

        Args:
            event: The event to mark.
        """
        self._seen.add(event.event_id)

    def check_and_mark(self, event: RideEvent) -> bool:
        """Check for duplicate, mark as seen if new.

        Args:
            event: The event to check.

        Returns:
            True if this is a NEW event (not duplicate).
        """
        if self.is_duplicate(event):
            return False
        self.mark_seen(event)
        return True

    def clear(self) -> None:
        """Clear the seen set."""
        self._seen.clear()

    @property
    def count(self) -> int:
        """Number of tracked events."""
        return len(self._seen)
