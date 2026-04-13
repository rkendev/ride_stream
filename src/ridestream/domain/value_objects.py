"""Domain value objects for RideStream v2.

Immutable types representing domain concepts: identifiers, locations,
distances, monetary amounts, timestamps, and event types.
"""

from __future__ import annotations

import hashlib
import math
import uuid
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class EventType(StrEnum):
    """Types of events in the ride lifecycle.

    RIDE_REQUESTED: Passenger requests a ride.
    RIDE_ACCEPTED: Driver accepts the ride request.
    RIDE_STARTED: Ride begins (driver picks up passenger).
    RIDE_COMPLETED: Ride ends (passenger dropped off).
    RIDE_CANCELLED: Ride cancelled by either party.
    GPS_UPDATE: Periodic location update during ride.
    """

    RIDE_REQUESTED = "ride_requested"
    RIDE_ACCEPTED = "ride_accepted"
    RIDE_STARTED = "ride_started"
    RIDE_COMPLETED = "ride_completed"
    RIDE_CANCELLED = "ride_cancelled"
    GPS_UPDATE = "gps_update"


class RideStatus(StrEnum):
    """Possible states of a ride in the system.

    REQUESTED: Rider requested, waiting for driver acceptance.
    ACCEPTED: Driver accepted, en route to pickup.
    IN_PROGRESS: Ride underway, driver has passenger.
    COMPLETED: Ride finished successfully.
    CANCELLED: Ride cancelled before completion.
    """

    REQUESTED = "requested"
    ACCEPTED = "accepted"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class RideID(BaseModel):
    """Unique ride identifier."""

    model_config = ConfigDict(frozen=True)

    value: str = Field(default_factory=lambda: str(uuid.uuid4()))

    @field_validator("value")
    @classmethod
    def validate_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("RideID cannot be empty")
        return v

    def __str__(self) -> str:
        return self.value

    def __hash__(self) -> int:
        return hash(self.value)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, RideID):
            return self.value == other.value
        return NotImplemented


class DriverID(BaseModel):
    """Unique driver identifier."""

    model_config = ConfigDict(frozen=True)

    value: str = Field(default_factory=lambda: str(uuid.uuid4()))

    @field_validator("value")
    @classmethod
    def validate_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("DriverID cannot be empty")
        return v

    def __str__(self) -> str:
        return self.value

    def __hash__(self) -> int:
        return hash(self.value)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, DriverID):
            return self.value == other.value
        return NotImplemented


class PassengerID(BaseModel):
    """Unique passenger identifier."""

    model_config = ConfigDict(frozen=True)

    value: str = Field(default_factory=lambda: str(uuid.uuid4()))

    @field_validator("value")
    @classmethod
    def validate_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("PassengerID cannot be empty")
        return v

    def __str__(self) -> str:
        return self.value

    def __hash__(self) -> int:
        return hash(self.value)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, PassengerID):
            return self.value == other.value
        return NotImplemented


class Location(BaseModel):
    """Geographic coordinate with validation.

    Args:
        latitude: Latitude in degrees (-90 to 90).
        longitude: Longitude in degrees (-180 to 180).
    """

    model_config = ConfigDict(frozen=True)

    latitude: float = Field(ge=-90.0, le=90.0)
    longitude: float = Field(ge=-180.0, le=180.0)

    def distance_to(self, other: Location) -> float:
        """Calculate haversine distance to another location in kilometers.

        Args:
            other: Target location.

        Returns:
            Distance in kilometers.
        """
        r = 6371.0  # Earth radius in km
        lat1 = math.radians(self.latitude)
        lon1 = math.radians(self.longitude)
        lat2 = math.radians(other.latitude)
        lon2 = math.radians(other.longitude)

        dlat = lat2 - lat1
        dlon = lon2 - lon1

        a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

        return r * c

    def __hash__(self) -> int:
        return hash((self.latitude, self.longitude))

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Location):
            return self.latitude == other.latitude and self.longitude == other.longitude
        return NotImplemented


class Distance(BaseModel):
    """Distance measurement in kilometers, non-negative.

    Args:
        value_km: Distance in kilometers (must be >= 0).
    """

    model_config = ConfigDict(frozen=True)

    value_km: float = Field(ge=0.0)

    @field_validator("value_km")
    @classmethod
    def round_to_precision(cls, v: float) -> float:
        return round(v, 2)

    @property
    def meters(self) -> float:
        """Distance in meters."""
        return self.value_km * 1000.0

    def __hash__(self) -> int:
        return hash(self.value_km)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Distance):
            return self.value_km == other.value_km
        return NotImplemented


class Money(BaseModel):
    """Monetary amount in cents (integer precision, no floating point).

    Args:
        cents: Amount in cents (must be >= 0).
        currency: ISO 4217 currency code (default: USD).
    """

    model_config = ConfigDict(frozen=True)

    cents: int = Field(ge=0)
    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")

    @property
    def dollars(self) -> float:
        """Amount in dollars (or major currency unit)."""
        return self.cents / 100.0

    def add(self, other: Money) -> Money:
        """Add two money amounts (must be same currency)."""
        if self.currency != other.currency:
            raise ValueError(f"Cannot add {self.currency} and {other.currency}")
        return Money(cents=self.cents + other.cents, currency=self.currency)

    def __hash__(self) -> int:
        return hash((self.cents, self.currency))

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Money):
            return self.cents == other.cents and self.currency == other.currency
        return NotImplemented


class Timestamp(BaseModel):
    """Timezone-aware timestamp.

    Args:
        value: UTC datetime. Defaults to current UTC time.
    """

    model_config = ConfigDict(frozen=True)

    value: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("value")
    @classmethod
    def ensure_timezone_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            return v.replace(tzinfo=UTC)
        return v

    def to_iso(self) -> str:
        """ISO 8601 formatted string."""
        return self.value.isoformat()

    def __hash__(self) -> int:
        return hash(self.value)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Timestamp):
            return self.value == other.value
        return NotImplemented


def generate_event_id(ride_id: str, event_type: str, timestamp: str) -> str:
    """Generate deterministic event ID from components.

    Args:
        ride_id: The ride identifier.
        event_type: The event type string.
        timestamp: ISO timestamp string.

    Returns:
        SHA-256 hex digest (first 32 chars).
    """
    content = f"{ride_id}:{event_type}:{timestamp}"
    return hashlib.sha256(content.encode()).hexdigest()[:32]
