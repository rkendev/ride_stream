"""Unit tests for catalog adapters (Glue + Hive)."""

from __future__ import annotations

from unittest.mock import MagicMock

import boto3
import pytest
from moto import mock_aws

from ridestream.adapters.aws.glue_catalog import GlueCatalogAdapter
from ridestream.adapters.errors import CatalogError
from ridestream.adapters.local.hive_catalog import HiveCatalogAdapter

COLUMNS = [
    {"name": "event_id", "type": "string"},
    {"name": "ride_id", "type": "string"},
    {"name": "fare_cents", "type": "int"},
    {"name": "distance_km", "type": "double"},
]

DB = "ridestream_test"
TABLE = "ride_events"
LOCATION = "s3://bronze/ride_events/"


# ── Glue Catalog Adapter ─────────────────────────────────


@pytest.fixture
def glue_client():
    with mock_aws():
        client = boto3.client("glue", region_name="us-east-1")
        client.create_database(DatabaseInput={"Name": DB})
        yield client


@pytest.fixture
def glue_adapter(glue_client) -> GlueCatalogAdapter:
    return GlueCatalogAdapter(glue_client=glue_client, database=DB)


class TestGlueCreateTable:
    def test_create_table(self, glue_adapter: GlueCatalogAdapter) -> None:
        glue_adapter.create_table(DB, TABLE, COLUMNS, LOCATION)
        schema = glue_adapter.get_table_schema(DB, TABLE)
        assert len(schema) == 4
        assert schema[0]["name"] == "event_id"

    def test_create_table_column_types(self, glue_adapter: GlueCatalogAdapter) -> None:
        glue_adapter.create_table(DB, TABLE, COLUMNS, LOCATION)
        schema = glue_adapter.get_table_schema(DB, TABLE)
        type_map = {c["name"]: c["type"] for c in schema}
        assert type_map["event_id"] == "string"
        assert type_map["fare_cents"] == "int"
        assert type_map["distance_km"] == "double"

    def test_create_table_invalid_db_raises(self, glue_adapter: GlueCatalogAdapter) -> None:
        with pytest.raises(CatalogError, match="Failed to create"):
            glue_adapter.create_table("nonexistent_db", TABLE, COLUMNS, LOCATION)


class TestGlueGetSchema:
    def test_get_schema(self, glue_adapter: GlueCatalogAdapter) -> None:
        glue_adapter.create_table(DB, TABLE, COLUMNS, LOCATION)
        schema = glue_adapter.get_table_schema(DB, TABLE)
        assert isinstance(schema, list)
        assert all("name" in c and "type" in c for c in schema)

    def test_get_schema_nonexistent_raises(self, glue_adapter: GlueCatalogAdapter) -> None:
        with pytest.raises(CatalogError, match="Failed to get schema"):
            glue_adapter.get_table_schema(DB, "nonexistent")


class TestGlueFactory:
    @mock_aws
    def test_factory_creates_adapter(self) -> None:
        from ridestream.adapters.aws.glue_catalog import create_glue_catalog

        adapter = create_glue_catalog()
        assert isinstance(adapter, GlueCatalogAdapter)


# ── Hive Catalog Adapter ─────────────────────────────────


@pytest.fixture
def mock_engine() -> MagicMock:
    engine = MagicMock()
    engine.execute.return_value = [
        {"Column": "event_id", "Type": "VARCHAR"},
        {"Column": "ride_id", "Type": "VARCHAR"},
    ]
    return engine


@pytest.fixture
def hive_adapter(mock_engine: MagicMock) -> HiveCatalogAdapter:
    return HiveCatalogAdapter(query_engine=mock_engine, catalog="hive")


class TestHiveCreateTable:
    def test_create_table_calls_engine(
        self, hive_adapter: HiveCatalogAdapter, mock_engine: MagicMock
    ) -> None:
        hive_adapter.create_table("default", "test_table", COLUMNS, "s3a://bronze/test/")
        mock_engine.execute.assert_called_once()
        sql = mock_engine.execute.call_args[0][0]
        assert "CREATE TABLE IF NOT EXISTS hive.default.test_table" in sql
        assert "event_id VARCHAR" in sql
        assert "fare_cents INTEGER" in sql
        assert "external_location" in sql

    def test_create_table_error_raises(
        self, hive_adapter: HiveCatalogAdapter, mock_engine: MagicMock
    ) -> None:
        mock_engine.execute.side_effect = RuntimeError("connection refused")
        with pytest.raises(CatalogError, match="Failed to create"):
            hive_adapter.create_table("default", "bad_table", COLUMNS, "s3a://x/")


class TestHiveGetSchema:
    def test_get_schema_returns_columns(self, hive_adapter: HiveCatalogAdapter) -> None:
        schema = hive_adapter.get_table_schema("default", "test_table")
        assert len(schema) == 2
        assert schema[0] == {"name": "event_id", "type": "VARCHAR"}

    def test_get_schema_calls_show_columns(
        self, hive_adapter: HiveCatalogAdapter, mock_engine: MagicMock
    ) -> None:
        hive_adapter.get_table_schema("default", "test_table")
        sql = mock_engine.execute.call_args[0][0]
        assert "SHOW COLUMNS FROM hive.default.test_table" in sql

    def test_get_schema_error_raises(
        self, hive_adapter: HiveCatalogAdapter, mock_engine: MagicMock
    ) -> None:
        mock_engine.execute.side_effect = RuntimeError("table not found")
        with pytest.raises(CatalogError, match="Failed to get schema"):
            hive_adapter.get_table_schema("default", "missing")


# ── SQL Injection Guards (Fix 6) ─────────────────────────


class TestHiveInjectionGuards:
    def test_rejects_db_with_semicolon(self, hive_adapter: HiveCatalogAdapter) -> None:
        with pytest.raises(CatalogError, match="Invalid database"):
            hive_adapter.create_table("default; DROP TABLE users", "t", COLUMNS, "s3a://b/")

    def test_rejects_db_with_space(self, hive_adapter: HiveCatalogAdapter) -> None:
        with pytest.raises(CatalogError, match="Invalid database"):
            hive_adapter.create_table("bad name", "t", COLUMNS, "s3a://b/")

    def test_rejects_db_with_quote(self, hive_adapter: HiveCatalogAdapter) -> None:
        with pytest.raises(CatalogError, match="Invalid database"):
            hive_adapter.create_table("'--", "t", COLUMNS, "s3a://b/")

    def test_rejects_table_with_backtick(self, hive_adapter: HiveCatalogAdapter) -> None:
        with pytest.raises(CatalogError, match="Invalid table"):
            hive_adapter.create_table("default", "t`", COLUMNS, "s3a://b/")

    def test_rejects_empty_table(self, hive_adapter: HiveCatalogAdapter) -> None:
        with pytest.raises(CatalogError, match="Invalid table"):
            hive_adapter.create_table("default", "", COLUMNS, "s3a://b/")

    def test_rejects_column_with_injection(self, hive_adapter: HiveCatalogAdapter) -> None:
        bad_cols = [{"name": "col; DROP", "type": "string"}]
        with pytest.raises(CatalogError, match="Invalid column"):
            hive_adapter.create_table("default", "t", bad_cols, "s3a://b/")

    def test_rejects_bad_column_type(self, hive_adapter: HiveCatalogAdapter) -> None:
        bad_cols = [{"name": "col", "type": "VARCHAR; DROP TABLE users"}]
        with pytest.raises(CatalogError, match="Invalid type"):
            hive_adapter.create_table("default", "t", bad_cols, "s3a://b/")

    def test_rejects_location_not_s3(self, hive_adapter: HiveCatalogAdapter) -> None:
        with pytest.raises(CatalogError, match="Invalid S3 location"):
            hive_adapter.create_table("default", "t", COLUMNS, "file:///etc/passwd")

    def test_rejects_location_with_injection(self, hive_adapter: HiveCatalogAdapter) -> None:
        with pytest.raises(CatalogError, match="Invalid S3 location"):
            hive_adapter.create_table("default", "t", COLUMNS, "s3a://b/'; DROP")

    def test_rejects_empty_location(self, hive_adapter: HiveCatalogAdapter) -> None:
        with pytest.raises(CatalogError, match="Invalid"):
            hive_adapter.create_table("default", "t", COLUMNS, "")

    def test_get_schema_rejects_bad_db(self, hive_adapter: HiveCatalogAdapter) -> None:
        with pytest.raises(CatalogError, match="Invalid database"):
            hive_adapter.get_table_schema("bad; DROP", "t")

    def test_get_schema_rejects_bad_table(self, hive_adapter: HiveCatalogAdapter) -> None:
        with pytest.raises(CatalogError, match="Invalid table"):
            hive_adapter.get_table_schema("default", "t; DROP")

    def test_accepts_valid_identifiers(
        self, hive_adapter: HiveCatalogAdapter, mock_engine: MagicMock
    ) -> None:
        """Valid identifiers should pass through unmodified."""
        hive_adapter.create_table("valid_db", "valid_table", COLUMNS, "s3a://bronze/path/")
        sql = mock_engine.execute.call_args[0][0]
        assert "valid_db.valid_table" in sql

    def test_accepts_parameterized_type(
        self, hive_adapter: HiveCatalogAdapter, mock_engine: MagicMock
    ) -> None:
        """VARCHAR(50), DECIMAL(10,2) should be accepted."""
        cols = [
            {"name": "name", "type": "VARCHAR(255)"},
            {"name": "amount", "type": "DECIMAL(10,2)"},
        ]
        hive_adapter.create_table("default", "t", cols, "s3a://b/")
        sql = mock_engine.execute.call_args[0][0]
        assert "VARCHAR(255)" in sql
        assert "DECIMAL(10,2)" in sql
