"""Unit tests for query engine adapters (Athena + Trino)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from ridestream.adapters.aws.athena_query_engine import AthenaQueryEngine
from ridestream.adapters.errors import QueryEngineError
from ridestream.adapters.local.trino_query_engine import TrinoQueryEngine

# ── Trino Query Engine ────────────────────────────────────


class TestTrinoInit:
    def test_default_config(self) -> None:
        engine = TrinoQueryEngine()
        assert engine._host == "localhost"
        assert engine._port == 8888
        assert engine._catalog == "hive"
        assert engine._schema == "ridestream_dev"

    def test_custom_config(self) -> None:
        engine = TrinoQueryEngine(host="trino-prod", port=443, schema="ridestream_prod")
        assert engine._host == "trino-prod"
        assert engine._port == 443


class TestTrinoExecute:
    @patch("ridestream.adapters.local.trino_query_engine.trino.dbapi.connect")
    def test_execute_returns_dicts(self, mock_connect: MagicMock) -> None:
        mock_cursor = MagicMock()
        mock_cursor.description = [("id",), ("name",)]
        mock_cursor.fetchall.return_value = [("1", "Alice"), ("2", "Bob")]
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_connect.return_value = mock_conn

        engine = TrinoQueryEngine()
        results = engine.execute("SELECT id, name FROM users")

        assert len(results) == 2
        assert results[0] == {"id": "1", "name": "Alice"}
        assert results[1] == {"id": "2", "name": "Bob"}
        mock_cursor.execute.assert_called_once_with("SELECT id, name FROM users")

    @patch("ridestream.adapters.local.trino_query_engine.trino.dbapi.connect")
    def test_execute_empty_result(self, mock_connect: MagicMock) -> None:
        mock_cursor = MagicMock()
        mock_cursor.description = [("count",)]
        mock_cursor.fetchall.return_value = []
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_connect.return_value = mock_conn

        engine = TrinoQueryEngine()
        results = engine.execute("SELECT count(*) FROM empty_table")
        assert results == []

    @patch("ridestream.adapters.local.trino_query_engine.trino.dbapi.connect")
    def test_connection_error_raises(self, mock_connect: MagicMock) -> None:
        import trino.exceptions

        mock_connect.side_effect = trino.exceptions.TrinoConnectionError("refused")

        engine = TrinoQueryEngine()
        with pytest.raises(QueryEngineError, match="Failed to connect"):
            engine.execute("SELECT 1")

    @patch("ridestream.adapters.local.trino_query_engine.trino.dbapi.connect")
    def test_query_error_raises(self, mock_connect: MagicMock) -> None:
        import trino.exceptions

        mock_cursor = MagicMock()
        mock_cursor.execute.side_effect = trino.exceptions.TrinoUserError(
            {"errorName": "SYNTAX_ERROR", "message": "bad sql"},
            "bad sql",
        )
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_connect.return_value = mock_conn

        engine = TrinoQueryEngine()
        with pytest.raises(QueryEngineError, match="Trino query error"):
            engine.execute("SELCT bad syntax")


# ── Athena Query Engine ───────────────────────────────────


@pytest.fixture
def athena_client() -> MagicMock:
    client = MagicMock()
    client.start_query_execution.return_value = {"QueryExecutionId": "qid-123"}
    client.get_query_execution.return_value = {"QueryExecution": {"Status": {"State": "SUCCEEDED"}}}
    client.get_query_results.return_value = {
        "ResultSet": {
            "ResultSetMetadata": {
                "ColumnInfo": [{"Name": "id"}, {"Name": "name"}],
            },
            "Rows": [
                {"Data": [{"VarCharValue": "id"}, {"VarCharValue": "name"}]},  # header
                {"Data": [{"VarCharValue": "1"}, {"VarCharValue": "Alice"}]},
                {"Data": [{"VarCharValue": "2"}, {"VarCharValue": "Bob"}]},
            ],
        }
    }
    return client


@pytest.fixture
def athena_engine(athena_client: MagicMock) -> AthenaQueryEngine:
    return AthenaQueryEngine(
        athena_client=athena_client,
        poll_interval=0,
        max_wait=10,
    )


class TestAthenaInit:
    def test_default_config(self) -> None:
        engine = AthenaQueryEngine(athena_client=MagicMock())
        assert engine._database == "ridestream_prod"
        assert engine._workgroup == "ridestream-prod"
        assert engine._poll_interval == 2


class TestAthenaExecute:
    def test_execute_returns_results(
        self, athena_engine: AthenaQueryEngine, athena_client: MagicMock
    ) -> None:
        results = athena_engine.execute("SELECT id, name FROM users")

        assert len(results) == 2
        assert results[0] == {"id": "1", "name": "Alice"}
        athena_client.start_query_execution.assert_called_once()

    def test_execute_starts_query(
        self, athena_engine: AthenaQueryEngine, athena_client: MagicMock
    ) -> None:
        athena_engine.execute("SELECT 1")
        call_kwargs = athena_client.start_query_execution.call_args[1]
        assert call_kwargs["QueryString"] == "SELECT 1"
        assert call_kwargs["QueryExecutionContext"]["Database"] == "ridestream_prod"

    def test_execute_polls_until_succeeded(self, athena_client: MagicMock) -> None:
        athena_client.get_query_execution.side_effect = [
            {"QueryExecution": {"Status": {"State": "RUNNING"}}},
            {"QueryExecution": {"Status": {"State": "RUNNING"}}},
            {"QueryExecution": {"Status": {"State": "SUCCEEDED"}}},
        ]
        engine = AthenaQueryEngine(athena_client=athena_client, poll_interval=0, max_wait=10)
        engine.execute("SELECT 1")
        assert athena_client.get_query_execution.call_count == 3

    def test_execute_failed_raises(self, athena_client: MagicMock) -> None:
        athena_client.get_query_execution.return_value = {
            "QueryExecution": {"Status": {"State": "FAILED", "StateChangeReason": "syntax error"}}
        }
        engine = AthenaQueryEngine(athena_client=athena_client, poll_interval=0)
        with pytest.raises(QueryEngineError, match="FAILED.*syntax error"):
            engine.execute("BAD SQL")

    def test_execute_cancelled_raises(self, athena_client: MagicMock) -> None:
        athena_client.get_query_execution.return_value = {
            "QueryExecution": {"Status": {"State": "CANCELLED"}}
        }
        engine = AthenaQueryEngine(athena_client=athena_client, poll_interval=0)
        with pytest.raises(QueryEngineError, match="CANCELLED"):
            engine.execute("SELECT 1")

    def test_execute_timeout_raises(self, athena_client: MagicMock) -> None:
        athena_client.get_query_execution.return_value = {
            "QueryExecution": {"Status": {"State": "RUNNING"}}
        }
        engine = AthenaQueryEngine(athena_client=athena_client, poll_interval=0, max_wait=0)
        with pytest.raises(QueryEngineError, match="timed out"):
            engine.execute("SELECT 1")

    def test_skips_header_row(self, athena_engine: AthenaQueryEngine) -> None:
        results = athena_engine.execute("SELECT id, name FROM users")
        # Header row is skipped, only data rows returned
        assert len(results) == 2
        assert results[0]["id"] == "1"


class TestAthenaFactory:
    @pytest.fixture(autouse=True)
    def _mock_boto(self):
        with patch("ridestream.adapters.aws.athena_query_engine.boto3.client") as mock:
            mock.return_value = MagicMock()
            yield mock

    def test_factory_creates_engine(self) -> None:
        from ridestream.adapters.aws.athena_query_engine import create_athena_engine

        engine = create_athena_engine()
        assert isinstance(engine, AthenaQueryEngine)
