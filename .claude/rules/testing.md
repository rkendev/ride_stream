# Testing Rules (RideStream v2: Data Pipeline)

## Core Principle
Tests validate critical paths. Unit tests are fast and isolated. Integration tests exercise the full Docker stack: Kafka, MinIO, Trino, Spark, Airflow. Empty integration tests are a violation.

## Rule 11: INTEGRATION TESTS MUST NOT BE EMPTY
Integration tests MUST spin up real Docker containers and exercise the full data pipeline stack. Mocking Kafka, MinIO, or Trino is insufficient — they miss serialization, network, and query execution bugs.

### What Counts as Integration Tests
**YES (Real Docker containers)**:
- Kafka: produce ride events → consume events → verify serialization/deserialization
- MinIO: put_object to bronze bucket → get_object → verify content
- Trino: create table via Hive Metastore → query via Trino at port 8888 → verify results
- dbt: run staging models against Trino → verify transformed tables in silver/gold
- Full pipeline: publish ride event → ingest to bronze → transform to silver → dbt to gold → query via Trino

**NO (Not integration tests)**:
- Mocking Kafka topics (use real kafka-python producer/consumer)
- Mocking MinIO S3 calls (use real minio SDK)
- In-memory test doubles for Trino queries
- Unit tests without Docker stack
- Moto S3 mocks (not the actual pipeline)

### Integration Test Structure

```python
# tests/integration/conftest.py
import pytest
import time
from docker.compose import DockerCompose
from kafka import KafkaProducer, KafkaConsumer
from minio import Minio
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

@pytest.fixture(scope="session")
def docker_stack():
    """Spins up full Docker stack (Kafka, MinIO, Trino, Hive, Spark, Airflow)."""
    import subprocess
    
    # Start stack
    subprocess.run(
        ["docker", "compose", "-f", "docker/docker-compose.yml", "up", "-d"],
        check=True,
        capture_output=True
    )
    
    # Wait for health checks
    max_retries = 30
    for attempt in range(max_retries):
        try:
            # Verify Kafka
            kafka_admin = KafkaAdminClient(bootstrap_servers=['localhost:9092'])
            kafka_admin.close()
            
            # Verify MinIO
            minio_client = Minio("localhost:9000", access_key="minioadmin", secret_key="minioadmin")
            minio_client.bucket_exists("bronze")
            
            # Verify Trino
            trino_engine = create_engine(
                "trino://user@localhost:8888/hive/default",
                poolclass=NullPool
            )
            with trino_engine.connect() as conn:
                conn.execute("SELECT 1")
            
            break
        except Exception:
            if attempt == max_retries - 1:
                raise
            time.sleep(1)
    
    yield
    
    # Teardown
    subprocess.run(
        ["docker", "compose", "-f", "docker/docker-compose.yml", "down", "-v"],
        check=True,
        capture_output=True
    )

@pytest.fixture
def kafka_producer():
    """Real Kafka producer."""
    return KafkaProducer(
        bootstrap_servers=['localhost:9092'],
        value_serializer=lambda v: json.dumps(v).encode('utf-8')
    )

@pytest.fixture
def kafka_consumer():
    """Real Kafka consumer."""
    return KafkaConsumer(
        bootstrap_servers=['localhost:9092'],
        value_deserializer=lambda m: json.loads(m.decode('utf-8')),
        auto_offset_reset='earliest',
        consumer_timeout_ms=5000
    )

@pytest.fixture
def minio_client():
    """Real MinIO client."""
    return Minio(
        "localhost:9000",
        access_key="minioadmin",
        secret_key="minioadmin",
        secure=False
    )

@pytest.fixture
def trino_engine():
    """Real Trino SQL engine."""
    return create_engine(
        "trino://user@localhost:8888/hive/default",
        poolclass=NullPool
    )

# tests/integration/test_kafka_pipeline.py
import json
import pytest
from kafka import KafkaProducer, KafkaConsumer

@pytest.mark.asyncio
async def test_ride_event_kafka_produce_consume(docker_stack, kafka_producer, kafka_consumer):
    """Produce ride event to Kafka, consume and verify serialization."""
    
    # 1. Publish ride request event
    ride_event = {
        "ride_id": "ride_123",
        "driver_id": "driver_456",
        "passenger_id": "passenger_789",
        "pickup_lat": 40.7128,
        "pickup_lon": -74.0060,
        "dropoff_lat": 40.7580,
        "dropoff_lon": -73.9855,
        "distance_km": 5.2,
        "fare_cents": 1250,
        "timestamp": "2024-01-15T10:30:00Z"
    }
    
    kafka_producer.send("ride_events", value=ride_event)
    kafka_producer.flush()
    
    # 2. Subscribe and consume
    kafka_consumer.subscribe(["ride_events"])
    messages = kafka_consumer.poll(timeout_ms=10000, max_records=1)
    
    # 3. Verify deserialization
    assert len(messages) > 0, "No messages consumed"
    topic_messages = messages.get(TopicPartition("ride_events", 0), [])
    assert len(topic_messages) > 0, "No messages in ride_events topic"
    
    consumed_event = topic_messages[0].value
    assert consumed_event["ride_id"] == "ride_123"
    assert consumed_event["driver_id"] == "driver_456"
    assert consumed_event["distance_km"] == 5.2
    assert consumed_event["fare_cents"] == 1250
    
    kafka_producer.close()
    kafka_consumer.close()

@pytest.mark.asyncio
async def test_kafka_serialization_with_schema(docker_stack, kafka_producer, kafka_consumer):
    """Verify Avro schema serialization for ride events."""
    
    # Event with strict schema
    ride_schema = {
        "type": "record",
        "name": "RideEvent",
        "fields": [
            {"name": "ride_id", "type": "string"},
            {"name": "driver_id", "type": "string"},
            {"name": "distance_km", "type": "float"},
            {"name": "fare_cents", "type": "int"}
        ]
    }
    
    event = {
        "ride_id": "ride_456",
        "driver_id": "driver_789",
        "distance_km": 10.5,
        "fare_cents": 2500
    }
    
    kafka_producer.send("ride_events", value=event)
    kafka_producer.flush()
    
    kafka_consumer.subscribe(["ride_events"])
    messages = kafka_consumer.poll(timeout_ms=10000, max_records=1)
    
    assert len(messages) > 0
    consumed = messages[TopicPartition("ride_events", 0)][0].value
    assert consumed["distance_km"] == 10.5
```

### MinIO Integration Test

```python
# tests/integration/test_minio_storage.py
import pytest
from minio import Minio
from io import BytesIO
import json

@pytest.mark.asyncio
async def test_minio_put_get_ride_data(docker_stack, minio_client):
    """Put ride data to MinIO bronze bucket, retrieve and verify."""
    
    # 1. Create object in bronze bucket
    ride_data = {
        "ride_id": "ride_123",
        "driver_id": "driver_456",
        "distance_km": 5.2,
        "fare_cents": 1250,
        "timestamp": "2024-01-15T10:30:00Z"
    }
    
    ride_json = json.dumps(ride_data).encode('utf-8')
    minio_client.put_object(
        "bronze",
        "rides/2024/01/15/ride_123.json",
        BytesIO(ride_json),
        length=len(ride_json)
    )
    
    # 2. Verify object exists
    stat = minio_client.stat_object("bronze", "rides/2024/01/15/ride_123.json")
    assert stat is not None
    assert stat.size == len(ride_json)
    
    # 3. Get object and verify content
    response = minio_client.get_object("bronze", "rides/2024/01/15/ride_123.json")
    retrieved_data = json.loads(response.read().decode('utf-8'))
    
    assert retrieved_data["ride_id"] == "ride_123"
    assert retrieved_data["driver_id"] == "driver_456"
    assert retrieved_data["distance_km"] == 5.2
    assert retrieved_data["fare_cents"] == 1250

@pytest.mark.asyncio
async def test_minio_list_objects_by_date(docker_stack, minio_client):
    """List rides in MinIO by date partition, verify structure."""
    
    # Upload multiple rides for a date
    for i in range(5):
        ride_data = {"ride_id": f"ride_{i}", "distance_km": 10.0 + i}
        ride_json = json.dumps(ride_data).encode('utf-8')
        minio_client.put_object(
            "bronze",
            f"rides/2024/01/15/ride_{i}.json",
            BytesIO(ride_json),
            length=len(ride_json)
        )
    
    # List all objects in date partition
    objects = minio_client.list_objects("bronze", prefix="rides/2024/01/15/")
    object_names = [obj.object_name for obj in objects]
    
    assert len(object_names) == 5
    assert "rides/2024/01/15/ride_0.json" in object_names
    assert "rides/2024/01/15/ride_4.json" in object_names

@pytest.mark.asyncio
async def test_minio_bucket_versioning(docker_stack, minio_client):
    """Test MinIO object versioning for ride data updates."""
    
    # Put initial version
    v1_data = {"ride_id": "ride_123", "fare_cents": 1000}
    v1_json = json.dumps(v1_data).encode('utf-8')
    minio_client.put_object(
        "bronze",
        "rides/ride_123.json",
        BytesIO(v1_json),
        length=len(v1_json)
    )
    
    # Update object
    v2_data = {"ride_id": "ride_123", "fare_cents": 1250}
    v2_json = json.dumps(v2_data).encode('utf-8')
    minio_client.put_object(
        "bronze",
        "rides/ride_123.json",
        BytesIO(v2_json),
        length=len(v2_json)
    )
    
    # Verify latest version
    response = minio_client.get_object("bronze", "rides/ride_123.json")
    current = json.loads(response.read().decode('utf-8'))
    assert current["fare_cents"] == 1250
```

### Trino Integration Test

```python
# tests/integration/test_trino_queries.py
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

@pytest.mark.asyncio
async def test_trino_create_table_via_hive(docker_stack, trino_engine):
    """Create table via Hive Metastore, query via Trino."""
    
    with trino_engine.connect() as conn:
        # 1. Create table (registers in Hive Metastore)
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS rides (
                ride_id VARCHAR,
                driver_id VARCHAR,
                distance_km DOUBLE,
                fare_cents INT
            )
        """))
        conn.commit()
        
        # 2. Insert test data
        conn.execute(text("""
            INSERT INTO rides VALUES
            ('ride_1', 'driver_1', 5.2, 1250),
            ('ride_2', 'driver_2', 10.5, 2500)
        """))
        conn.commit()
        
        # 3. Query and verify
        result = conn.execute(text("SELECT COUNT(*) as cnt FROM rides"))
        row = result.fetchone()
        assert row[0] == 2
        
        # 4. Aggregate query
        result = conn.execute(text("""
            SELECT driver_id, SUM(fare_cents) as total_fare
            FROM rides
            GROUP BY driver_id
            ORDER BY driver_id
        """))
        rows = result.fetchall()
        assert len(rows) == 2
        assert rows[0][1] == 1250  # driver_1 total
        assert rows[1][1] == 2500  # driver_2 total

@pytest.mark.asyncio
async def test_trino_bronze_to_silver_transformation(docker_stack, trino_engine):
    """Query bronze data, verify silver transformation via Trino."""
    
    with trino_engine.connect() as conn:
        # Create bronze (raw) table
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS bronze_rides (
                ride_id VARCHAR,
                driver_id VARCHAR,
                distance_km DOUBLE,
                fare_cents INT,
                created_at VARCHAR
            )
        """))
        
        # Insert bronze data
        conn.execute(text("""
            INSERT INTO bronze_rides VALUES
            ('ride_1', 'driver_1', 5.0, 1000, '2024-01-15'),
            ('ride_2', 'driver_1', 10.0, 2000, '2024-01-15')
        """))
        conn.commit()
        
        # Create silver (transformed) view
        conn.execute(text("""
            CREATE OR REPLACE VIEW silver_rides AS
            SELECT
                ride_id,
                driver_id,
                distance_km,
                fare_cents / 100.0 as fare_usd,
                created_at
            FROM bronze_rides
        """))
        conn.commit()
        
        # Query silver view
        result = conn.execute(text("""
            SELECT SUM(fare_usd) as total_revenue
            FROM silver_rides
            WHERE driver_id = 'driver_1'
        """))
        row = result.fetchone()
        assert row[0] == 30.0  # 1000/100 + 2000/100

@pytest.mark.asyncio
async def test_trino_join_dimension_tables(docker_stack, trino_engine):
    """Test joining fact and dimension tables in Trino."""
    
    with trino_engine.connect() as conn:
        # Create dimension table
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS drivers (
                driver_id VARCHAR,
                driver_name VARCHAR,
                rating DOUBLE
            )
        """))
        
        # Create fact table
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS rides (
                ride_id VARCHAR,
                driver_id VARCHAR,
                fare_cents INT
            )
        """))
        
        # Insert test data
        conn.execute(text("""
            INSERT INTO drivers VALUES
            ('driver_1', 'Alice', 4.8),
            ('driver_2', 'Bob', 4.5)
        """))
        
        conn.execute(text("""
            INSERT INTO rides VALUES
            ('ride_1', 'driver_1', 1000),
            ('ride_2', 'driver_1', 1500),
            ('ride_3', 'driver_2', 2000)
        """))
        conn.commit()
        
        # Join and aggregate
        result = conn.execute(text("""
            SELECT
                d.driver_name,
                d.rating,
                COUNT(*) as num_rides,
                AVG(r.fare_cents) as avg_fare
            FROM rides r
            LEFT JOIN drivers d ON r.driver_id = d.driver_id
            GROUP BY d.driver_name, d.rating
            ORDER BY d.driver_name
        """))
        rows = result.fetchall()
        assert len(rows) == 2
        assert rows[0][0] == 'Alice'  # driver_name
        assert rows[0][2] == 2         # num_rides
```

### dbt Integration Test

```python
# tests/integration/test_dbt_transforms.py
import pytest
import subprocess
import json
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

@pytest.mark.asyncio
async def test_dbt_run_staging_models(docker_stack, trino_engine):
    """Run dbt staging models, verify tables created in silver layer."""
    
    # 1. Seed bronze data (raw ingest)
    with trino_engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS raw_rides (
                ride_id VARCHAR,
                driver_id VARCHAR,
                distance_km DOUBLE,
                fare_cents INT,
                created_at VARCHAR
            )
        """))
        
        conn.execute(text("""
            INSERT INTO raw_rides VALUES
            ('ride_1', 'driver_1', 5.0, 1000, '2024-01-15T10:00:00Z'),
            ('ride_2', 'driver_1', 10.0, 2000, '2024-01-15T10:30:00Z'),
            ('ride_3', 'driver_2', 8.0, 1500, '2024-01-15T11:00:00Z')
        """))
        conn.commit()
    
    # 2. Run dbt models
    result = subprocess.run(
        ["dbt", "run", "--select", "stg_rides", "--profiles-dir", "."],
        cwd="/workspace/ride_stream_v2",
        capture_output=True,
        text=True,
        env={**os.environ, "RIDESTREAM_PROFILE": "dev"}
    )
    assert result.returncode == 0, f"dbt run failed: {result.stderr}"
    
    # 3. Verify staging table created
    with trino_engine.connect() as conn:
        result = conn.execute(text("SELECT COUNT(*) FROM stg_rides"))
        count = result.scalar()
        assert count == 3
        
        # Verify columns and data
        result = conn.execute(text("""
            SELECT ride_id, driver_id, distance_km
            FROM stg_rides
            WHERE driver_id = 'driver_1'
            ORDER BY ride_id
        """))
        rows = result.fetchall()
        assert len(rows) == 2
        assert rows[0][2] == 5.0
        assert rows[1][2] == 10.0

@pytest.mark.asyncio
async def test_dbt_test_models(docker_stack):
    """Run dbt test command, verify all model tests pass."""
    
    result = subprocess.run(
        ["dbt", "test", "--select", "stg_rides", "--profiles-dir", "."],
        cwd="/workspace/ride_stream_v2",
        capture_output=True,
        text=True,
        env={**os.environ, "RIDESTREAM_PROFILE": "dev"}
    )
    assert result.returncode == 0, f"dbt test failed: {result.stderr}"
    assert "PASS" in result.stdout or "pass" in result.stdout

@pytest.mark.asyncio
async def test_dbt_incremental_model(docker_stack, trino_engine):
    """Test incremental dbt model, verify only new data processed."""
    
    # 1. Initial run
    result = subprocess.run(
        ["dbt", "run", "--select", "fact_daily_rides", "--profiles-dir", "."],
        cwd="/workspace/ride_stream_v2",
        capture_output=True,
        text=True,
        env={**os.environ, "RIDESTREAM_PROFILE": "dev"}
    )
    assert result.returncode == 0
    
    with trino_engine.connect() as conn:
        result = conn.execute(text("SELECT COUNT(*) FROM fact_daily_rides"))
        initial_count = result.scalar()
    
    # 2. Insert new data
    with trino_engine.connect() as conn:
        conn.execute(text("""
            INSERT INTO raw_rides VALUES
            ('ride_new', 'driver_3', 7.5, 1750, '2024-01-16T10:00:00Z')
        """))
        conn.commit()
    
    # 3. Run dbt again (incremental mode)
    result = subprocess.run(
        ["dbt", "run", "--select", "fact_daily_rides", "--profiles-dir", "."],
        cwd="/workspace/ride_stream_v2",
        capture_output=True,
        text=True,
        env={**os.environ, "RIDESTREAM_PROFILE": "dev"}
    )
    assert result.returncode == 0
    
    with trino_engine.connect() as conn:
        result = conn.execute(text("SELECT COUNT(*) FROM fact_daily_rides"))
        final_count = result.scalar()
    
    # New row should be added
    assert final_count > initial_count
```

### End-to-End Pipeline Test

```python
# tests/integration/test_full_pipeline.py
import pytest
import json
import time
from kafka import KafkaProducer, KafkaConsumer
from minio import Minio
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool
from io import BytesIO
import subprocess

@pytest.mark.asyncio
async def test_full_pipeline_ride_to_gold(docker_stack):
    """End-to-end: Kafka event → MinIO bronze → dbt silver/gold → Trino query."""
    
    kafka_producer = KafkaProducer(
        bootstrap_servers=['localhost:9092'],
        value_serializer=lambda v: json.dumps(v).encode('utf-8')
    )
    
    minio = Minio("localhost:9000", access_key="minioadmin", secret_key="minioadmin", secure=False)
    
    trino = create_engine(
        "trino://user@localhost:8888/hive/default",
        poolclass=NullPool
    )
    
    # 1. Publish ride event to Kafka
    ride_event = {
        "ride_id": "ride_e2e_1",
        "driver_id": "driver_e2e_1",
        "passenger_id": "passenger_1",
        "pickup_lat": 40.7128,
        "pickup_lon": -74.0060,
        "distance_km": 5.5,
        "fare_cents": 1350,
        "timestamp": "2024-01-15T15:00:00Z"
    }
    
    kafka_producer.send("ride_events", value=ride_event)
    kafka_producer.flush()
    print("✓ Ride event published to Kafka")
    
    # 2. Simulate ingestion: Kafka → MinIO bronze
    time.sleep(2)  # Wait for processing
    ride_json = json.dumps(ride_event).encode('utf-8')
    minio.put_object(
        "bronze",
        "rides/2024/01/15/ride_e2e_1.json",
        BytesIO(ride_json),
        length=len(ride_json)
    )
    print("✓ Ride data ingested to MinIO bronze")
    
    # 3. Create bronze table (raw) in Trino
    with trino.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS bronze_rides (
                ride_id VARCHAR,
                driver_id VARCHAR,
                distance_km DOUBLE,
                fare_cents INT,
                timestamp VARCHAR
            )
        """))
        
        conn.execute(text("""
            INSERT INTO bronze_rides VALUES
            ('ride_e2e_1', 'driver_e2e_1', 5.5, 1350, '2024-01-15T15:00:00Z')
        """))
        conn.commit()
    print("✓ Bronze table created in Trino")
    
    # 4. Run dbt: bronze → silver (staging) → gold (fact)
    result = subprocess.run(
        ["dbt", "run", "--profiles-dir", "."],
        cwd="/workspace/ride_stream_v2",
        capture_output=True,
        text=True,
        env={**os.environ, "RIDESTREAM_PROFILE": "dev"}
    )
    assert result.returncode == 0, f"dbt run failed: {result.stderr}"
    print("✓ dbt transformations executed (bronze→silver→gold)")
    
    # 5. Query gold layer via Trino
    with trino.connect() as conn:
        result = conn.execute(text("""
            SELECT
                ride_id,
                driver_id,
                distance_km,
                fare_cents / 100.0 as fare_usd
            FROM fact_rides
            WHERE ride_id = 'ride_e2e_1'
        """))
        
        row = result.fetchone()
        assert row is not None, "Gold fact table query returned no results"
        assert row[0] == "ride_e2e_1"
        assert row[1] == "driver_e2e_1"
        assert row[2] == 5.5
        assert row[3] == 13.5
    
    print("✓ Gold layer queried successfully via Trino")
    print("✓ Full pipeline test PASSED")
    
    kafka_producer.close()
```

## Unit Tests (Fast, Isolated)
Domain logic and adapter implementations tested without Docker.

```python
# tests/unit/domain/test_ride_service.py
from src.domain.ride_service import RideService
from src.domain.exceptions import InvalidLocationError
import pytest

def test_calculate_fare_basic():
    """Unit test: pure calculation."""
    fare = RideService.calculate_fare(distance_km=5.0, base_fare_cents=250)
    assert fare == 250 + 500  # 250 base + 5km * 100

def test_validate_location_valid():
    """Unit test: validation rule."""
    assert RideService.validate_location(40.7128, -74.0060) is True

def test_validate_location_invalid_latitude():
    """Unit test: validation rejects invalid input."""
    with pytest.raises(InvalidLocationError):
        RideService.validate_location(91.0, 0.0)

def test_apply_surge_pricing():
    """Unit test: surge pricing calculation."""
    assert RideService.apply_surge_pricing(demand_level=3) == 1.0
    assert RideService.apply_surge_pricing(demand_level=7) == 1.25
    assert RideService.apply_surge_pricing(demand_level=15) == 1.5

# tests/unit/domain/test_ride_event.py
from src.domain.ride import RideEvent, EventType, RideStatus
from datetime import datetime, UTC

def test_ride_event_creation():
    """Unit test: create ride event with frozen model."""
    event = RideEvent(
        ride_id="ride_1",
        driver_id="driver_1",
        event_type=EventType.RIDE_REQUESTED,
        timestamp=datetime.now(UTC)
    )
    assert event.ride_id == "ride_1"
    
    # Frozen model prevents modification
    with pytest.raises(Exception):  # ValidationError
        event.ride_id = "ride_2"
```

## Unit Tests: Adapters (Mocking External Services)

```python
# tests/unit/adapters/test_kafka_adapter.py
import pytest
from unittest.mock import AsyncMock, MagicMock
from src.adapters.event_bus.kafka import KafkaEventBusAdapter
from src.domain.ride import RideEvent, EventType

@pytest.fixture
def mock_kafka_producer():
    """Mock Kafka producer."""
    return MagicMock()

@pytest.fixture
def kafka_adapter(mock_kafka_producer):
    adapter = KafkaEventBusAdapter()
    adapter.producer = mock_kafka_producer
    return adapter

def test_publish_ride_event(kafka_adapter):
    """Unit test: publish event via adapter."""
    event = RideEvent(
        ride_id="ride_1",
        driver_id="driver_1",
        event_type=EventType.RIDE_REQUESTED
    )
    
    kafka_adapter.publish(event)
    
    # Verify producer was called
    kafka_adapter.producer.send.assert_called_once()
    args = kafka_adapter.producer.send.call_args
    assert "ride_events" in str(args)

# tests/unit/adapters/test_minio_adapter.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.adapters.storage.minio import MinIOStorageAdapter

@pytest.fixture
def mock_minio_client():
    return MagicMock()

@pytest.fixture
def minio_adapter(mock_minio_client):
    adapter = MinIOStorageAdapter()
    adapter.client = mock_minio_client
    return adapter

def test_put_object(minio_adapter):
    """Unit test: upload object to MinIO."""
    data = b"test ride data"
    
    minio_adapter.put_object("bronze", "rides/ride_1.json", data)
    
    minio_adapter.client.put_object.assert_called_once()
    call_args = minio_adapter.client.put_object.call_args
    assert "bronze" in str(call_args)
    assert "rides/ride_1.json" in str(call_args)
```

## Unit Tests: Application Layer (Mocked Ports)

```python
# tests/unit/application/test_ride_service.py
import pytest
from unittest.mock import AsyncMock
from src.application.ride_service import RideApplicationService
from src.domain.ride import RideEvent, EventType

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
    mock.put_object = AsyncMock()
    return mock

@pytest.fixture
def mock_query():
    """Mock QueryPort."""
    mock = AsyncMock()
    mock.execute = AsyncMock(return_value=[])
    return mock

@pytest.fixture
def service(mock_event_bus, mock_storage, mock_query):
    return RideApplicationService(
        event_bus=mock_event_bus,
        storage=mock_storage,
        query_engine=mock_query
    )

@pytest.mark.asyncio
async def test_request_ride(service, mock_event_bus):
    """Unit test: request ride orchestrates domain + ports."""
    ride = await service.request_ride(
        driver_id="driver_1",
        passenger_id="passenger_1",
        distance_km=5.0
    )
    
    assert ride.event_type == EventType.RIDE_REQUESTED
    mock_event_bus.publish.assert_called_once()

@pytest.mark.asyncio
async def test_request_ride_failure(service, mock_event_bus):
    """Unit test: handle port failure gracefully."""
    mock_event_bus.publish.side_effect = Exception("Kafka unavailable")
    
    with pytest.raises(Exception):
        await service.request_ride(
            driver_id="driver_1",
            passenger_id="passenger_1",
            distance_km=5.0
        )
```

## Coverage Targets

| Layer | Target | Command |
|-------|--------|---------|
| Domain | ≥95% | `pytest tests/unit/domain/ --cov=src.domain` |
| Adapters | ≥80% | `pytest tests/unit/adapters/ --cov=src.adapters` |
| Application | ≥90% | `pytest tests/unit/application/ --cov=src.application` |
| Overall | ≥85% | `pytest --cov=src` |

## Test Organization

```
tests/
├── unit/
│   ├── domain/
│   │   ├── test_ride_service.py
│   │   └── test_ride_event.py
│   ├── adapters/
│   │   ├── test_kafka_adapter.py
│   │   ├── test_minio_adapter.py
│   │   └── test_trino_adapter.py
│   └── application/
│       └── test_ride_service.py
├── integration/
│   ├── conftest.py             # Docker stack fixture
│   ├── test_kafka_pipeline.py
│   ├── test_minio_storage.py
│   ├── test_trino_queries.py
│   ├── test_dbt_transforms.py
│   └── test_full_pipeline.py
├── fixtures/
│   ├── rides.json
│   └── events.json
└── conftest.py
```

## Pytest Configuration
```ini
# pytest.ini
[pytest]
testpaths = tests
asyncio_mode = auto
addopts = 
    --cov=src
    --cov-report=term-missing
    --cov-report=html
    --cov-fail-under=85
    --timeout=120
    -v

markers =
    asyncio: async tests
    integration: requires Docker stack (Kafka, MinIO, Trino, dbt)
    unit: isolated unit tests
    slow: slow tests (> 10 seconds)
```

## Docker Stack for Integration Tests
Uses docker/docker-compose.yml (same as development).

```yaml
# docker/docker-compose.yml (MUST match production specs)
version: '3.8'

services:
  kafka:
    image: confluentinc/cp-kafka:7.5.0
    environment:
      KAFKA_BROKER_ID: 1
      KAFKA_ZOOKEEPER_CONNECT: zookeeper:2181
      KAFKA_ADVERTISED_LISTENERS: PLAINTEXT://kafka:9092
    depends_on:
      - zookeeper
    healthcheck:
      test: ['CMD', 'kafka-broker-api-versions.sh', '--bootstrap-server', 'localhost:9092']
      interval: 10s
      timeout: 5s
      retries: 5

  zookeeper:
    image: confluentinc/cp-zookeeper:7.5.0
    environment:
      ZOOKEEPER_CLIENT_PORT: 2181
    healthcheck:
      test: ['CMD', 'echo', 'ruok', '|', 'nc', 'localhost', '2181']
      interval: 10s
      timeout: 5s
      retries: 5

  minio:
    image: minio/minio:latest
    environment:
      MINIO_ROOT_USER: minioadmin
      MINIO_ROOT_PASSWORD: minioadmin
    ports:
      - '9000:9000'
      - '9001:9001'
    command: server /data --console-address ":9001"
    volumes:
      - minio_data:/data
    healthcheck:
      test: ['CMD', 'curl', '-f', 'http://localhost:9000/minio/health/live']
      interval: 10s
      timeout: 5s
      retries: 5

  hive-metastore:
    image: apache/hadoop:3.3
    environment:
      SERVICE_NAME: hive-metastore
    ports:
      - '9083:9083'
    healthcheck:
      test: ['CMD', 'curl', '-f', 'http://localhost:9083']
      interval: 10s
      timeout: 5s
      retries: 5

  trino:
    image: trinodb/trino:latest
    ports:
      - '8888:8080'
    environment:
      TRINO_COORDINATOR: 'true'
    volumes:
      - ./trino-config:/etc/trino
    depends_on:
      - hive-metastore
      - minio
    healthcheck:
      test: ['CMD', 'curl', '-f', 'http://localhost:8080/ui/']
      interval: 10s
      timeout: 5s
      retries: 5

volumes:
  minio_data:
```

## Test Execution

```bash
# Local development
docker compose -f docker/docker-compose.yml up -d
sleep 15  # Wait for health checks
pytest tests/integration/ -v --timeout=120
docker compose -f docker/docker-compose.yml down -v

# Unit tests (no Docker required)
pytest tests/unit/ -v --cov=src

# Full test suite
pytest tests/ -v --cov=src --cov-fail-under=85

# dbt tests
dbt test --select staging --profiles-dir .
```

## Anti-Patterns (Never Do This)
- Integration tests with only moto (no real Kafka/MinIO/Trino)
- Mocking Kafka producer/consumer (use real kafka-python)
- Mocking MinIO S3 calls (use real minio SDK)
- In-memory test doubles for Trino queries (use real Trino engine)
- Empty integration test fixtures
- Tests that pass with mock but fail with real Docker
- Skip/xfail without issue links
- Tests without assertions
- Sleeping instead of waiting for conditions
- Hardcoded timeouts (use fixtures with explicit waits)
- No dbt test execution in integration tests
- Forgetting to stop Docker stack after tests
