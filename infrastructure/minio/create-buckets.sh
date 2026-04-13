#!/bin/bash
set -e

echo "Waiting for MinIO to be ready..."
for i in {1..30}; do
    mc alias set minio http://minio:9000 minioadmin minioadmin > /dev/null 2>&1 && break
    echo "Attempt $i/30..."
    sleep 2
done

echo "Creating buckets..."
mc mb minio/bronze --ignore-existing
mc mb minio/silver --ignore-existing
mc mb minio/gold --ignore-existing
mc mb minio/athena-results --ignore-existing

echo "Buckets created successfully"
mc ls minio/
