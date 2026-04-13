"""Data generators for ride simulation.

Pure functions — no external dependencies, no I/O.
Used by RideSimulator to create realistic test data.
"""

from __future__ import annotations

import math
import random

from ridestream.domain.value_objects import Distance, Location, Money


class LocationGenerator:
    """Generates random locations within city bounds.

    Default bounds: San Francisco (~37.7°N, ~-122.4°W ± 0.05°).
    """

    def __init__(
        self,
        center_lat: float = 37.7749,
        center_lon: float = -122.4194,
        radius_deg: float = 0.05,
    ) -> None:
        self._center_lat = center_lat
        self._center_lon = center_lon
        self._radius = radius_deg

    def random_location(self) -> Location:
        """Generate a random location within city bounds."""
        lat = self._center_lat + random.uniform(-self._radius, self._radius)
        lon = self._center_lon + random.uniform(-self._radius, self._radius)
        return Location(latitude=round(lat, 6), longitude=round(lon, 6))

    def random_pair(self) -> tuple[Location, Location]:
        """Generate a pickup/dropoff pair (guaranteed different)."""
        pickup = self.random_location()
        dropoff = self.random_location()
        # Ensure pickup != dropoff
        while pickup == dropoff:
            dropoff = self.random_location()
        return pickup, dropoff


class DistanceCalculator:
    """Calculates haversine distance between two locations."""

    @staticmethod
    def calculate(pickup: Location, dropoff: Location) -> Distance:
        """Calculate distance between two locations.

        Args:
            pickup: Start location.
            dropoff: End location.

        Returns:
            Distance in kilometers.
        """
        km = pickup.distance_to(dropoff)
        return Distance(value_km=round(km, 2))


class FareGenerator:
    """Generates fares based on distance and surge pricing."""

    def __init__(
        self,
        base_fare_cents: int = 250,
        per_km_cents: int = 150,
        min_fare_cents: int = 500,
    ) -> None:
        self._base = base_fare_cents
        self._per_km = per_km_cents
        self._min = min_fare_cents

    def calculate(
        self,
        distance: Distance,
        surge_multiplier: float = 1.0,
    ) -> Money:
        """Calculate fare for a ride.

        Args:
            distance: Trip distance.
            surge_multiplier: Surge pricing multiplier (1.0 = no surge).

        Returns:
            Fare in cents.
        """
        raw = self._base + int(math.ceil(distance.value_km * self._per_km * surge_multiplier))
        return Money(cents=max(raw, self._min))

    @staticmethod
    def random_surge() -> float:
        """Generate a random surge multiplier (1.0-2.5)."""
        return round(random.choice([1.0, 1.0, 1.0, 1.25, 1.5, 1.75, 2.0, 2.5]), 2)
