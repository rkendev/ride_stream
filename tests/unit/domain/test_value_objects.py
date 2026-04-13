"""Unit tests for domain value objects."""

import pytest
from pydantic import ValidationError

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

# ── RideID ────────────────────────────────────────────────


class TestRideID:
    def test_create_default(self) -> None:
        rid = RideID()
        assert len(rid.value) == 36  # UUID format

    def test_create_with_value(self) -> None:
        rid = RideID(value="ride-123")
        assert rid.value == "ride-123"

    def test_empty_value_raises(self) -> None:
        with pytest.raises(ValueError, match="cannot be empty"):
            RideID(value="   ")

    def test_immutable(self) -> None:
        rid = RideID(value="ride-123")
        with pytest.raises(ValidationError):
            rid.value = "changed"  # type: ignore[misc]

    def test_equality(self) -> None:
        a = RideID(value="ride-123")
        b = RideID(value="ride-123")
        assert a == b

    def test_inequality(self) -> None:
        a = RideID(value="ride-123")
        b = RideID(value="ride-456")
        assert a != b

    def test_str(self) -> None:
        rid = RideID(value="ride-123")
        assert str(rid) == "ride-123"

    def test_hashable(self) -> None:
        rid = RideID(value="ride-123")
        assert hash(rid) == hash("ride-123")
        s = {rid}
        assert rid in s


# ── DriverID / PassengerID ────────────────────────────────


class TestDriverID:
    def test_create_default(self) -> None:
        did = DriverID()
        assert len(did.value) == 36

    def test_empty_raises(self) -> None:
        with pytest.raises(ValueError):
            DriverID(value="")

    def test_equality(self) -> None:
        a = DriverID(value="d1")
        b = DriverID(value="d1")
        assert a == b


class TestPassengerID:
    def test_create_default(self) -> None:
        pid = PassengerID()
        assert len(pid.value) == 36

    def test_empty_raises(self) -> None:
        with pytest.raises(ValueError):
            PassengerID(value="  ")


# ── Location ─────────────────────────────────────────────


class TestLocation:
    def test_valid_location(self) -> None:
        loc = Location(latitude=40.7128, longitude=-74.0060)
        assert loc.latitude == 40.7128
        assert loc.longitude == -74.0060

    def test_latitude_too_high(self) -> None:
        with pytest.raises(ValueError):
            Location(latitude=91.0, longitude=0.0)

    def test_latitude_too_low(self) -> None:
        with pytest.raises(ValueError):
            Location(latitude=-91.0, longitude=0.0)

    def test_longitude_too_high(self) -> None:
        with pytest.raises(ValueError):
            Location(latitude=0.0, longitude=181.0)

    def test_longitude_too_low(self) -> None:
        with pytest.raises(ValueError):
            Location(latitude=0.0, longitude=-181.0)

    def test_boundary_values(self) -> None:
        loc = Location(latitude=90.0, longitude=180.0)
        assert loc.latitude == 90.0
        loc2 = Location(latitude=-90.0, longitude=-180.0)
        assert loc2.latitude == -90.0

    def test_immutable(self) -> None:
        loc = Location(latitude=40.0, longitude=-74.0)
        with pytest.raises(ValidationError):
            loc.latitude = 50.0  # type: ignore[misc]

    def test_distance_to_same_point(self) -> None:
        loc = Location(latitude=40.7128, longitude=-74.0060)
        assert loc.distance_to(loc) == pytest.approx(0.0, abs=0.001)

    def test_distance_to_known_points(self) -> None:
        # NYC to LA ~ 3944 km
        nyc = Location(latitude=40.7128, longitude=-74.0060)
        la = Location(latitude=34.0522, longitude=-118.2437)
        dist = nyc.distance_to(la)
        assert 3900 < dist < 4000

    def test_distance_symmetry(self) -> None:
        a = Location(latitude=40.0, longitude=-74.0)
        b = Location(latitude=34.0, longitude=-118.0)
        assert a.distance_to(b) == pytest.approx(b.distance_to(a))

    def test_equality(self) -> None:
        a = Location(latitude=40.0, longitude=-74.0)
        b = Location(latitude=40.0, longitude=-74.0)
        assert a == b

    def test_hashable(self) -> None:
        loc = Location(latitude=40.0, longitude=-74.0)
        s = {loc}
        assert loc in s


# ── Distance ─────────────────────────────────────────────


class TestDistance:
    def test_valid_distance(self) -> None:
        d = Distance(value_km=5.0)
        assert d.value_km == 5.0

    def test_zero_distance(self) -> None:
        d = Distance(value_km=0.0)
        assert d.value_km == 0.0

    def test_negative_raises(self) -> None:
        with pytest.raises(ValueError):
            Distance(value_km=-1.0)

    def test_meters_property(self) -> None:
        d = Distance(value_km=5.0)
        assert d.meters == 5000.0

    def test_rounding(self) -> None:
        d = Distance(value_km=5.12345)
        assert d.value_km == 5.12

    def test_immutable(self) -> None:
        d = Distance(value_km=5.0)
        with pytest.raises(ValidationError):
            d.value_km = 10.0  # type: ignore[misc]


# ── Money ─────────────────────────────────────────────────


class TestMoney:
    def test_valid_money(self) -> None:
        m = Money(cents=1500)
        assert m.cents == 1500
        assert m.currency == "USD"

    def test_dollars_property(self) -> None:
        m = Money(cents=1550)
        assert m.dollars == 15.50

    def test_zero_cents(self) -> None:
        m = Money(cents=0)
        assert m.cents == 0

    def test_negative_raises(self) -> None:
        with pytest.raises(ValueError):
            Money(cents=-100)

    def test_custom_currency(self) -> None:
        m = Money(cents=1000, currency="EUR")
        assert m.currency == "EUR"

    def test_invalid_currency(self) -> None:
        with pytest.raises(ValueError):
            Money(cents=100, currency="invalid")

    def test_add_same_currency(self) -> None:
        a = Money(cents=100)
        b = Money(cents=200)
        result = a.add(b)
        assert result.cents == 300

    def test_add_different_currency_raises(self) -> None:
        a = Money(cents=100, currency="USD")
        b = Money(cents=200, currency="EUR")
        with pytest.raises(ValueError, match="Cannot add"):
            a.add(b)

    def test_immutable(self) -> None:
        m = Money(cents=100)
        with pytest.raises(ValidationError):
            m.cents = 200  # type: ignore[misc]

    def test_equality(self) -> None:
        a = Money(cents=100)
        b = Money(cents=100)
        assert a == b

    def test_inequality_cents(self) -> None:
        a = Money(cents=100)
        b = Money(cents=200)
        assert a != b

    def test_inequality_currency(self) -> None:
        a = Money(cents=100, currency="USD")
        b = Money(cents=100, currency="EUR")
        assert a != b


# ── Timestamp ─────────────────────────────────────────────


class TestTimestamp:
    def test_default_is_utc(self) -> None:
        ts = Timestamp()
        assert ts.value.tzinfo is not None

    def test_to_iso(self) -> None:
        ts = Timestamp()
        iso = ts.to_iso()
        assert "T" in iso
        assert "+" in iso or "Z" in iso

    def test_naive_datetime_gets_utc(self) -> None:
        from datetime import datetime

        naive = datetime(2024, 1, 1, 12, 0, 0)  # noqa: DTZ001
        ts = Timestamp(value=naive)
        assert ts.value.tzinfo is not None

    def test_immutable(self) -> None:
        ts = Timestamp()
        with pytest.raises(ValidationError):
            ts.value = None  # type: ignore[assignment]


# ── Enums ─────────────────────────────────────────────────


class TestEventType:
    def test_values(self) -> None:
        assert EventType.RIDE_REQUESTED == "ride_requested"
        assert EventType.RIDE_COMPLETED == "ride_completed"
        assert EventType.GPS_UPDATE == "gps_update"

    def test_all_members(self) -> None:
        assert len(EventType) == 6


class TestRideStatus:
    def test_values(self) -> None:
        assert RideStatus.REQUESTED == "requested"
        assert RideStatus.COMPLETED == "completed"

    def test_all_members(self) -> None:
        assert len(RideStatus) == 5


# ── generate_event_id ─────────────────────────────────────


class TestGenerateEventID:
    def test_deterministic(self) -> None:
        id1 = generate_event_id("ride-1", "ride_requested", "2024-01-01T00:00:00Z")
        id2 = generate_event_id("ride-1", "ride_requested", "2024-01-01T00:00:00Z")
        assert id1 == id2

    def test_different_inputs(self) -> None:
        id1 = generate_event_id("ride-1", "ride_requested", "2024-01-01T00:00:00Z")
        id2 = generate_event_id("ride-2", "ride_requested", "2024-01-01T00:00:00Z")
        assert id1 != id2

    def test_length(self) -> None:
        eid = generate_event_id("ride-1", "ride_requested", "2024-01-01T00:00:00Z")
        assert len(eid) == 32
