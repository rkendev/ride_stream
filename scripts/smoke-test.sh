#!/bin/bash
# set -e  # disabled: check() handles errors

echo "==== RideStream v2 Smoke Test ===="

PASS=0
FAIL=0

check() {
    local name="$1"
    shift
    echo -n "  Checking $name... "
    if "$@" > /dev/null 2>&1; then
        echo "PASS"
        PASS=$((PASS + 1))
    else
        echo "FAIL"
        FAIL=$((FAIL + 1))
    fi
}

# Kafka — exec into broker, use internal listener
check "Kafka topics" docker exec rs2-kafka kafka-topics --bootstrap-server kafka:29092 --list

# MinIO — use curl against S3 API from host (no mc alias needed)
check "MinIO bronze bucket" curl -sf http://localhost:9000/minio/health/live
check "MinIO buckets exist" docker run --rm --network ride_stream_v2_default minio/mc:latest sh -c "mc alias set minio http://minio:9000 minioadmin minioadmin && mc ls minio/bronze"

# Trino — use curl like the healthcheck does
check "Trino connectivity" curl -sf http://localhost:8888/v1/info

# Hive Metastore — TCP port check (same as Docker healthcheck)
check "Hive Metastore" bash -c 'echo > /dev/tcp/localhost/9083'

# Spark Master
check "Spark master" curl -sf http://localhost:8090/

# Airflow
check "Airflow webserver" curl -sf http://localhost:8085/health

echo ""
echo "==== Results: $PASS passed, $FAIL failed ===="

if [ "$FAIL" -gt 0 ]; then
    echo "SMOKE TEST FAILED"
    exit 1
fi

echo "ALL SMOKE TESTS PASSED"
