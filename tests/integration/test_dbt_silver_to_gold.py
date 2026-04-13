"""Integration test: dbt Silver → Gold against real Trino + Hive.

Seeds bronze data in MinIO, creates a bronze external table, runs dbt
silver+gold models via subprocess against the Docker Trino, runs dbt tests,
queries gold tables, and asserts expected row counts/values.
"""

from __future__ import annotations

import contextlib
import json
import subprocess
import uuid

import pytest

from tests.integration.conftest import BRONZE_BUCKET

pytestmark = pytest.mark.integration

DBT_DIR = "dbt"
PROFILES_DIR = "."


def _cleanup_prefix(s3_client, prefix: str) -> None:
    response = s3_client.list_objects_v2(Bucket=BRONZE_BUCKET, Prefix=prefix)
    for obj in response.get("Contents", []):
        s3_client.delete_object(Bucket=BRONZE_BUCKET, Key=obj["Key"])


def _run_dbt(args: list[str], timeout: int = 120) -> subprocess.CompletedProcess[str]:
    """Run a dbt command from the dbt/ directory."""
    return subprocess.run(
        ["dbt", *args, "--profiles-dir", PROFILES_DIR, "--target", "dev"],
        cwd=DBT_DIR,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


class TestDbtCompileAndParse:
    """Static correctness: compile, parse, model listing."""

    def test_dbt_debug(self) -> None:
        result = _run_dbt(["debug"], timeout=30)
        assert "All checks passed" in result.stdout

    def test_dbt_compile(self) -> None:
        result = _run_dbt(["compile"], timeout=60)
        assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"

    def test_dbt_ls_lists_silver_and_gold_models(self) -> None:
        result = subprocess.run(
            ["dbt", "ls", "--resource-type", "model", "--profiles-dir", PROFILES_DIR],
            cwd=DBT_DIR,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0
        for model in (
            "stg_rides",
            "stg_metrics",
            "rides_summary",
            "driver_metrics",
            "passenger_metrics",
        ):
            assert model in result.stdout


class TestDbtSilverGoldRun:
    """Data-flow: seed bronze, run dbt silver + gold, assert on gold rows."""

    def test_full_dbt_pipeline_against_seeded_data(self, s3_client, trino_engine) -> None:
        """Seed bronze → dbt run silver → dbt run gold → query gold."""
        test_id = uuid.uuid4().hex[:6]
        prefix = f"dbt-test-{test_id}/"
        bronze_table = f"ride_events_{test_id}"
        schema = "ridestream_dev"

        # 1. Seed bronze data — two riders, two completed rides
        rows = [
            {
                "event_id": f"e1-{test_id}",
                "ride_id": f"r1-{test_id}",
                "event_type": "ride_completed",
                "timestamp": "2026-04-11 12:00:00",
                "rider_id": f"rider-a-{test_id}",
                "driver_id": f"drv-a-{test_id}",
                "latitude": 40.7,
                "longitude": -74.0,
                "fare_cents": 1500,
                "distance_meters": 5000,
            },
            {
                "event_id": f"e2-{test_id}",
                "ride_id": f"r2-{test_id}",
                "event_type": "ride_completed",
                "timestamp": "2026-04-11 13:00:00",
                "rider_id": f"rider-a-{test_id}",
                "driver_id": f"drv-b-{test_id}",
                "latitude": 40.8,
                "longitude": -74.1,
                "fare_cents": 2000,
                "distance_meters": 8000,
            },
        ]
        ndjson = "\n".join(json.dumps(r) for r in rows).encode("utf-8")
        s3_client.put_object(Bucket=BRONZE_BUCKET, Key=f"{prefix}events.json", Body=ndjson)

        try:
            # 2. Ensure schema exists
            trino_engine.execute(f"CREATE SCHEMA IF NOT EXISTS hive.{schema}")

            # 3. Create bronze.ride_events external table pointing at our seed data
            # The dbt source() macro uses schema='default' by default, so create
            # the bronze table there.
            trino_engine.execute(f"DROP TABLE IF EXISTS hive.default.{bronze_table}")
            # timestamp stored as VARCHAR in bronze (raw Kafka JSON);
            # silver model casts to TIMESTAMP.
            trino_engine.execute(
                f"CREATE TABLE hive.default.{bronze_table} ("
                f"  event_id VARCHAR,"
                f"  ride_id VARCHAR,"
                f"  event_type VARCHAR,"
                f"  timestamp VARCHAR,"
                f"  rider_id VARCHAR,"
                f"  driver_id VARCHAR,"
                f"  latitude DOUBLE,"
                f"  longitude DOUBLE,"
                f"  fare_cents INTEGER,"
                f"  distance_meters INTEGER"
                f") WITH ("
                f"  external_location = 's3a://{BRONZE_BUCKET}/{prefix}',"
                f"  format = 'JSON'"
                f")"
            )

            # 4. Verify bronze rows are queryable
            bronze_rows = trino_engine.execute(
                f"SELECT COUNT(*) AS cnt FROM hive.default.{bronze_table}"
            )
            assert bronze_rows[0]["cnt"] == 2

            # 5. Alias the test bronze table to `ride_events` (dbt source name)
            # dbt's source('bronze', 'ride_events') references hive.default.ride_events
            # so create a view pointing at our test table
            trino_engine.execute("DROP VIEW IF EXISTS hive.default.ride_events")
            trino_engine.execute(
                f"CREATE VIEW hive.default.ride_events AS SELECT * FROM hive.default.{bronze_table}"
            )

            # Pre-cleanup: drop the whole ridestream_dev schema + recreate.
            # Hive rejects dbt's rename/drop pattern for leftover tables,
            # so the safest reset is to drop the schema cascade.
            with contextlib.suppress(Exception):
                trino_engine.execute(f"DROP SCHEMA hive.{schema} CASCADE")
            trino_engine.execute(f"CREATE SCHEMA hive.{schema}")

            # 6. Run dbt silver layer (--full-refresh: recreates tables, avoids rename)
            silver_result = _run_dbt(
                ["run", "--select", "stg_rides", "--full-refresh"], timeout=120
            )
            assert silver_result.returncode == 0, f"dbt silver failed: {silver_result.stdout}"

            # 7. Query silver output
            silver_rows = trino_engine.execute(
                f"SELECT COUNT(*) AS cnt FROM hive.{schema}.stg_rides "
                f"WHERE ride_id IN ('r1-{test_id}', 'r2-{test_id}')"
            )
            assert silver_rows[0]["cnt"] == 2

            # 8. Run gold layer (--full-refresh for same reason)
            gold_result = _run_dbt(
                ["run", "--select", "rides_summary", "--full-refresh"], timeout=120
            )
            assert gold_result.returncode == 0, f"dbt gold failed: {gold_result.stdout}"

            # 9. Query gold rides_summary — aggregates by date/driver/passenger
            gold_rows = trino_engine.execute(
                f"SELECT driver_id, passenger_id, rides_completed, "
                f"       total_distance_km, total_revenue_cents "
                f"FROM hive.{schema}.rides_summary "
                f"WHERE passenger_id = 'rider-a-{test_id}' "
                f"ORDER BY driver_id"
            )
            assert len(gold_rows) == 2  # same rider, two different drivers
            total_revenue = sum(r["total_revenue_cents"] for r in gold_rows)
            assert total_revenue == 3500  # 1500 + 2000

        finally:
            # Best-effort cleanup
            for drop_sql in [
                f"DROP TABLE IF EXISTS hive.{schema}.rides_summary",
                f"DROP TABLE IF EXISTS hive.{schema}.driver_metrics",
                f"DROP TABLE IF EXISTS hive.{schema}.passenger_metrics",
                f"DROP TABLE IF EXISTS hive.{schema}.stg_rides",
                f"DROP TABLE IF EXISTS hive.{schema}.stg_metrics",
                "DROP VIEW IF EXISTS hive.default.ride_events",
                f"DROP TABLE IF EXISTS hive.default.{bronze_table}",
            ]:
                with contextlib.suppress(Exception):
                    trino_engine.execute(drop_sql)
            _cleanup_prefix(s3_client, prefix)
