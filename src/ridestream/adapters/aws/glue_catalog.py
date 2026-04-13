"""AWS Glue Data Catalog adapter.

Implements CatalogAdapter port using boto3 Glue client.
Operations wrapped with exponential-backoff retry for throttling errors.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

import boto3
from botocore.exceptions import ClientError

from ridestream.adapters.errors import CatalogError
from ridestream.domain.repositories import CatalogAdapter

logger = logging.getLogger(__name__)

# Mapping from simple types to Glue/Hive types
_TYPE_MAP = {
    "string": "string",
    "int": "int",
    "bigint": "bigint",
    "double": "double",
    "float": "float",
    "boolean": "boolean",
    "timestamp": "timestamp",
}

# Glue API errors worth retrying (throttling, transient)
_RETRYABLE_CODES = frozenset(
    {
        "ThrottlingException",
        "Throttling",
        "TooManyRequestsException",
        "ServiceUnavailableException",
        "InternalServiceException",
        "InternalFailureException",
    }
)


def _retry_glue[T](
    operation: Callable[[], T],
    *,
    op_name: str,
    max_retries: int = 3,
) -> T:
    """Retry a Glue API call with exponential backoff on throttling."""
    last_error: ClientError | None = None
    for attempt in range(1, max_retries + 1):
        try:
            return operation()
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            if code in _RETRYABLE_CODES and attempt < max_retries:
                backoff = 2**attempt
                logger.warning(
                    "glue_retry",
                    extra={
                        "operation": op_name,
                        "attempt": attempt,
                        "error_code": code,
                        "backoff_seconds": backoff,
                    },
                )
                time.sleep(backoff)
                last_error = e
                continue
            raise
    assert last_error is not None
    raise last_error


class GlueCatalogAdapter(CatalogAdapter):
    """AWS Glue Data Catalog adapter.

    Args:
        glue_client: Configured boto3 Glue client.
        database: Default database name.
        max_retries: Max attempts for throttled Glue calls.
    """

    def __init__(
        self,
        glue_client: Any,
        database: str = "ridestream_prod",
        max_retries: int = 3,
    ) -> None:
        self._glue = glue_client
        self._database = database
        self._max_retries = max_retries

    def create_table(
        self,
        database: str,
        table: str,
        columns: list[dict[str, str]],
        location: str,
    ) -> None:
        """Register a table in Glue Data Catalog (with retry).

        Args:
            database: Database name.
            table: Table name.
            columns: Column definitions.
            location: S3 data location.

        Raises:
            CatalogError: If table creation fails after retries.
        """
        glue_columns = [
            {"Name": col["name"], "Type": _TYPE_MAP.get(col["type"], col["type"])}
            for col in columns
        ]
        try:
            _retry_glue(
                lambda: self._glue.create_table(
                    DatabaseName=database,
                    TableInput={
                        "Name": table,
                        "StorageDescriptor": {
                            "Columns": glue_columns,
                            "Location": location,
                            "InputFormat": "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat",
                            "OutputFormat": "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat",
                            "SerdeInfo": {
                                "SerializationLibrary": "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe",
                            },
                        },
                        "TableType": "EXTERNAL_TABLE",
                    },
                ),
                op_name="create_table",
                max_retries=self._max_retries,
            )
            logger.info("table_created", extra={"database": database, "table": table})
        except ClientError as e:
            raise CatalogError(f"Failed to create table {database}.{table}: {e}") from e

    def get_table_schema(
        self,
        database: str,
        table: str,
    ) -> list[dict[str, str]]:
        """Get schema for a table from Glue Data Catalog (with retry).

        Args:
            database: Database name.
            table: Table name.

        Returns:
            Column definitions.

        Raises:
            CatalogError: If table lookup fails after retries.
        """
        try:
            response = _retry_glue(
                lambda: self._glue.get_table(DatabaseName=database, Name=table),
                op_name="get_table",
                max_retries=self._max_retries,
            )
            columns = response["Table"]["StorageDescriptor"]["Columns"]
            return [{"name": col["Name"], "type": col["Type"]} for col in columns]
        except ClientError as e:
            raise CatalogError(f"Failed to get schema for {database}.{table}: {e}") from e


def create_glue_catalog(region: str = "us-east-1") -> GlueCatalogAdapter:
    """Factory for Glue catalog adapter."""
    client = boto3.client("glue", region_name=region)
    return GlueCatalogAdapter(glue_client=client)
