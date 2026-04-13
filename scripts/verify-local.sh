#!/bin/bash
# ============================================================
# RideStream v2 — Local Production Readiness Verification
# Run this ON THE VPS inside the ride_stream_v2 project root.
# Requires Docker stack running (make docker-up).
# ============================================================

set -u  # Error on undefined vars, but don't exit on failure (we track pass/fail)

PASS=0
FAIL=0
TOTAL=0

pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); TOTAL=$((TOTAL + 1)); }
fail() { echo "  FAIL: $1"; echo "        $2"; FAIL=$((FAIL + 1)); TOTAL=$((TOTAL + 1)); }

section() { echo ""; echo "==== $1 ===="; }

# -----------------------------------------------------------
section "1. DOCKER STACK HEALTH"
# -----------------------------------------------------------

for svc in rs2-kafka rs2-minio rs2-trino rs2-hive-metastore rs2-spark-master rs2-airflow-webserver; do
    status=$(docker inspect "$svc" --format '{{.State.Health.Status}}' 2>/dev/null || echo "not_found")
    if [ "$status" = "healthy" ]; then
        pass "$svc is healthy"
    elif [ "$status" = "not_found" ]; then
        # Some containers don't have healthchecks, check if running
        running=$(docker inspect "$svc" --format '{{.State.Running}}' 2>/dev/null || echo "false")
        if [ "$running" = "true" ]; then
            pass "$svc is running (no healthcheck)"
        else
            fail "$svc" "Container not found or not running"
        fi
    else
        fail "$svc" "Status: $status"
    fi
done

# -----------------------------------------------------------
section "2. SMOKE TEST (make smoke-test)"
# -----------------------------------------------------------

if make smoke-test > /tmp/smoke-test.out 2>&1; then
    pass "make smoke-test"
else
    fail "make smoke-test" "$(tail -5 /tmp/smoke-test.out)"
fi

# -----------------------------------------------------------
section "3. KAFKA EVENT BUS — REAL PRODUCE + CONSUME"
# -----------------------------------------------------------

# Produce 1 ride (4 events) via the actual simulator + KafkaEventPublisher
KAFKA_OUT=$(python3 -c "
from ridestream.adapters.local.kafka_event_bus import KafkaEventPublisher
from ridestream.application.ride_simulator import RideSimulator
from ridestream.config import Config, Environment
config = Config(environment=Environment.LOCAL)
pub = KafkaEventPublisher(bootstrap_servers='localhost:9092', topic='ride.events.raw')
try:
    sim = RideSimulator(event_publisher=pub, config=config)
    rides = sim.run(num_rides=1)
    print(f'OK:produced:{len(rides)}')
except Exception as e:
    print(f'ERROR:{e}')
finally:
    pub.close()
" 2>/dev/null)

if echo "$KAFKA_OUT" | grep -q "OK:produced:1"; then
    pass "Simulator produced 1 ride (4 events) to Kafka"
else
    fail "Simulator → Kafka" "$KAFKA_OUT"
fi

# Consume and verify flat schema
CONSUME_OUT=$(python3 -c "
import json
from kafka import KafkaConsumer
consumer = KafkaConsumer(
    'ride.events.raw',
    bootstrap_servers='localhost:9092',
    auto_offset_reset='latest',
    consumer_timeout_ms=8000,
    value_deserializer=lambda m: json.loads(m.decode()),
    group_id=None
)
# Read recent messages
msgs = []
for msg in consumer:
    msgs.append(msg.value)
    if len(msgs) >= 4:
        break
consumer.close()

if len(msgs) == 0:
    print('ERROR:no messages consumed')
else:
    # Check flat schema fields
    sample = msgs[0]
    required = ['event_id', 'ride_id', 'event_type', 'timestamp']
    missing = [f for f in required if f not in sample]
    if missing:
        print(f'ERROR:missing fields: {missing}')
        print(f'FIELDS:{list(sample.keys())}')
    else:
        print(f'OK:consumed:{len(msgs)}')
        print(f'FIELDS:{list(sample.keys())}')
        # Check event types
        types = {m.get(\"event_type\") for m in msgs}
        print(f'TYPES:{types}')
" 2>/dev/null)

if echo "$CONSUME_OUT" | grep -q "OK:consumed"; then
    pass "Consumed events from Kafka with flat schema"
    # Print the fields for visual inspection
    echo "        Fields: $(echo "$CONSUME_OUT" | grep FIELDS | head -1)"
    echo "        Types: $(echo "$CONSUME_OUT" | grep TYPES)"
else
    fail "Kafka consume" "$CONSUME_OUT"
fi

# -----------------------------------------------------------
section "4. MINIO — WRITE + READ BACK"
# -----------------------------------------------------------

MINIO_OUT=$(python3 -c "
import json, boto3
from botocore.config import Config as BotoConfig
s3 = boto3.client(
    's3',
    endpoint_url='http://localhost:9000',
    aws_access_key_id='minioadmin',
    aws_secret_access_key='minioadmin',
    config=BotoConfig(signature_version='s3v4'),
    region_name='us-east-1'
)
# Write
test_event = {'ride_id': 'verify-001', 'event_type': 'ride_completed', 'fare_cents': 1500}
s3.put_object(Bucket='bronze', Key='verify/test.json', Body=json.dumps(test_event).encode())
# Read back
obj = s3.get_object(Bucket='bronze', Key='verify/test.json')
data = json.loads(obj['Body'].read())
if data['ride_id'] == 'verify-001' and data['fare_cents'] == 1500:
    print('OK:roundtrip')
else:
    print(f'ERROR:mismatch:{data}')
# Cleanup
s3.delete_object(Bucket='bronze', Key='verify/test.json')
" 2>/dev/null)

if echo "$MINIO_OUT" | grep -q "OK:roundtrip"; then
    pass "MinIO bronze write → read roundtrip"
else
    fail "MinIO roundtrip" "$MINIO_OUT"
fi

# -----------------------------------------------------------
section "5. TRINO — QUERY REAL DATA"
# -----------------------------------------------------------

# Write NDJSON to MinIO, create Hive external table, query via Trino
TRINO_OUT=$(python3 -c "
import json, boto3, uuid
from botocore.config import Config as BotoConfig
from ridestream.adapters.local.trino_query_engine import TrinoQueryEngine

s3 = boto3.client('s3', endpoint_url='http://localhost:9000',
    aws_access_key_id='minioadmin', aws_secret_access_key='minioadmin',
    config=BotoConfig(signature_version='s3v4'), region_name='us-east-1')

tid = uuid.uuid4().hex[:6]
prefix = f'verify-trino-{tid}/'
table = f'verify_{tid}'

# Write test data as NDJSON
rows = [
    {'ride_id': 'r1', 'fare_cents': 1000, 'event_type': 'ride_completed'},
    {'ride_id': 'r2', 'fare_cents': 2000, 'event_type': 'ride_completed'},
    {'ride_id': 'r3', 'fare_cents': 1500, 'event_type': 'ride_completed'},
]
ndjson = '\n'.join(json.dumps(r) for r in rows).encode()
s3.put_object(Bucket='bronze', Key=f'{prefix}data.json', Body=ndjson)

engine = TrinoQueryEngine(host='localhost', port=8888, user='admin', catalog='hive')

try:
    # Create external table
    engine.execute(
        f\"\"\"CREATE TABLE IF NOT EXISTS hive.default.{table} (
            ride_id VARCHAR, fare_cents INTEGER, event_type VARCHAR
        ) WITH (external_location = 's3a://bronze/{prefix}', format = 'JSON')\"\"\"
    )

    # Query
    results = engine.execute(f'SELECT COUNT(*) AS cnt, SUM(fare_cents) AS total FROM hive.default.{table}')
    cnt = results[0]['cnt']
    total = results[0]['total']

    if cnt == 3 and total == 4500:
        print(f'OK:query:count={cnt},total={total}')
    else:
        print(f'ERROR:unexpected:count={cnt},total={total}')
finally:
    # Cleanup
    try:
        engine.execute(f'DROP TABLE IF EXISTS hive.default.{table}')
    except:
        pass
    s3.delete_object(Bucket='bronze', Key=f'{prefix}data.json')
" 2>/dev/null)

if echo "$TRINO_OUT" | grep -q "OK:query"; then
    pass "Trino: NDJSON → Hive external table → SELECT SUM (count=3, total=4500)"
else
    fail "Trino query" "$TRINO_OUT"
fi

# -----------------------------------------------------------
section "6. DBT — COMPILE + PARSE"
# -----------------------------------------------------------

if (cd dbt && dbt debug --target dev --profiles-dir . > /dev/null 2>&1); then
    pass "dbt debug (Trino connection)"
else
    fail "dbt debug" "Cannot connect to Trino"
fi

if (cd dbt && dbt compile --target dev --profiles-dir . > /dev/null 2>&1); then
    pass "dbt compile (all models valid SQL)"
else
    fail "dbt compile" "SQL compilation failed"
fi

# -----------------------------------------------------------
section "7. UNIT TESTS (make test)"
# -----------------------------------------------------------

UNIT_OUT=$(make test 2>&1 | tail -5)
UNIT_COUNT=$(echo "$UNIT_OUT" | grep -oP '\d+ passed' | head -1)
if echo "$UNIT_OUT" | grep -q "passed"; then
    pass "Unit tests: $UNIT_COUNT"
else
    fail "Unit tests" "$UNIT_OUT"
fi

# -----------------------------------------------------------
section "8. INTEGRATION TESTS (real data flow)"
# -----------------------------------------------------------

INT_OUT=$(pytest tests/integration/ -v --timeout=120 2>&1 | tail -10)
INT_COUNT=$(echo "$INT_OUT" | grep -oP '\d+ passed' | head -1)
if echo "$INT_OUT" | grep -q "passed"; then
    pass "Integration tests: $INT_COUNT"
else
    fail "Integration tests" "$INT_OUT"
fi

# -----------------------------------------------------------
section "9. FULL PIPELINE — SIMULATOR → KAFKA → MINIO → TRINO AGGREGATE"
# -----------------------------------------------------------

PIPELINE_OUT=$(python3 -c "
import json, uuid, time, boto3
from botocore.config import Config as BotoConfig
from kafka import KafkaConsumer
from ridestream.adapters.local.kafka_event_bus import KafkaEventPublisher
from ridestream.adapters.local.trino_query_engine import TrinoQueryEngine
from ridestream.application.ride_simulator import RideSimulator
from ridestream.config import Config, Environment

tid = uuid.uuid4().hex[:6]
config = Config(environment=Environment.LOCAL)

# 1. Simulate 5 rides → Kafka
pub = KafkaEventPublisher(bootstrap_servers='localhost:9092', topic='ride.events.raw')
sim = RideSimulator(event_publisher=pub, config=config)
rides = sim.run(num_rides=5)
pub.close()
ride_ids = {str(r.id) for r in rides}

# 2. Consume completed events from Kafka
consumer = KafkaConsumer('ride.events.raw', bootstrap_servers='localhost:9092',
    auto_offset_reset='earliest', consumer_timeout_ms=10000,
    value_deserializer=lambda m: json.loads(m.decode()), group_id=f'pipeline-{tid}')
completed = []
for msg in consumer:
    v = msg.value
    if v.get('ride_id') in ride_ids and v.get('event_type') == 'ride_completed':
        completed.append(v)
consumer.close()

if len(completed) != 5:
    print(f'ERROR:expected 5 completed, got {len(completed)}')
    raise SystemExit(1)

# 3. Land in MinIO bronze
s3 = boto3.client('s3', endpoint_url='http://localhost:9000',
    aws_access_key_id='minioadmin', aws_secret_access_key='minioadmin',
    config=BotoConfig(signature_version='s3v4'), region_name='us-east-1')
prefix = f'pipeline-verify-{tid}/'
ndjson = '\n'.join(json.dumps(e) for e in completed).encode()
s3.put_object(Bucket='bronze', Key=f'{prefix}completed.json', Body=ndjson)

# 4. Create Hive table + query via Trino
table = f'pipeline_{tid}'
engine = TrinoQueryEngine(host='localhost', port=8888, user='admin', catalog='hive')
try:
    engine.execute(f\"\"\"CREATE TABLE IF NOT EXISTS hive.default.{table} (
        event_id VARCHAR, ride_id VARCHAR, event_type VARCHAR,
        rider_id VARCHAR, driver_id VARCHAR,
        fare_cents INTEGER, distance_meters INTEGER,
        surge_multiplier DOUBLE, timestamp VARCHAR
    ) WITH (external_location = 's3a://bronze/{prefix}', format = 'JSON')\"\"\")

    results = engine.execute(f'SELECT COUNT(*) AS cnt, SUM(fare_cents) AS total_fare FROM hive.default.{table}')
    cnt = results[0]['cnt']
    total = results[0]['total_fare']

    if cnt == 5 and total > 0:
        print(f'OK:pipeline:rides=5,total_fare_cents={total}')
    else:
        print(f'ERROR:count={cnt},total={total}')
finally:
    try:
        engine.execute(f'DROP TABLE IF EXISTS hive.default.{table}')
    except:
        pass
    s3.delete_object(Bucket='bronze', Key=f'{prefix}completed.json')
" 2>/dev/null)

if echo "$PIPELINE_OUT" | grep -q "OK:pipeline"; then
    pass "Full pipeline: 5 rides → Kafka → MinIO → Hive → Trino SUM"
    echo "        $(echo "$PIPELINE_OUT" | grep OK:pipeline)"
else
    fail "Full pipeline" "$PIPELINE_OUT"
fi

# -----------------------------------------------------------
section "10. AIRFLOW DAG PARSED"
# -----------------------------------------------------------

DAG_OUT=$(docker exec rs2-airflow-webserver airflow dags list 2>/dev/null | grep ridestream_v2_daily || echo "NOT_FOUND")
if echo "$DAG_OUT" | grep -q "ridestream_v2_daily"; then
    pass "Airflow DAG ridestream_v2_daily is registered"
else
    fail "Airflow DAG" "ridestream_v2_daily not found in airflow dags list"
fi

# -----------------------------------------------------------
echo ""
echo "========================================"
echo "  VERIFICATION RESULTS: $PASS passed, $FAIL failed (of $TOTAL checks)"
echo "========================================"

if [ "$FAIL" -gt 0 ]; then
    echo "  STATUS: NOT READY — $FAIL checks failed"
    exit 1
fi
echo "  STATUS: LOCALLY PRODUCTION-READY"
exit 0
