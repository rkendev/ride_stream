# Domain Layer Rules

## Core Principle
Domain logic is framework-agnostic, immutable, and independent. Zero external dependencies except standard library (pydantic, datetime, hashlib, math).

## Model Design
**Frozen Pydantic Models**
All domain models must use `frozen=True` to prevent post-instantiation modification.

```python
# GOOD
from pydantic import BaseModel, Field, ConfigDict
from uuid import uuid4

class Ride(BaseModel):
    model_config = ConfigDict(frozen=True)
    
    id: str = Field(default_factory=lambda: str(uuid4()))
    driver_id: str
    distance_km: float = Field(gt=0)
    status: RideStatus = RideStatus.REQUESTED

# BAD
class Ride(BaseModel):
    id: str
    driver_id: str
    # Missing frozen=True, allows setattr(ride, 'id', 'new_id')
```

## Allowed Imports
Only these external libraries are permitted:
- **pydantic** (BaseModel, Field, validator, ConfigDict)
- **datetime** (date, datetime, timedelta, timezone, UTC)
- **hashlib** (sha256, md5 for checksums)
- **math** (sqrt, pi, floor, ceil)
- **enum** (StrEnum)
- **typing** (Any, Optional, Union, List, Dict, Callable)
- **uuid** (UUID, uuid4)
- **decimal** (Decimal for monetary values)

Everything else (boto3, requests, sqlalchemy, fastapi) lives in adapters.

```python
# GOOD (domain/ride.py) - Python 3.12
from pydantic import BaseModel, Field, ConfigDict
from datetime import datetime, UTC
from enum import StrEnum

class RideStatus(StrEnum):
    REQUESTED = "requested"
    ACCEPTED = "accepted"
    COMPLETED = "completed"
    CANCELLED = "cancelled"

class Ride(BaseModel):
    model_config = ConfigDict(frozen=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

# BAD (domain/ride.py)
import boto3  # External adapter library
from sqlalchemy import Column, String  # Database library
import requests  # HTTP client

# BAD (Python 3.11 or deprecated patterns)
class RideStatus(str, Enum):  # ✗ Old pattern, use StrEnum
    REQUESTED = "requested"

created_at: datetime = Field(default_factory=datetime.utcnow)  # ✗ Deprecated
```

## Service Design
**Static Methods Only**
Domain services contain pure business logic, never state or dependencies.

```python
# GOOD
class RideService:
    @staticmethod
    def calculate_fare(distance_km: float, base_fare_cents: int, surge_multiplier: float = 1.0) -> int:
        """Calculate total fare in cents."""
        per_km_rate = 100  # cents per km
        base_total = base_fare_cents + int(distance_km * per_km_rate)
        return int(base_total * surge_multiplier)
    
    @staticmethod
    def validate_location(lat: float, lon: float) -> bool:
        """Validate coordinate bounds."""
        return -90 <= lat <= 90 and -180 <= lon <= 180
    
    @staticmethod
    def apply_surge_pricing(demand_level: int) -> float:
        """Calculate surge multiplier based on demand."""
        if demand_level < 5:
            return 1.0
        elif demand_level < 10:
            return 1.25
        else:
            return 1.5

# BAD (instance methods)
class RideService:
    def __init__(self, db_port):
        self.db_port = db_port  # State = not a domain service
    
    def calculate_fare(self, ride_id: str) -> int:
        # Depends on external state (db_port)
        ride = self.db_port.get_ride(ride_id)
        return ride.base_fare + ride.distance_km * 100

# BAD (dependencies injected)
class RideService:
    def __init__(self, repository_port: RepositoryPort):
        self._repo = repository_port  # Depends on adapter
    
    async def get_ride(self, ride_id: str):
        return await self._repo.fetch(ride_id)  # Async = I/O
```

## Value Objects
Small, immutable objects representing domain concepts.

```python
from pydantic import BaseModel, Field, ConfigDict
from math import sin, cos, sqrt, atan2, radians

class Location(BaseModel):
    model_config = ConfigDict(frozen=True)
    
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    
    def distance_to(self, other: "Location") -> float:
        """Haversine distance in km."""
        R = 6371  # Earth radius km
        lat1, lon1 = radians(self.latitude), radians(self.longitude)
        lat2, lon2 = radians(other.latitude), radians(other.longitude)
        dlat, dlon = lat2 - lat1, lon2 - lon1
        a = sin(dlat/2)**2 + cos(lat1)*cos(lat2)*sin(dlon/2)**2
        return 2 * R * atan2(sqrt(a), sqrt(1-a))

class FareCalculation(BaseModel):
    model_config = ConfigDict(frozen=True)
    
    base_fare_cents: int = Field(ge=0)
    distance_fare_cents: int = Field(ge=0)
    time_fare_cents: int = Field(ge=0)
    surge_multiplier: float = Field(ge=1.0)
    total_fare_cents: int = Field(ge=0)
```

## StrEnum Pattern (Python 3.11+)
Use `StrEnum` for string-based enumerations (replaces `str, Enum` pattern).

```python
from enum import StrEnum

# GOOD (Python 3.12)
class RideStatus(StrEnum):
    """Possible states of a ride in the system."""
    REQUESTED = "requested"
    ACCEPTED = "accepted"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"

class EventType(StrEnum):
    """Types of events in the ride lifecycle."""
    RIDE_REQUESTED = "ride_requested"
    RIDE_ACCEPTED = "ride_accepted"
    RIDE_STARTED = "ride_started"
    RIDE_COMPLETED = "ride_completed"
    RIDE_CANCELLED = "ride_cancelled"
    PAYMENT_PROCESSED = "payment_processed"

class PaymentMethod(StrEnum):
    """Accepted payment methods."""
    CREDIT_CARD = "credit_card"
    DEBIT_CARD = "debit_card"
    CASH = "cash"
    WALLET = "wallet"

# Usage
status = RideStatus.REQUESTED
event_type = EventType.RIDE_REQUESTED
# StrEnum values are strings natively
assert isinstance(status, str)  # True
assert status == "requested"    # True
```

## DateTime Handling (Python 3.12)
Always use `datetime.now(UTC)` instead of deprecated `datetime.utcnow()`.

```python
from datetime import datetime, UTC, timedelta
from pydantic import BaseModel, Field, ConfigDict

# GOOD (Python 3.12+)
class RideEvent(BaseModel):
    model_config = ConfigDict(frozen=True)
    
    ride_id: str
    event_type: EventType
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    
    @staticmethod
    def create_requested_event(ride_id: str) -> "RideEvent":
        """Create a RIDE_REQUESTED event with current UTC time."""
        return RideEvent(
            ride_id=ride_id,
            event_type=EventType.RIDE_REQUESTED,
            timestamp=datetime.now(UTC)
        )

# BAD (deprecated pattern)
class RideEvent(BaseModel):
    timestamp: datetime = Field(default_factory=datetime.utcnow)  # ✗ Deprecated

# BAD (naive datetime, no timezone)
class RideEvent(BaseModel):
    timestamp: datetime = Field(default_factory=datetime.now)  # ✗ No timezone info
```

## Validation Rules
Validation logic lives in Pydantic validators, not external service calls.

```python
from pydantic import BaseModel, Field, ConfigDict, field_validator

class Ride(BaseModel):
    model_config = ConfigDict(frozen=True)
    
    distance_km: float = Field(gt=0, le=500)
    duration_minutes: int = Field(ge=1)
    fare_cents: int = Field(ge=0)
    
    @field_validator("distance_km")
    @classmethod
    def validate_distance(cls, v: float) -> float:
        """Ensure distance is reasonable (>= 100 meters)."""
        if v < 0.1:
            raise ValueError("Distance too short (min 100m)")
        return round(v, 2)
    
    @field_validator("duration_minutes")
    @classmethod
    def validate_duration(cls, v: int) -> int:
        """Ensure duration is at least 1 minute."""
        if v < 1:
            raise ValueError("Duration must be at least 1 minute")
        return v

# NOT HERE (external validation)
class Ride(BaseModel):
    distance_km: float
    
    @field_validator("distance_km")
    @classmethod
    def validate_distance(cls, v: float, info: ValidationInfo) -> float:
        # BAD: Calls external service
        weather = WeatherServicePort().get_weather(...)
        if weather.rain:
            ...
```

## No External Service Calls
Domain logic never calls adapters, ports, or external services. Business rules are expressed as pure functions.

```python
# GOOD: Pure calculation
@staticmethod
def calculate_surge_multiplier(demand_level: int) -> float:
    """Apply surge pricing based on demand level."""
    if demand_level < 5:
        return 1.0
    elif demand_level < 10:
        return 1.25
    else:
        return 1.5

# BAD: Calls external service
@staticmethod
def calculate_surge_multiplier(weather_port: WeatherServicePort) -> float:
    weather = weather_port.get_current_weather()  # External call
    if weather.rain:
        return 1.5
```

## Entities
Entities have identity (unique ID) and a lifecycle. Immutable after creation (use `frozen=True`).

```python
from pydantic import BaseModel, Field, ConfigDict
from datetime import datetime, UTC
from uuid import uuid4

class RideEvent(BaseModel):
    """Entity: A discrete event in a ride's lifecycle."""
    model_config = ConfigDict(frozen=True)
    
    ride_id: str
    driver_id: str
    passenger_id: str
    event_type: EventType
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    
    # Location data
    pickup_latitude: float
    pickup_longitude: float
    dropoff_latitude: float
    dropoff_longitude: float
    
    # Ride metrics
    distance_km: float
    duration_minutes: int
    fare_cents: int
    surge_multiplier: float
    
    # Payment
    payment_method: PaymentMethod
    status: RideStatus
```

## Exceptions
Domain-specific exceptions only. Never catch system exceptions.

```python
# domain/exceptions.py
class DomainError(Exception):
    """Base domain error."""
    pass

class InvalidLocationError(DomainError):
    """Raised when location is outside service area or invalid bounds."""
    pass

class RideValidationError(DomainError):
    """Raised when ride data is invalid."""
    pass

class InvalidFareError(DomainError):
    """Raised when fare calculation produces invalid result."""
    pass

class InvalidDurationError(DomainError):
    """Raised when ride duration is invalid."""
    pass

# GOOD usage
def validate_location(lat: float, lon: float) -> None:
    if not (-90 <= lat <= 90):
        raise InvalidLocationError(f"Latitude out of range: {lat}")
    if not (-180 <= lon <= 180):
        raise InvalidLocationError(f"Longitude out of range: {lon}")

# BAD usage (system exception, no context)
def validate_location(lat: float, lon: float) -> None:
    assert -90 <= lat <= 90  # Use exceptions, not asserts
```

## Documentation
Every domain class and service has a docstring explaining business intent.

```python
from enum import StrEnum

class RideStatus(StrEnum):
    """Possible states of a ride in the system.
    
    REQUESTED: Rider requested, waiting for driver acceptance.
    ACCEPTED: Driver accepted, en route to pickup.
    IN_PROGRESS: Ride in progress, driver transporting passenger.
    COMPLETED: Ride finished, payment pending.
    CANCELLED: Ride cancelled before completion.
    """
    REQUESTED = "requested"
    ACCEPTED = "accepted"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"

class RideService:
    @staticmethod
    def calculate_fare(distance_km: float, base_fare_cents: int, surge_multiplier: float = 1.0) -> int:
        """Calculate total ride fare.
        
        Applies surge pricing to base fare + distance-based rate.
        
        Args:
            distance_km: Distance traveled in kilometers
            base_fare_cents: Starting fare in cents
            surge_multiplier: Surge pricing factor (>= 1.0)
        
        Returns:
            Total fare in cents (integer)
        
        Raises:
            ValueError: If distance_km < 0 or surge_multiplier < 1.0
        """
        if distance_km < 0:
            raise ValueError("Distance cannot be negative")
        if surge_multiplier < 1.0:
            raise ValueError("Surge multiplier must be >= 1.0")
        
        per_km_rate = 100  # cents per km
        base_total = base_fare_cents + int(distance_km * per_km_rate)
        return int(base_total * surge_multiplier)
```

## Testing Domain Logic
Domain tests are fast, isolated, and 100% pass/fail with no mocking.

```python
# tests/unit/domain/test_ride_service.py
from src.domain.ride_service import RideService
from src.domain.exceptions import InvalidLocationError
import pytest

def test_calculate_fare():
    """Unit test: basic fare calculation."""
    fare = RideService.calculate_fare(distance_km=5.0, base_fare_cents=250)
    assert fare == 250 + 500

def test_calculate_fare_with_surge():
    """Unit test: fare with surge pricing."""
    fare = RideService.calculate_fare(
        distance_km=5.0, 
        base_fare_cents=250, 
        surge_multiplier=1.5
    )
    assert fare == int((250 + 500) * 1.5)

def test_validate_location_valid():
    """Unit test: valid location passes."""
    assert RideService.validate_location(40.7128, -74.0060) is True

def test_validate_location_invalid_latitude():
    """Unit test: invalid latitude raises exception."""
    with pytest.raises(InvalidLocationError):
        RideService.validate_location(91.0, 0.0)

def test_apply_surge_pricing():
    """Unit test: surge multiplier based on demand."""
    assert RideService.apply_surge_pricing(demand_level=3) == 1.0
    assert RideService.apply_surge_pricing(demand_level=7) == 1.25
    assert RideService.apply_surge_pricing(demand_level=15) == 1.5
```

## Anti-Patterns (Never Do This)
- Instance variables or state in services
- Async/await (blocking operations)
- External API calls (boto3, requests, sqlalchemy)
- Bare except or except Exception
- f-string SQL queries
- Hardcoded configuration values
- Dependency injection (use ports in application layer)
- `datetime.utcnow()` (use `datetime.now(UTC)` instead)
- `str, Enum` pattern (use `StrEnum` instead)
- Naive datetime without timezone
- DateTime.now() without abstraction (inject TimePort if needed for testing)
- Logging (push logs to application layer via return types)
