"""AWS S3 storage adapter — boto3 implementation for production.

Uses real S3 (no endpoint override). Same interface as MinIO adapter.
"""

from __future__ import annotations

from ridestream.adapters.local.minio_storage import S3RideRepository, create_s3_client


def create_aws_s3_repository(
    bucket: str = "ridestream-bronze-prod",
    prefix: str = "rides/",
    region: str = "us-east-1",
) -> S3RideRepository:
    """Factory for AWS S3-backed ride repository.

    Uses default AWS credential chain (IAM role, env vars, ~/.aws/credentials).

    Args:
        bucket: S3 bucket name.
        prefix: Key prefix for ride objects.
        region: AWS region.

    Returns:
        Configured S3RideRepository.
    """
    client = create_s3_client(endpoint_url=None, region=region)
    return S3RideRepository(s3_client=client, bucket=bucket, prefix=prefix)
