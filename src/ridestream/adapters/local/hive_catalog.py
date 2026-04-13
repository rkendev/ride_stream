"""Local Hive Metastore catalog adapter.

Implements CatalogAdapter port using Trino as a proxy to Hive Metastore.
For local development, we use Trino SQL to manage Hive tables
rather than raw Thrift calls (simpler, more reliable).

All identifiers and locations are STRICTLY validated before any
interpolation into SQL to prevent injection attacks.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from ridestream.adapters.errors import CatalogError
from ridestream.domain.repositories import CatalogAdapter

logger = logging.getLogger(__name__)

# Mapping from simple types to Trino/Hive column types
_TYPE_MAP = {
    "string": "VARCHAR",
    "int": "INTEGER",
    "bigint": "BIGINT",
    "double": "DOUBLE",
    "float": "REAL",
    "boolean": "BOOLEAN",
    "timestamp": "TIMESTAMP",
}

# Accept only safe SQL identifiers: letters, digits, underscore.
# Must start with a letter or underscore.
_IDENTIFIER_PATTERN = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")

# Accept S3/S3A URIs with standard bucket/key characters.
_LOCATION_PATTERN = re.compile(r"^s3a?://[a-zA-Z0-9._/-]+/?$")

# Accept only known safe type strings (already in _TYPE_MAP values or matching
# the identifier pattern for passthrough).
_VALID_RAW_TYPES = frozenset(_TYPE_MAP.values()) | frozenset(_TYPE_MAP.keys())


def _validate_identifier(name: str, kind: str = "identifier") -> str:
    """Validate a SQL identifier — letters, digits, underscore only.

    Args:
        name: The identifier to validate.
        kind: Human-readable kind ("database", "table", "column") for errors.

    Returns:
        The identifier if valid.

    Raises:
        CatalogError: If the identifier contains unsafe characters.
    """
    if not isinstance(name, str) or not name:
        raise CatalogError(f"Invalid {kind}: empty or non-string")
    if not _IDENTIFIER_PATTERN.match(name):
        raise CatalogError(f"Invalid {kind}: {name!r} (allowed: [a-zA-Z_][a-zA-Z0-9_]*)")
    return name


def _validate_type(type_str: str) -> str:
    """Validate a column type.

    Args:
        type_str: The type name.

    Returns:
        The mapped Trino type (or the type as-is if already a valid raw type).

    Raises:
        CatalogError: If the type contains unsafe characters.
    """
    if type_str in _TYPE_MAP:
        return _TYPE_MAP[type_str]
    if type_str in _VALID_RAW_TYPES:
        return type_str
    # Allow parameterized types like VARCHAR(50), DECIMAL(10,2) — strict pattern
    if re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*(\(\d+(,\d+)?\))?$", type_str):
        return type_str
    raise CatalogError(f"Invalid type: {type_str!r}")


def _validate_location(location: str) -> str:
    """Validate an S3/S3A location URI.

    Args:
        location: The S3/S3A URI (e.g., s3a://bronze/path/).

    Returns:
        The location if valid.

    Raises:
        CatalogError: If the location doesn't match the safe pattern.
    """
    if not isinstance(location, str) or not location:
        raise CatalogError("Invalid location: empty or non-string")
    if not _LOCATION_PATTERN.match(location):
        raise CatalogError(f"Invalid S3 location: {location!r} (expected s3://... or s3a://...)")
    return location


class HiveCatalogAdapter(CatalogAdapter):
    """Hive Metastore catalog adapter via Trino SQL.

    Args:
        query_engine: A QueryEngine instance connected to Trino.
        catalog: Trino catalog name (default: hive).
    """

    def __init__(self, query_engine: Any, catalog: str = "hive") -> None:
        self._engine = query_engine
        self._catalog = _validate_identifier(catalog, "catalog")

    def create_table(
        self,
        database: str,
        table: str,
        columns: list[dict[str, str]],
        location: str,
    ) -> None:
        """Create a table in Hive Metastore via Trino.

        All inputs (database, table, column names/types, location) are
        validated against strict patterns before SQL interpolation.

        Args:
            database: Schema name (validated identifier).
            table: Table name (validated identifier).
            columns: Column definitions (each name + type validated).
            location: S3/MinIO data location (validated URI).

        Raises:
            CatalogError: If validation fails or table creation fails.
        """
        db_safe = _validate_identifier(database, "database")
        table_safe = _validate_identifier(table, "table")
        loc_safe = _validate_location(location)

        col_parts: list[str] = []
        for col in columns:
            name = _validate_identifier(col.get("name", ""), "column")
            type_str = _validate_type(col.get("type", ""))
            col_parts.append(f"{name} {type_str}")
        col_defs = ", ".join(col_parts)

        fqn = f"{self._catalog}.{db_safe}.{table_safe}"
        # All interpolated values validated by _validate_* helpers above.
        sql = (  # nosec B608
            f"CREATE TABLE IF NOT EXISTS {fqn} ({col_defs}) "
            f"WITH (external_location = '{loc_safe}', format = 'PARQUET')"
        )

        try:
            self._engine.execute(sql)
            logger.info("table_created", extra={"table": fqn, "location": loc_safe})
        except CatalogError:
            raise
        except Exception as e:
            raise CatalogError(f"Failed to create table {fqn}: {e}") from e

    def get_table_schema(
        self,
        database: str,
        table: str,
    ) -> list[dict[str, str]]:
        """Get schema for a table from Hive via Trino.

        Args:
            database: Schema name (validated identifier).
            table: Table name (validated identifier).

        Returns:
            Column definitions.

        Raises:
            CatalogError: If validation fails or schema lookup fails.
        """
        db_safe = _validate_identifier(database, "database")
        table_safe = _validate_identifier(table, "table")
        fqn = f"{self._catalog}.{db_safe}.{table_safe}"
        sql = f"SHOW COLUMNS FROM {fqn}"  # nosec B608

        try:
            rows = self._engine.execute(sql)
            return [
                {"name": str(row.get("Column", "")), "type": str(row.get("Type", ""))}
                for row in rows
            ]
        except CatalogError:
            raise
        except Exception as e:
            raise CatalogError(f"Failed to get schema for {fqn}: {e}") from e
