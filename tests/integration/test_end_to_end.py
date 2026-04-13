"""Integration test: Full end-to-end pipeline.

Simulator → Kafka → MinIO bronze → Hive external table → Trino query.
Validates the complete local pipeline using real Docker services.
"""

from __future__ import annotations

import contextlib
import json
import uuid

import pytest

from ridestream.adapters.local.kafka_event_bus import KafkaEventPublisher
from ridestream.application.ride_simulator import RideSimulator
from ridestream.config import Config, Environment
from tests.integration.conftest import (
    BRONZE_BUCKET,
    KAFKA_BOOTSTRAP,
    KAFKA_TOPIC,
)

pytestmark = pytest.mark.integration


def _cleanup_prefix(s3_client, prefix: str) -> None:
    response = s3_client.list_objects_v2(Bucket=BRONZE_BUCKET, Prefix=prefix)
    for obj in response.get("Contents", []):
        s3_client.delete_object(Bucket=BRONZE_BUCKET, Key=obj["Key"])


class TestEndToEndPipeline:
    def test_simulator_to_trino_full_pipeline(
        self, s3_client, kafka_consumer_factory, trino_engine
    ) -> None:
        """Full pipeline: simulate → Kafka → MinIO → Hive → Trino.

        Proves the pipeline works end-to-end: simulator publishes →
        events land in MinIO (flat schema) → registered as Hive external
        table → queryable + aggregatable via Trino.
        """
        test_id = uuid.uuid4().hex[:6]
        prefix = f"e2e-{test_id}/"
        table_name = f"e2e_events_{test_id}"

        # Step 1: Simulate 3 rides → 12 events to Kafka
        config = Config(environment=Environment.LOCAL)
        publisher = KafkaEventPublisher(bootstrap_servers=KAFKA_BOOTSTRAP, topic=KAFKA_TOPIC)
        try:
            simulator = RideSimulator(event_publisher=publisher, config=config)
            rides = simulator.run(num_rides=3)
            ride_ids = {str(r.id) for r in rides}
        finally:
            publisher.close()

        # Step 2: Consume and filter to completed events, land them in MinIO
        group = f"e2e-{test_id}"
        consumer = kafka_consumer_factory(KAFKA_TOPIC, group_id=group)

        completed_events: list[dict] = []
        all_events: list[dict] = []
        for msg in consumer:
            payload = msg.value
            if payload.get("ride_id") not in ride_ids:
                continue
            all_events.append(payload)
            if payload.get("event_type") == "ride_completed":
                completed_events.append(payload)

        assert len(all_events) == 12  # 4 events * 3 rides
        assert len(completed_events) == 3

        # Write completed events as NDJSON to MinIO
        ndjson = "\n".join(json.dumps(e) for e in completed_events).encode("utf-8")
        s3_client.put_object(Bucket=BRONZE_BUCKET, Key=f"{prefix}completed.json", Body=ndjson)

        try:
            # Step 3: Register as Hive external table
            trino_engine.execute(
                f"CREATE TABLE IF NOT EXISTS hive.default.{table_name} ("
                f"  event_id VARCHAR, ride_id VARCHAR, event_type VARCHAR,"
                f"  rider_id VARCHAR, driver_id VARCHAR,"
                f"  fare_cents INTEGER, distance_meters INTEGER"
                f") WITH ("
                f"  external_location = 's3a://{BRONZE_BUCKET}/{prefix}',"
                f"  format = 'JSON'"
                f")"
            )

            # Step 4: Query via Trino — row count matches what we landed
            row_count = trino_engine.execute(
                f"SELECT COUNT(*) AS cnt FROM hive.default.{table_name}"
            )
            assert row_count[0]["cnt"] == 3

            # Step 5: Aggregation — sum of fares matches what simulator produced
            agg = trino_engine.execute(
                f"SELECT SUM(fare_cents) AS total_cents,"
                f"       COUNT(DISTINCT ride_id) AS unique_rides "
                f"FROM hive.default.{table_name}"
            )
            assert agg[0]["unique_rides"] == 3

            expected_total_cents = sum(e["fare_cents"] for e in completed_events)
            assert agg[0]["total_cents"] == expected_total_cents

            # Step 6: Rider/driver linkage preserved through the pipeline
            rider_rows = trino_engine.execute(
                f"SELECT rider_id, driver_id FROM hive.default.{table_name}"
            )
            for row in rider_rows:
                assert row["rider_id"] is not None
                assert row["driver_id"] is not None

        finally:
            with contextlib.suppress(Exception):
                trino_engine.execute(f"DROP TABLE IF EXISTS hive.default.{table_name}")
            _cleanup_prefix(s3_client, prefix)

    def test_kafka_topics_exist(self) -> None:
        """Infrastructure sanity: required Kafka topics are present."""
        from kafka import KafkaConsumer

        consumer = KafkaConsumer(
            bootstrap_servers=KAFKA_BOOTSTRAP,
            consumer_timeout_ms=5000,
        )
        topics = consumer.topics()
        consumer.close()

        assert "ride.events.raw" in topics
        assert "ride.events.dlq" in topics
        assert "ride.metrics.5min" in topics

    def test_minio_buckets_exist(self, s3_client) -> None:
        """Infrastructure sanity: required MinIO buckets exist."""
        for bucket in ["bronze", "silver", "gold", "athena-results"]:
            response = s3_client.head_bucket(Bucket=bucket)
            assert response["ResponseMetadata"]["HTTPStatusCode"] == 200
