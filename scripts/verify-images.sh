#!/bin/bash
set -e

echo "==== Docker Image Tag Verification ===="

# Images actually used in docker-compose.yml (updated after spec-time verification)
images=(
    "apache/airflow:2.9.3-python3.12"
    "confluentinc/cp-kafka:7.6.0"
    "confluentinc/cp-zookeeper:7.6.0"
    "apache/spark:3.5.1-python3"
    "trinodb/trino:430"
    "postgres:15-alpine"
    "minio/minio:latest"
    "minio/mc:latest"
    "apache/hive:4.0.0"
)

PASS=0
FAIL=0

for image in "${images[@]}"; do
    echo -n "  Verifying $image... "
    if docker manifest inspect "$image" > /dev/null 2>&1; then
        echo "PASS"
        PASS=$((PASS + 1))
    else
        echo "FAIL"
        FAIL=$((FAIL + 1))
    fi
done

echo ""
echo "==== Results: $PASS passed, $FAIL failed ===="

if [ "$FAIL" -gt 0 ]; then
    echo "IMAGE VERIFICATION FAILED — do not proceed with docker-compose"
    exit 1
fi

echo "ALL IMAGES VERIFIED"
