"""Adapter-level exceptions for RideStream v2.

These map infrastructure errors to domain-safe exceptions.
Application layer catches these — never raw kafka/boto3/trino errors.
"""


class AdapterError(Exception):
    """Base adapter error."""


class EventBusError(AdapterError):
    """Raised when Kafka/MSK operations fail."""


class StorageError(AdapterError):
    """Raised when S3/MinIO operations fail."""


class CatalogError(AdapterError):
    """Raised when Glue/Hive catalog operations fail."""


class QueryEngineError(AdapterError):
    """Raised when Athena/Trino query operations fail."""
