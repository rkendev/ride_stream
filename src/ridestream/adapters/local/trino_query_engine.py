"""Local Trino query engine adapter.

Implements QueryEngine port using trino-python-client.
Connects to Trino coordinator on port 8888 (local Docker).
"""

from __future__ import annotations

import logging

import trino

from ridestream.adapters.errors import QueryEngineError
from ridestream.domain.repositories import QueryEngine

logger = logging.getLogger(__name__)


class TrinoQueryEngine(QueryEngine):
    """Trino SQL query engine for local development.

    Args:
        host: Trino coordinator host.
        port: Trino coordinator port.
        user: Trino user.
        catalog: Default catalog.
        schema: Default schema.
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 8888,
        user: str = "trino",
        catalog: str = "hive",
        schema: str = "ridestream_dev",
    ) -> None:
        self._host = host
        self._port = port
        self._user = user
        self._catalog = catalog
        self._schema = schema

    def _get_connection(self) -> trino.dbapi.Connection:
        """Create a new Trino connection."""
        try:
            return trino.dbapi.connect(
                host=self._host,
                port=self._port,
                user=self._user,
                catalog=self._catalog,
                schema=self._schema,
            )
        except trino.exceptions.TrinoConnectionError as e:
            raise QueryEngineError(
                f"Failed to connect to Trino at {self._host}:{self._port}: {e}"
            ) from e

    def execute(self, sql: str) -> list[dict[str, object]]:
        """Execute a SQL query via Trino and return results as dicts.

        Args:
            sql: The SQL query.

        Returns:
            List of row dictionaries.

        Raises:
            QueryEngineError: If query execution fails.
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute(sql)
            columns = [desc[0] for desc in cursor.description] if cursor.description else []
            rows = cursor.fetchall()
            cursor.close()
            conn.close()
            return [dict(zip(columns, row, strict=True)) for row in rows]
        except trino.exceptions.TrinoUserError as e:
            raise QueryEngineError(f"Trino query error: {e}") from e
        except trino.exceptions.TrinoConnectionError as e:
            raise QueryEngineError(f"Trino connection error: {e}") from e
