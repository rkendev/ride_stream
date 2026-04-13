"""AWS Athena query engine adapter.

Implements QueryEngine port using boto3 Athena client.
Polls query execution status, reads results from S3.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import boto3
from botocore.exceptions import ClientError

from ridestream.adapters.errors import QueryEngineError
from ridestream.domain.repositories import QueryEngine

logger = logging.getLogger(__name__)


class AthenaQueryEngine(QueryEngine):
    """AWS Athena SQL query engine for production.

    Args:
        athena_client: Configured boto3 Athena client.
        output_location: S3 location for query results.
        database: Athena database/catalog.
        workgroup: Athena workgroup.
        poll_interval: Seconds between status polls.
        max_wait: Maximum wait time in seconds.
    """

    def __init__(
        self,
        athena_client: Any,
        output_location: str = "s3://ridestream-athena-results/output/",
        database: str = "ridestream_prod",
        workgroup: str = "ridestream-prod",
        poll_interval: int = 2,
        max_wait: int = 300,
    ) -> None:
        self._athena = athena_client
        self._output_location = output_location
        self._database = database
        self._workgroup = workgroup
        self._poll_interval = poll_interval
        self._max_wait = max_wait

    def execute(self, sql: str) -> list[dict[str, object]]:
        """Execute a SQL query via Athena.

        Starts query, polls until complete, returns results.

        Args:
            sql: The SQL query.

        Returns:
            List of row dictionaries.

        Raises:
            QueryEngineError: If query fails or times out.
        """
        try:
            # Start query
            response = self._athena.start_query_execution(
                QueryString=sql,
                QueryExecutionContext={"Database": self._database},
                ResultConfiguration={"OutputLocation": self._output_location},
                WorkGroup=self._workgroup,
            )
            query_id = response["QueryExecutionId"]
            logger.info("athena_query_started", extra={"query_id": query_id})

            # Poll for completion
            elapsed = 0
            while elapsed < self._max_wait:
                status_response = self._athena.get_query_execution(QueryExecutionId=query_id)
                state = status_response["QueryExecution"]["Status"]["State"]

                if state == "SUCCEEDED":
                    return self._get_results(query_id)
                elif state in ("FAILED", "CANCELLED"):
                    reason = status_response["QueryExecution"]["Status"].get(
                        "StateChangeReason", "Unknown"
                    )
                    raise QueryEngineError(f"Athena query {state}: {reason}")

                time.sleep(self._poll_interval)
                elapsed += self._poll_interval

            raise QueryEngineError(f"Athena query timed out after {self._max_wait}s")

        except ClientError as e:
            raise QueryEngineError(f"Athena API error: {e}") from e

    def _get_results(self, query_id: str) -> list[dict[str, object]]:
        """Fetch query results from Athena.

        Args:
            query_id: The query execution ID.

        Returns:
            List of row dictionaries.
        """
        try:
            response = self._athena.get_query_results(QueryExecutionId=query_id)
            result_set = response["ResultSet"]
            columns = [col["Name"] for col in result_set["ResultSetMetadata"]["ColumnInfo"]]

            rows: list[dict[str, object]] = []
            # Skip header row (first row is column names)
            for row_data in result_set["Rows"][1:]:
                values = [datum.get("VarCharValue", None) for datum in row_data["Data"]]
                rows.append(dict(zip(columns, values, strict=True)))

            return rows
        except ClientError as e:
            raise QueryEngineError(f"Failed to get results: {e}") from e


def create_athena_engine(
    region: str = "us-east-1",
    output_location: str = "s3://ridestream-athena-results/output/",
) -> AthenaQueryEngine:
    """Factory for Athena query engine."""
    client = boto3.client("athena", region_name=region)
    return AthenaQueryEngine(athena_client=client, output_location=output_location)
