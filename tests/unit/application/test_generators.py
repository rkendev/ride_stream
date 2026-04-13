"""Unit tests for data generators."""

from __future__ import annotations

from ridestream.application.generators import (
    DistanceCalculator,
    FareGenerator,
    LocationGenerator,
)
from ridestream.domain.value_objects import Distance, Location, Money

# ── LocationGenerator ─────────────────────────────────────


class TestLocationGenerator:
    def test_default_bounds(self) -> None:
        gen = LocationGenerator()
        loc = gen.random_location()
        assert 37.72 < loc.latitude < 37.83
        assert -122.47 < loc.longitude < -122.37

    def test_custom_bounds(self) -> None:
        gen = LocationGenerator(center_lat=40.0, center_lon=-74.0, radius_deg=0.01)
        loc = gen.random_location()
        assert 39.99 <= loc.latitude <= 40.01
        assert -74.01 <= loc.longitude <= -73.99

    def test_random_pair_different(self) -> None:
        gen = LocationGenerator()
        pickup, dropoff = gen.random_pair()
        assert pickup != dropoff

    def test_generates_valid_locations(self) -> None:
        gen = LocationGenerator()
        for _ in range(100):
            loc = gen.random_location()
            assert -90 <= loc.latitude <= 90
            assert -180 <= loc.longitude <= 180

    def test_1000_locations_no_error(self) -> None:
        gen = LocationGenerator()
        locations = [gen.random_location() for _ in range(1000)]
        assert len(locations) == 1000


# ── DistanceCalculator ────────────────────────────────────


class TestDistanceCalculator:
    def test_same_point_zero(self) -> None:
        loc = Location(latitude=37.77, longitude=-122.42)
        d = DistanceCalculator.calculate(loc, loc)
        assert d.value_km == 0.0

    def test_known_distance(self) -> None:
        # SF to Oakland ~13 km
        sf = Location(latitude=37.7749, longitude=-122.4194)
        oak = Location(latitude=37.8044, longitude=-122.2712)
        d = DistanceCalculator.calculate(sf, oak)
        assert 10 < d.value_km < 20

    def test_returns_distance_type(self) -> None:
        a = Location(latitude=37.77, longitude=-122.42)
        b = Location(latitude=37.78, longitude=-122.41)
        d = DistanceCalculator.calculate(a, b)
        assert isinstance(d, Distance)

    def test_symmetry(self) -> None:
        a = Location(latitude=37.77, longitude=-122.42)
        b = Location(latitude=37.80, longitude=-122.27)
        assert (
            DistanceCalculator.calculate(a, b).value_km
            == DistanceCalculator.calculate(b, a).value_km
        )


# ── FareGenerator ─────────────────────────────────────────


class TestFareGenerator:
    def test_base_fare(self) -> None:
        gen = FareGenerator(base_fare_cents=250, per_km_cents=150, min_fare_cents=500)
        fare = gen.calculate(Distance(value_km=0.0))
        assert fare.cents == 500  # minimum fare

    def test_per_km(self) -> None:
        gen = FareGenerator(base_fare_cents=250, per_km_cents=150, min_fare_cents=0)
        fare = gen.calculate(Distance(value_km=10.0))
        # 250 + ceil(10 * 150 * 1.0) = 250 + 1500 = 1750
        assert fare.cents == 1750

    def test_surge_multiplier(self) -> None:
        gen = FareGenerator(base_fare_cents=250, per_km_cents=100, min_fare_cents=0)
        fare = gen.calculate(Distance(value_km=10.0), surge_multiplier=2.0)
        # 250 + ceil(10 * 100 * 2.0) = 250 + 2000 = 2250
        assert fare.cents == 2250

    def test_minimum_fare(self) -> None:
        gen = FareGenerator(base_fare_cents=100, per_km_cents=50, min_fare_cents=500)
        fare = gen.calculate(Distance(value_km=1.0))
        # raw = 100 + 50 = 150, but min is 500
        assert fare.cents == 500

    def test_returns_money_type(self) -> None:
        gen = FareGenerator()
        fare = gen.calculate(Distance(value_km=5.0))
        assert isinstance(fare, Money)
        assert fare.currency == "USD"

    def test_random_surge_range(self) -> None:
        surges = {FareGenerator.random_surge() for _ in range(1000)}
        assert all(1.0 <= s <= 3.0 for s in surges)
        assert 1.0 in surges  # most common

    def test_no_negative_fare(self) -> None:
        gen = FareGenerator()
        for _ in range(100):
            d = Distance(value_km=round(0.01 * (_ + 1), 2))
            fare = gen.calculate(d, surge_multiplier=FareGenerator.random_surge())
            assert fare.cents >= 0
