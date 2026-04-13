"""E2E test fixtures for RideStream v2.

AWS fixtures using boto3 — skip if AWS_ACCOUNT_ID not set.
These tests exercise the full AWS pipeline (MSK, Glue, Athena, S3).
"""

from __future__ import annotations

import os

import boto3
import pytest

AWS_ACCOUNT_ID = os.getenv("AWS_ACCOUNT_ID")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")

# Skip all E2E tests if no AWS credentials
pytestmark = pytest.mark.skipif(
    not AWS_ACCOUNT_ID,
    reason="AWS_ACCOUNT_ID not set — skipping E2E tests (requires AWS account)",
)


@pytest.fixture(scope="session")
def aws_region() -> str:
    return AWS_REGION


@pytest.fixture(scope="session")
def s3_client_aws():
    """Real AWS S3 client (not MinIO)."""
    return boto3.client("s3", region_name=AWS_REGION)


@pytest.fixture(scope="session")
def glue_client():
    """AWS Glue client for catalog operations."""
    return boto3.client("glue", region_name=AWS_REGION)


@pytest.fixture(scope="session")
def athena_client():
    """AWS Athena client for query execution."""
    return boto3.client("athena", region_name=AWS_REGION)
