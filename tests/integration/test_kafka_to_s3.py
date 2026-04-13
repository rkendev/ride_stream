"""Integration test: Kafka → MinIO bronze (real consume-and-land).

Publishes via the real KafkaEventPublisher, consumes with a real KafkaConsumer,
and lands each message as a JSON object in MinIO bronze. Then reads back from
MinIO and asserts content matches what was published (roundtrip).
"""

from __future__ import annotations

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


class TestKafkaToS3Roundtrip:
    def test_simulator_kafka_landing_roundtrip(self, s3_client, kafka_consumer_factory) -> None:
        """End-to-end: simulate → publish to Kafka → consume → write to MinIO → read back."""
        test_id = uuid.uuid4().hex[:8]
        prefix = f"test-k2s-{test_id}/"

        # Step 1: Simulate 2 rides → publishes 8 events to Kafka
        config = Config(environment=Environment.LOCAL)
        publisher = KafkaEventPublisher(bootstrap_servers=KAFKA_BOOTSTRAP, topic=KAFKA_TOPIC)
        try:
            simulator = RideSimulator(event_publisher=publisher, config=config)
            rides = simulator.run(num_rides=2)
            ride_ids = {str(r.id) for r in rides}
        finally:
            publisher.close()

        # Step 2: Consume events from Kafka and write each to MinIO
        group = f"k2s-{test_id}"
        consumer = kafka_consumer_factory(KAFKA_TOPIC, group_id=group)

        consumed: list[dict] = []
        try:
            for msg in consumer:
                payload = msg.value
                if payload.get("ride_id") not in ride_ids:
                    continue
                consumed.append(payload)
                # Write to MinIO (simulating Firehose landing)
                key = f"{prefix}{payload['event_id']}.json"
                s3_client.put_object(
                    Bucket=BRONZE_BUCKET,
                    Key=key,
                    Body=json.dumps(payload).encode("utf-8"),
                    ContentType="application/json",
                )

            # Step 3: Assert objects exist in bronze
            response = s3_client.list_objects_v2(Bucket=BRONZE_BUCKET, Prefix=prefix)
            contents = response.get("Contents", [])
            assert len(contents) == len(consumed)
            assert len(consumed) >= 8  # 2 rides * 4 events

            # Step 4: Read back and verify roundtrip
            roundtripped_ids: set[str] = set()
            for obj in contents:
                response = s3_client.get_object(Bucket=BRONZE_BUCKET, Key=obj["Key"])
                body = json.loads(response["Body"].read().decode("utf-8"))
                roundtripped_ids.add(body["event_id"])
                # Verify flat schema preserved
                assert "event_id" in body
                assert "ride_id" in body
                assert "event_type" in body
                assert "rider_id" in body

            consumed_ids = {p["event_id"] for p in consumed}
            assert roundtripped_ids == consumed_ids
        finally:
            _cleanup_prefix(s3_client, prefix)

    def test_bronze_bucket_exists_and_writable(self, s3_client) -> None:
        """Sanity: bronze bucket accepts writes and deletes."""
        key = f"write-check-{uuid.uuid4().hex[:6]}.json"
        s3_client.put_object(Bucket=BRONZE_BUCKET, Key=key, Body=b'{"probe": true}')
        response = s3_client.get_object(Bucket=BRONZE_BUCKET, Key=key)
        body = json.loads(response["Body"].read().decode("utf-8"))
        assert body["probe"] is True
        s3_client.delete_object(Bucket=BRONZE_BUCKET, Key=key)
