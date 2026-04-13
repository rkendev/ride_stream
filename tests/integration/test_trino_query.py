"""Integration test: Trino querying Hive external tables on MinIO.

Writes JSON rows to MinIO, creates an external Hive table, queries it via
Trino, and asserts the returned rows match what was written.
"""

from __future__ import annotations

import contextlib
import json
import uuid

import pytest

from tests.integration.conftest import BRONZE_BUCKET

pytestmark = pytest.mark.integration


def _cleanup_prefix(s3_client, prefix: str) -> None:
    response = s3_client.list_objects_v2(Bucket=BRONZE_BUCKET, Prefix=prefix)
    for obj in response.get("Contents", []):
        s3_client.delete_object(Bucket=BRONZE_BUCKET, Key=obj["Key"])


class TestTrinoConnectivity:
    """Connectivity-level sanity checks (fast)."""

    def test_select_one(self, trino_engine) -> None:
        results = trino_engine.execute("SELECT 1 AS test_value")
        assert len(results) == 1
        assert results[0]["test_value"] == 1

    def test_hive_catalog_registered(self, trino_engine) -> None:
        results = trino_engine.execute("SHOW CATALOGS")
        names = [r.get("Catalog", "") for r in results]
        assert "hive" in names

    def test_current_date(self, trino_engine) -> None:
        results = trino_engine.execute("SELECT CURRENT_DATE AS today")
        assert results[0]["today"] is not None


class TestTrinoHiveExternalTable:
    """Actual data-flow tests: write → register → query via Trino."""

    def test_write_register_and_query(self, s3_client, trino_engine) -> None:
        """Write JSON to MinIO, register as Hive external table, query it."""
        test_id = uuid.uuid4().hex[:6]
        table_name = f"integ_test_{test_id}"
        prefix = f"trino-test/{table_name}/"

        # Write 3 newline-delimited JSON records (Hive JSON SerDe format)
        rows = [
            {"ride_id": "r1", "event_type": "ride_completed", "fare_cents": 1000},
            {"ride_id": "r2", "event_type": "ride_completed", "fare_cents": 1500},
            {"ride_id": "r3", "event_type": "ride_requested", "fare_cents": 0},
        ]
        ndjson_body = "\n".join(json.dumps(r) for r in rows).encode("utf-8")
        s3_client.put_object(
            Bucket=BRONZE_BUCKET,
            Key=f"{prefix}data.json",
            Body=ndjson_body,
            ContentType="application/x-ndjson",
        )

        create_sql = (
            f"CREATE TABLE IF NOT EXISTS hive.default.{table_name} ("
            f"  ride_id VARCHAR, event_type VARCHAR, fare_cents INTEGER"
            f") WITH ("
            f"  external_location = 's3a://{BRONZE_BUCKET}/{prefix}',"
            f"  format = 'JSON'"
            f")"
        )

        try:
            trino_engine.execute(create_sql)

            # Query all rows
            results = trino_engine.execute(
                f"SELECT ride_id, event_type, fare_cents "
                f"FROM hive.default.{table_name} "
                f"ORDER BY ride_id"
            )
            assert len(results) == 3
            assert results[0]["ride_id"] == "r1"
            assert results[0]["fare_cents"] == 1000
            assert results[2]["ride_id"] == "r3"
            assert results[2]["event_type"] == "ride_requested"

            # Aggregation query
            agg = trino_engine.execute(
                f"SELECT event_type, COUNT(*) AS cnt, SUM(fare_cents) AS total "
                f"FROM hive.default.{table_name} "
                f"GROUP BY event_type "
                f"ORDER BY event_type"
            )
            agg_by_type = {r["event_type"]: r for r in agg}
            assert agg_by_type["ride_completed"]["cnt"] == 2
            assert agg_by_type["ride_completed"]["total"] == 2500
            assert agg_by_type["ride_requested"]["cnt"] == 1

        finally:
            with contextlib.suppress(Exception):
                trino_engine.execute(f"DROP TABLE IF EXISTS hive.default.{table_name}")
            _cleanup_prefix(s3_client, prefix)
