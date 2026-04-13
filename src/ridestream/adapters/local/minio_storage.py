"""Local MinIO/S3 storage adapter — boto3 with MinIO endpoint.

Implements RideRepository port using S3-compatible MinIO for local development.
Uses same boto3 client as AWS adapter, just different endpoint.

All S3 operations wrapped with exponential-backoff retry for transient errors
(SlowDown, ServiceUnavailable, InternalError, RequestTimeout).
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from functools import partial
from typing import Any

import boto3
from botocore.exceptions import ClientError

from ridestream.adapters.errors import StorageError
from ridestream.domain.entities import Ride
from ridestream.domain.repositories import RideRepository
from ridestream.domain.value_objects import RideID, RideStatus

logger = logging.getLogger(__name__)

# Error codes worth retrying (transient / rate-limiting)
_RETRYABLE_CODES = frozenset(
    {
        "SlowDown",
        "ServiceUnavailable",
        "InternalError",
        "RequestTimeout",
        "RequestTimeoutException",
        "Throttling",
        "ThrottlingException",
        "TooManyRequestsException",
    }
)


def _retry_s3[T](
    operation: Callable[[], T],
    *,
    op_name: str,
    max_retries: int = 3,
) -> T:
    """Retry an S3 operation with exponential backoff on transient errors.

    Args:
        operation: Zero-arg callable that performs the S3 call.
        op_name: Human-readable operation name for logging.
        max_retries: Maximum number of attempts (default 3).

    Returns:
        Whatever the operation returns.

    Raises:
        ClientError: If a non-retryable error occurs or retries exhausted.
    """
    last_error: ClientError | None = None
    for attempt in range(1, max_retries + 1):
        try:
            return operation()
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            if code in _RETRYABLE_CODES and attempt < max_retries:
                backoff = 2**attempt
                logger.warning(
                    "s3_retry",
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
    # Unreachable — last attempt raises
    assert last_error is not None
    raise last_error


def create_s3_client(
    endpoint_url: str | None = None,
    region: str = "us-east-1",
    access_key: str = "minioadmin",
    secret_key: str = "minioadmin",
) -> boto3.client:
    """Create an S3 client, optionally targeting MinIO.

    Args:
        endpoint_url: MinIO endpoint (e.g. http://localhost:9000). None for real S3.
        region: AWS region.
        access_key: Access key (MinIO default or AWS IAM).
        secret_key: Secret key.

    Returns:
        Configured boto3 S3 client.
    """
    kwargs: dict[str, object] = {
        "service_name": "s3",
        "region_name": region,
        "aws_access_key_id": access_key,
        "aws_secret_access_key": secret_key,
    }
    if endpoint_url:
        kwargs["endpoint_url"] = endpoint_url
    return boto3.client(**kwargs)


class S3RideRepository(RideRepository):
    """Persists rides as JSON objects in S3/MinIO.

    Args:
        s3_client: Configured boto3 S3 client.
        bucket: S3 bucket name (default: bronze).
        prefix: Key prefix for ride objects (default: rides/).
        max_retries: Max attempts for transient S3 errors (default 3).
    """

    def __init__(
        self,
        s3_client: Any,
        bucket: str = "bronze",
        prefix: str = "rides/",
        max_retries: int = 3,
    ) -> None:
        self._s3 = s3_client
        self._bucket = bucket
        self._prefix = prefix
        self._max_retries = max_retries

    def _key(self, ride_id: RideID) -> str:
        return f"{self._prefix}{ride_id.value}.json"

    def save(self, ride: Ride) -> None:
        """Persist a ride as JSON to S3 (with retry).

        Args:
            ride: The ride to save.

        Raises:
            StorageError: If S3 put fails after retries.
        """
        key = self._key(ride.id)
        body = ride.model_dump_json().encode("utf-8")
        try:
            _retry_s3(
                lambda: self._s3.put_object(
                    Bucket=self._bucket,
                    Key=key,
                    Body=body,
                    ContentType="application/json",
                ),
                op_name="put_object",
                max_retries=self._max_retries,
            )
            logger.info("ride_saved", extra={"ride_id": str(ride.id), "key": key})
        except ClientError as e:
            raise StorageError(f"Failed to save ride {ride.id}: {e}") from e

    def get_by_id(self, ride_id: RideID) -> Ride | None:
        """Fetch a ride by ID from S3 (with retry).

        Args:
            ride_id: The ride identifier.

        Returns:
            The ride, or None if not found.

        Raises:
            StorageError: If S3 get fails (non-404) after retries.
        """
        key = self._key(ride_id)
        try:
            response = _retry_s3(
                lambda: self._s3.get_object(Bucket=self._bucket, Key=key),
                op_name="get_object",
                max_retries=self._max_retries,
            )
            body = response["Body"].read().decode("utf-8")
            data = json.loads(body)
            return Ride.model_validate(data)
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code in ("404", "NoSuchKey"):
                return None
            raise StorageError(f"Failed to get ride {ride_id}: {e}") from e

    def list_by_status(self, status: RideStatus) -> list[Ride]:
        """List rides by status (scans all rides in prefix, with retry).

        Args:
            status: The status to filter by.

        Returns:
            List of rides matching the status.

        Raises:
            StorageError: If S3 list/get fails after retries.
        """
        rides: list[Ride] = []
        try:
            response = _retry_s3(
                lambda: self._s3.list_objects_v2(Bucket=self._bucket, Prefix=self._prefix),
                op_name="list_objects_v2",
                max_retries=self._max_retries,
            )
            for obj in response.get("Contents", []):
                key = obj["Key"]
                if not key.endswith(".json"):
                    continue
                get_resp = _retry_s3(
                    partial(self._s3.get_object, Bucket=self._bucket, Key=key),
                    op_name="get_object",
                    max_retries=self._max_retries,
                )
                body = get_resp["Body"].read().decode("utf-8")
                ride = Ride.model_validate(json.loads(body))
                if ride.status == status:
                    rides.append(ride)
        except ClientError as e:
            raise StorageError(f"Failed to list rides: {e}") from e
        return rides

    def delete(self, ride_id: RideID) -> None:
        """Delete a ride from S3 (with retry).

        Args:
            ride_id: The ride to delete.

        Raises:
            StorageError: If S3 delete fails after retries.
        """
        key = self._key(ride_id)
        try:
            _retry_s3(
                lambda: self._s3.delete_object(Bucket=self._bucket, Key=key),
                op_name="delete_object",
                max_retries=self._max_retries,
            )
        except ClientError as e:
            raise StorageError(f"Failed to delete ride {ride_id}: {e}") from e
