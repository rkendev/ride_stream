# Application Layer Rules

## Core Principle
Application layer orchestrates domain logic and ports in a data pipeline. It depends ONLY on ports (abstractions), never on adapter implementations. This allows switching between local Docker stack and AWS without changing application code.

Application services coordinate the flow: ingest → validate → transform → store → query, delegating all I/O to ports.

## Dependency Rule (Critical)
**Never import from adapters/. Only import from domain/ and ports.**

```python
# GOOD: Depends on port abstractions
from src.domain.ride import Ride, RideIngestionError
from src.adapters.event_bus.port import EventBusPort
from src.adapters.storage.port import StoragePort

class RideIngestionService:
    def __init__(self, event_bus: EventBusPort, storage: StoragePort):
        self.event_bus = event_bus
        self.storage = storage

# BAD: Direct import of adapter (couples to Kafka, can't swap to local)
from src.adapters.event_bus.aws import EventBusAdapterKafka
from src.adapters.storage.aws import StorageAdapterS3

class RideIngestionService:
    def __init__(self):
        self.event_bus = EventBusAdapterKafka()  # WRONG: Concrete implementation
        self.storage = StorageAdapterS3()
```

## Service Design
Application services in RideStream v2 orchestrate three types of operations:
1. **Event reception** (EventBusPort)
2. **Data storage** (StoragePort in bronze/silver/gold buckets)
3. **Data transformation** (DbtPort for triggering model runs)
4. **Data querying** (QueryPort via Trino/Athena for analytics)

Keep orchestration thin—domain logic handles validation, services handle coordination.

### RideIngestionService
Receives raw ride events, validates via domain, publishes to EventBus, stores in bronze bucket.

```python
from src.domain.ride import Ride, RideStatus, RideValidationError
from src.adapters.event_bus.port import EventBusPort, EventBusError
from src.adapters.storage.port import StoragePort, StorageError
from src.application.exceptions import ApplicationError, IngestionError
import json
from loguru import logger
from datetime import datetime

class RideIngestionService:
    """Ingest raw ride events from external source."""
    
    def __init__(self, event_bus: EventBusPort, storage: StoragePort):
        """
        Args:
            event_bus: Port for publishing validated rides to Kafka/EventBridge
            storage: Port for storing to S3 bronze bucket
        """
        self.event_bus = event_bus
        self.storage = storage
    
    async def ingest_ride_event(self, raw_ride_data: dict) -> Ride:
        """
        Receive raw ride event, validate, publish, and store.
        
        Flow:
        1. Parse and validate against domain model
        2. Publish to EventBus (triggers async pipeline)
        3. Store in bronze bucket (raw data archive)
        
        Args:
            raw_ride_data: Raw ride event from external source
        
        Returns:
            Validated Ride domain object
        
        Raises:
            IngestionError: If validation or storage fails
        """
        try:
            logger.info(
                "ride_ingest_start",
                extra={
                    "raw_ride_id": raw_ride_data.get("ride_id"),
                    "timestamp": datetime.utcnow().isoformat()
                }
            )
            
            # 1. Validate and construct domain model (pure logic)
            ride = Ride(
                id=raw_ride_data["ride_id"],
                driver_id=raw_ride_data["driver_id"],
                distance_km=raw_ride_data["distance_km"],
                fare_cents=raw_ride_data["fare_cents"],
                status=RideStatus.PENDING
            )
            logger.info(
                "ride_validated",
                extra={"ride_id": ride.id}
            )
            
            # 2. Publish to EventBus (async I/O via port)
            await self.event_bus.publish(
                topic="rides.ingested",
                event={
                    "ride_id": ride.id,
                    "driver_id": ride.driver_id,
                    "distance_km": ride.distance_km,
                    "fare_cents": ride.fare_cents,
                    "timestamp": datetime.utcnow().isoformat()
                }
            )
            logger.info(
                "ride_published",
                extra={"ride_id": ride.id, "topic": "rides.ingested"}
            )
            
            # 3. Store in bronze bucket (raw archive)
            bronze_key = f"rides/bronze/{ride.id}.json"
            await self.storage.put(
                bucket="ridestream-bronze",
                key=bronze_key,
                data=json.dumps({
                    "ride_id": ride.id,
                    "driver_id": ride.driver_id,
                    "distance_km": ride.distance_km,
                    "fare_cents": ride.fare_cents,
                    "status": ride.status.value,
                    "ingested_at": datetime.utcnow().isoformat()
                }).encode()
            )
            logger.info(
                "ride_ingested",
                extra={
                    "ride_id": ride.id,
                    "bronze_key": bronze_key
                }
            )
            
            return ride
        
        except RideValidationError as e:
            logger.error(
                "ride_validation_failed",
                extra={"error": str(e), "raw_data": raw_ride_data},
                exc_info=True
            )
            raise IngestionError(f"Ride validation failed: {e}") from e
        
        except EventBusError as e:
            logger.error(
                "ride_publish_failed",
                extra={
                    "ride_id": raw_ride_data.get("ride_id"),
                    "error": str(e)
                },
                exc_info=True
            )
            raise IngestionError(f"Failed to publish ride to EventBus: {e}") from e
        
        except StorageError as e:
            logger.error(
                "ride_storage_failed",
                extra={
                    "ride_id": raw_ride_data.get("ride_id"),
                    "error": str(e)
                },
                exc_info=True
            )
            raise IngestionError(f"Failed to store ride in bronze bucket: {e}") from e
        
        except Exception as e:
            logger.error(
                "ride_ingest_unexpected_error",
                extra={"error": str(e), "raw_data": raw_ride_data},
                exc_info=True
            )
            raise ApplicationError(f"Unexpected ingest error: {e}") from e
```

### RideTransformationService
Reads from bronze bucket, applies domain transformations, writes to silver bucket, triggers dbt.

```python
from src.domain.ride import Ride
from src.domain.ride_service import RideService
from src.adapters.storage.port import StoragePort, StorageError
from src.adapters.dbt.port import DbtPort, DbtError
from src.application.exceptions import ApplicationError, TransformationError
import json
from loguru import logger
from datetime import datetime
from typing import List

class RideTransformationService:
    """Transform raw rides (bronze) to validated/enriched rides (silver)."""
    
    def __init__(self, storage: StoragePort, dbt: DbtPort):
        """
        Args:
            storage: Port for reading bronze, writing silver
            dbt: Port for triggering dbt transformations
        """
        self.storage = storage
        self.dbt = dbt
    
    async def transform_rides_batch(self, batch_date: str) -> int:
        """
        Transform all rides from a date.
        
        Flow:
        1. Read all rides from bronze/{date}/
        2. Apply domain transformations (calculate fare adjustments, etc.)
        3. Write to silver/{date}/
        4. Trigger dbt to build gold layer
        
        Args:
            batch_date: Date string (YYYY-MM-DD) to process
        
        Returns:
            Count of rides transformed
        
        Raises:
            TransformationError: If transformation fails
        """
        try:
            logger.info(
                "transform_batch_start",
                extra={"batch_date": batch_date}
            )
            
            # 1. Fetch all bronze files for this date
            bronze_prefix = f"rides/bronze/{batch_date}"
            bronze_files = await self.storage.list(
                bucket="ridestream-bronze",
                prefix=bronze_prefix
            )
            logger.info(
                "bronze_files_found",
                extra={"count": len(bronze_files), "prefix": bronze_prefix}
            )
            
            # 2. Read, transform, write each file
            transformed_count = 0
            for bronze_key in bronze_files:
                raw_data = await self.storage.get(
                    bucket="ridestream-bronze",
                    key=bronze_key
                )
                raw_dict = json.loads(raw_data.decode())
                
                # Apply domain transformations
                ride = Ride(**raw_dict)
                fare_with_surge = RideService.calculate_surge_adjusted_fare(
                    ride.fare_cents,
                    demand_level=5  # Could come from a metrics service
                )
                
                # Build silver record
                silver_record = {
                    "ride_id": ride.id,
                    "driver_id": ride.driver_id,
                    "distance_km": ride.distance_km,
                    "original_fare_cents": ride.fare_cents,
                    "surge_adjusted_fare_cents": fare_with_surge,
                    "trip_duration_category": self._categorize_duration(
                        ride.distance_km
                    ),
                    "transformed_at": datetime.utcnow().isoformat()
                }
                
                # Write to silver
                silver_key = f"rides/silver/{batch_date}/{ride.id}.json"
                await self.storage.put(
                    bucket="ridestream-silver",
                    key=silver_key,
                    data=json.dumps(silver_record).encode()
                )
                
                transformed_count += 1
                logger.debug(
                    "ride_transformed",
                    extra={"ride_id": ride.id, "silver_key": silver_key}
                )
            
            logger.info(
                "transform_batch_silver_complete",
                extra={
                    "batch_date": batch_date,
                    "transformed_count": transformed_count
                }
            )
            
            # 3. Trigger dbt to build gold layer
            await self.dbt.run_models(
                selector="fact_daily_ride_metrics",
                variables={"run_date": batch_date}
            )
            logger.info(
                "dbt_triggered",
                extra={"batch_date": batch_date, "selector": "fact_daily_ride_metrics"}
            )
            
            return transformed_count
        
        except StorageError as e:
            logger.error(
                "transform_storage_error",
                extra={"batch_date": batch_date, "error": str(e)},
                exc_info=True
            )
            raise TransformationError(f"Storage error during transformation: {e}") from e
        
        except DbtError as e:
            logger.error(
                "transform_dbt_error",
                extra={"batch_date": batch_date, "error": str(e)},
                exc_info=True
            )
            raise TransformationError(f"dbt execution failed: {e}") from e
        
        except Exception as e:
            logger.error(
                "transform_unexpected_error",
                extra={"batch_date": batch_date, "error": str(e)},
                exc_info=True
            )
            raise ApplicationError(f"Unexpected transformation error: {e}") from e
    
    @staticmethod
    def _categorize_duration(distance_km: float) -> str:
        """Categorize trip by distance (domain logic delegated)."""
        if distance_km < 5:
            return "short"
        elif distance_km < 15:
            return "medium"
        else:
            return "long"
```

### RideAnalyticsService
Queries gold layer via QueryPort, returns aggregated metrics.

```python
from src.adapters.query.port import QueryPort, QueryError
from src.application.exceptions import ApplicationError, AnalyticsError
from loguru import logger
from datetime import datetime, timedelta
from typing import List, Dict, Any

class RideAnalyticsService:
    """Query transformed ride metrics from gold layer."""
    
    def __init__(self, query: QueryPort):
        """
        Args:
            query: Port for querying Trino/Athena gold tables
        """
        self.query = query
    
    async def get_daily_ride_metrics(self, date: str) -> List[Dict[str, Any]]:
        """
        Fetch daily metrics for rides on given date.
        
        Args:
            date: Date string (YYYY-MM-DD)
        
        Returns:
            List of daily metrics by driver
        
        Raises:
            AnalyticsError: If query fails
        """
        try:
            logger.info(
                "analytics_query_start",
                extra={"query": "daily_ride_metrics", "date": date}
            )
            
            # Query gold layer (fact_daily_ride_metrics dbt model)
            results = await self.query.execute(
                model="fact_daily_ride_metrics",
                filters={"metric_date": date}
            )
            
            logger.info(
                "analytics_query_success",
                extra={
                    "query": "daily_ride_metrics",
                    "date": date,
                    "result_count": len(results)
                }
            )
            
            return results
        
        except QueryError as e:
            logger.error(
                "analytics_query_error",
                extra={"query": "daily_ride_metrics", "date": date, "error": str(e)},
                exc_info=True
            )
            raise AnalyticsError(f"Failed to query daily metrics: {e}") from e
        
        except Exception as e:
            logger.error(
                "analytics_unexpected_error",
                extra={"error": str(e)},
                exc_info=True
            )
            raise ApplicationError(f"Unexpected analytics error: {e}") from e
    
    async def get_driver_ratings(self, driver_id: str) -> Dict[str, Any]:
        """
        Fetch aggregated rating metrics for a driver.
        
        Args:
            driver_id: Driver ID
        
        Returns:
            Dict with avg_rating, num_rides, etc.
        
        Raises:
            AnalyticsError: If query fails
        """
        try:
            logger.info(
                "analytics_driver_rating_start",
                extra={"driver_id": driver_id}
            )
            
            results = await self.query.execute(
                model="fact_driver_ratings",
                filters={"driver_id": driver_id}
            )
            
            if not results:
                logger.warning(
                    "driver_not_found",
                    extra={"driver_id": driver_id}
                )
                return {}
            
            metric = results[0]
            logger.info(
                "analytics_driver_rating_success",
                extra={
                    "driver_id": driver_id,
                    "avg_rating": metric.get("avg_rating")
                }
            )
            
            return metric
        
        except QueryError as e:
            logger.error(
                "analytics_driver_query_error",
                extra={"driver_id": driver_id, "error": str(e)},
                exc_info=True
            )
            raise AnalyticsError(f"Failed to query driver ratings: {e}") from e
```

### PipelineOrchestrationService
Coordinates full pipeline: ingest → validate → transform → dbt → analytics.

```python
from src.application.ride_ingestion import RideIngestionService
from src.application.ride_transformation import RideTransformationService
from src.application.ride_analytics import RideAnalyticsService
from src.application.exceptions import ApplicationError, PipelineError
from loguru import logger
from datetime import datetime
from typing import List, Dict, Any

class PipelineOrchestrationService:
    """Orchestrate full RideStream pipeline."""
    
    def __init__(
        self,
        ingest_service: RideIngestionService,
        transform_service: RideTransformationService,
        analytics_service: RideAnalyticsService
    ):
        """
        Args:
            ingest_service: Service for ingesting raw events
            transform_service: Service for transforming bronze → silver
            analytics_service: Service for querying gold metrics
        """
        self.ingest_service = ingest_service
        self.transform_service = transform_service
        self.analytics_service = analytics_service
    
    async def run_daily_pipeline(self, batch_date: str) -> Dict[str, Any]:
        """
        Execute full pipeline for a day.
        
        Steps:
        1. Load rides from bronze (already ingested in real-time)
        2. Transform to silver
        3. Trigger dbt to build gold
        4. Query results
        
        Args:
            batch_date: Date to process (YYYY-MM-DD)
        
        Returns:
            Pipeline execution summary
        
        Raises:
            PipelineError: If any stage fails
        """
        try:
            logger.info(
                "pipeline_start",
                extra={
                    "batch_date": batch_date,
                    "timestamp": datetime.utcnow().isoformat()
                }
            )
            
            # Stage 1: Transform
            transform_count = await self.transform_service.transform_rides_batch(
                batch_date
            )
            logger.info(
                "pipeline_transform_complete",
                extra={
                    "batch_date": batch_date,
                    "rides_transformed": transform_count
                }
            )
            
            # Stage 2: Query gold metrics
            metrics = await self.analytics_service.get_daily_ride_metrics(
                batch_date
            )
            logger.info(
                "pipeline_analytics_complete",
                extra={
                    "batch_date": batch_date,
                    "metrics_returned": len(metrics)
                }
            )
            
            summary = {
                "batch_date": batch_date,
                "rides_transformed": transform_count,
                "metrics_available": len(metrics),
                "status": "success",
                "completed_at": datetime.utcnow().isoformat()
            }
            
            logger.info(
                "pipeline_success",
                extra=summary
            )
            
            return summary
        
        except Exception as e:
            logger.error(
                "pipeline_failed",
                extra={
                    "batch_date": batch_date,
                    "error": str(e)
                },
                exc_info=True
            )
            raise PipelineError(f"Pipeline failed for {batch_date}: {e}") from e
```

## Dependency Injection
Application services receive all dependencies via constructor (not global singletons).

```python
# GOOD: DI via constructor
class RideIngestionService:
    def __init__(self, event_bus: EventBusPort, storage: StoragePort):
        self.event_bus = event_bus
        self.storage = storage

# BAD: Global singleton
from src.adapters.event_bus.kafka import event_bus_instance

class RideIngestionService:
    async def ingest_ride(self, ride_data: dict):
        await event_bus_instance.publish(...)  # Hidden dependency

# BAD: Lazy initialization (couples to adapter)
class RideIngestionService:
    def __init__(self):
        pass
    
    async def ingest_ride(self, ride_data: dict):
        if not hasattr(self, 'event_bus'):
            self.event_bus = EventBusAdapterKafka()  # Concrete adapter!
```

## Error Handling
Map port/adapter errors to application-level exceptions.

```python
# src/application/exceptions.py
class ApplicationError(Exception):
    """Base application error."""
    pass

class IngestionError(ApplicationError):
    """Ride ingestion failed (validation, storage, or publishing)."""
    pass

class TransformationError(ApplicationError):
    """Ride transformation failed (bronze→silver or dbt)."""
    pass

class AnalyticsError(ApplicationError):
    """Analytics query failed."""
    pass

class PipelineError(ApplicationError):
    """End-to-end pipeline execution failed."""
    pass

# In service
async def ingest_ride_event(self, raw_ride_data: dict) -> Ride:
    try:
        ride = Ride(**raw_ride_data)  # Domain validation
        await self.event_bus.publish(...)  # Port call
    except RideValidationError as e:
        raise IngestionError(f"Validation failed: {e}") from e
    except EventBusError as e:
        raise IngestionError(f"EventBus publish failed: {e}") from e
    except StorageError as e:
        raise IngestionError(f"Storage failed: {e}") from e
```

## Async/Await
Application services orchestrate I/O. Domain logic must NOT be async.

```python
# GOOD: Application layer handles I/O coordination
async def ingest_ride_event(self, raw_ride_data: dict) -> Ride:
    ride = Ride(**raw_ride_data)  # Sync domain creation
    await self.event_bus.publish(...)  # Async I/O
    await self.storage.put(...)  # Async I/O
    return ride

# BAD: Domain logic doing I/O
class Ride(BaseModel):
    async def validate(self):  # Domain async
        port = get_event_bus()  # I/O in domain
        await port.publish(...)  # I/O in domain
```

## Logging Strategy
Log at application layer. Use loguru (structured JSON) for all logs.

```python
from loguru import logger

class RideIngestionService:
    async def ingest_ride_event(self, raw_ride_data: dict) -> Ride:
        try:
            logger.info(
                "ride_ingest_start",
                extra={
                    "ride_id": raw_ride_data.get("ride_id"),
                    "timestamp": datetime.utcnow().isoformat()
                }
            )
            
            ride = Ride(**raw_ride_data)
            await self.event_bus.publish(...)
            
            logger.info(
                "ride_ingested",
                extra={"ride_id": ride.id}
            )
            return ride
        
        except IngestionError as e:
            logger.error(
                "ride_ingest_failed",
                extra={"error": str(e)},
                exc_info=True
            )
            raise
```

## Testing Application Services
Test with port mocks (not adapter implementations).

```python
# tests/unit/application/test_ride_ingestion.py
import pytest
from unittest.mock import AsyncMock
from src.application.ride_ingestion import RideIngestionService
from src.domain.ride import Ride

@pytest.fixture
def mock_event_bus():
    """Mock EventBusPort."""
    mock = AsyncMock()
    mock.publish = AsyncMock()
    return mock

@pytest.fixture
def mock_storage():
    """Mock StoragePort."""
    mock = AsyncMock()
    mock.put = AsyncMock()
    return mock

@pytest.fixture
def service(mock_event_bus, mock_storage):
    return RideIngestionService(mock_event_bus, mock_storage)

@pytest.mark.asyncio
async def test_ingest_ride_success(service, mock_event_bus, mock_storage):
    raw_data = {
        "ride_id": "ride_123",
        "driver_id": "driver_1",
        "distance_km": 5.0,
        "fare_cents": 1500
    }
    
    ride = await service.ingest_ride_event(raw_data)
    
    assert ride.id == "ride_123"
    mock_event_bus.publish.assert_called_once()
    mock_storage.put.assert_called_once()

@pytest.mark.asyncio
async def test_ingest_ride_validation_error(service):
    raw_data = {
        "ride_id": "ride_123",
        # Missing required fields
    }
    
    from src.application.exceptions import IngestionError
    with pytest.raises(IngestionError):
        await service.ingest_ride_event(raw_data)
```

## Port Usage Pattern
Ports are passed through DI, used consistently across services.

```python
# src/adapters/event_bus/port.py (interface)
from abc import ABC, abstractmethod
from typing import Dict, Any

class EventBusPort(ABC):
    @abstractmethod
    async def publish(self, topic: str, event: Dict[str, Any]) -> None:
        """Publish event to topic."""
        pass

# src/adapters/storage/port.py (interface)
class StoragePort(ABC):
    @abstractmethod
    async def put(self, bucket: str, key: str, data: bytes) -> None:
        """Store data in bucket."""
        pass
    
    @abstractmethod
    async def get(self, bucket: str, key: str) -> bytes:
        """Retrieve data from bucket."""
        pass

# Service instantiation (with local adapters)
from src.adapters.event_bus.local import EventBusAdapterLocal
from src.adapters.storage.local import StorageAdapterLocal

event_bus = EventBusAdapterLocal()
storage = StorageAdapterLocal()
service = RideIngestionService(event_bus, storage)
await service.ingest_ride_event(raw_data)

# Service instantiation (with AWS adapters)
from src.adapters.event_bus.aws import EventBusAdapterKafka
from src.adapters.storage.aws import StorageAdapterS3

event_bus = EventBusAdapterKafka(broker_servers=["kafka:9092"])
storage = StorageAdapterS3(region="us-east-1")
service = RideIngestionService(event_bus, storage)
await service.ingest_ride_event(raw_data)
# ^ No code change in RideIngestionService!
```

## CloudProvider Pattern
Same application code runs against local Docker stack AND AWS — zero code changes.

```python
# src/config.py (environment-driven adapter selection)
import os
from src.adapters.event_bus.port import EventBusPort
from src.adapters.storage.port import StoragePort

def get_event_bus() -> EventBusPort:
    """Get event bus adapter based on environment."""
    profile = os.getenv("RIDESTREAM_PROFILE", "dev")
    
    if profile == "dev":
        from src.adapters.event_bus.local import EventBusAdapterLocal
        return EventBusAdapterLocal()
    
    elif profile == "prod":
        from src.adapters.event_bus.aws import EventBusAdapterKafka
        return EventBusAdapterKafka(
            broker_servers=os.getenv("KAFKA_BROKERS").split(",")
        )
    
    else:
        raise ValueError(f"Unknown profile: {profile}")

def get_storage() -> StoragePort:
    """Get storage adapter based on environment."""
    profile = os.getenv("RIDESTREAM_PROFILE", "dev")
    
    if profile == "dev":
        from src.adapters.storage.local import StorageAdapterLocal
        return StorageAdapterLocal(base_dir="/data/minio")
    
    elif profile == "prod":
        from src.adapters.storage.aws import StorageAdapterS3
        return StorageAdapterS3(region=os.getenv("AWS_REGION"))
    
    else:
        raise ValueError(f"Unknown profile: {profile}")

# main.py (same code for local + AWS)
import asyncio
from src.config import get_event_bus, get_storage
from src.application.ride_ingestion import RideIngestionService

async def main():
    event_bus = get_event_bus()
    storage = get_storage()
    service = RideIngestionService(event_bus, storage)
    
    # This code runs identically in local Docker AND AWS
    await service.ingest_ride_event({
        "ride_id": "ride_1",
        "driver_id": "driver_1",
        "distance_km": 5.0,
        "fare_cents": 1500
    })

if __name__ == "__main__":
    asyncio.run(main())
```

## Anti-Patterns (Never Do This)
- Importing adapter implementations directly
- Global service singletons
- Mixing I/O and domain logic
- Bare except or except Exception
- f-string SQL or event construction
- Hardcoded endpoint URLs
- Logging in adapters (adapters are quiet, app layer logs decisions)
- Skipping error mapping (let port errors bubble up unmapped)
- Testing with real AWS without integration test harness
