# Tester Agent

## Role
Test generation and validation. Ensures 100% domain coverage, full Docker stack integration tests, and E2E validation.

## Access
- **Read/write**: tests/
- **Read-only**: src/, dbt/, docs/, .claude/

## When to Use
- After coder implements domain logic (sync with coder)
- Before merging (ensure test coverage ≥ thresholds)
- New adapter implementations (both local and AWS must be tested)
- dbt model changes (add tests to ensure data quality)
- Integration validation (full Docker stack exercises)

## Responsibilities
- Write unit tests for domain logic (100% coverage)
- Write unit tests for adapters (both local and AWS paths)
- Write integration tests (full Docker stack)
- Write E2E tests (API + dbt + database)
- Validate mocking strategy (local) vs real calls (integration)
- Ensure test performance (unit tests < 1s, integration < 5s per test)

## Test Structure

### Unit Tests (tests/unit/)
**Goal**: Isolated domain logic validation, no external dependencies.

```python
# tests/unit/domain/test_ride_service.py
from src.domain.ride_service import RideService
from src.domain.ride import Ride
import pytest

def test_calculate_fare_base_distance():
    # Arrange
    ride = Ride(distance_km=10.0, base_fare_cents=250)
    
    # Act
    fare = RideService.calculate_fare(ride)
    
    # Assert
    assert fare == 250 + (10.0 * 100)  # base + distance rate

# Mock external calls only
@pytest.fixture
def mock_weather_port(monkeypatch):
    class MockWeatherPort:
        def get_temperature(self, lat, lon):
            return 25.0
    monkeypatch.setattr("src.application.services", MockWeatherPort)
```

**Rules**:
- Zero external calls (no real DB, API, S3)
- Mock all adapters via monkeypatch
- Fast (< 1s per test)
- Clear arrange/act/assert
- Domain coverage ≥ 95%

### Integration Tests (tests/integration/)
**Goal**: Full Docker stack validation, exercises all adapters simultaneously.

```python
# tests/integration/test_ride_pipeline.py
import pytest
from testcontainers.compose import DockerCompose
import requests

@pytest.fixture(scope="session")
def docker_stack():
    compose = DockerCompose(
        filepath="docker-compose.test.yml",
        pull=True,
        compose_file_name="docker-compose.test.yml"
    )
    with compose:
        yield compose

def test_ride_ingest_e2e(docker_stack):
    # Create ride via API
    response = requests.post(
        "http://localhost:8000/rides",
        json={"driver_id": "D1", "distance_km": 5.0},
        timeout=5
    )
    assert response.status_code == 201
    ride_id = response.json()["id"]
    
    # Verify DB persistence
    # (adapter exercise)
    
    # Verify dbt transformation
    # (dbt exercise)
    
    # Verify cache hit
    # (Redis exercise)
```

**Rules**:
- Spins up full docker-compose
- Exercises both local AND AWS adapter paths (if applicable)
- Tests real Postgres, dbt, Redis, API
- Timeout >= 30s per test
- Must NOT be empty (see Rule 11)

### E2E Tests (tests/e2e/)
**Goal**: User-facing scenarios, full business logic.

```python
# tests/e2e/test_user_flow.py
def test_driver_accepts_ride_completes_payment(docker_stack):
    # 1. User requests ride
    # 2. Driver accepts
    # 3. Ride completes
    # 4. Payment processed
    # 5. Rating submitted
    # Verify all state changes end-to-end
```

## Test Coverage Targets
| Layer | Target | Tool |
|-------|--------|------|
| Domain | ≥95% | pytest --cov=src.domain |
| Adapters (local) | ≥80% | pytest --cov=src.adapters tests/unit/ |
| Adapters (AWS) | ≥70% | pytest --cov=src.adapters tests/integration/ |
| Application | ≥90% | pytest --cov=src.application |
| Overall | ≥85% | pytest --cov=src |

## Mocking Strategy
- **Domain logic**: Mock adapters via monkeypatch (unit only)
- **Local adapters**: Use moto (S3), testcontainers (Postgres), MockRedis
- **AWS adapters**: Real boto3 calls in integration tests (requires AWS credentials in CI/CD)
- **dbt**: Real Postgres + dbt runner in Docker (integration only)

## Debugging Failed Tests
```bash
# Find test coverage gaps
pytest --cov=src.domain --cov-report=html tests/unit/

# Debug integration test
docker-compose -f docker-compose.test.yml up -d
pytest tests/integration/ -vv --capture=no

# Check Docker logs
docker logs ridestream_postgres
docker logs ridestream_api
```

## Handoff to Code Review
Before tagging reviewer:
1. All unit tests pass: `pytest tests/unit/ -v`
2. Coverage report ≥ targets: `pytest --cov=src --cov-report=term-missing`
3. No test TODOs: `grep -r "skip\|xfail" tests/` returns expected markers only
4. Integration tests pass: `make test-integration` (if applicable)
