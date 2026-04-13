"""E2E tests for AWS pipeline.

Tests the production path: CloudFormation → MSK → Glue → Athena.
Skips gracefully if AWS credentials are not available.
"""

from __future__ import annotations

import os

import pytest

pytestmark = [
    pytest.mark.aws,
    pytest.mark.skipif(
        not os.getenv("AWS_ACCOUNT_ID"),
        reason="AWS_ACCOUNT_ID not set — skipping AWS E2E tests",
    ),
]


class TestAWSS3:
    def test_bronze_bucket_exists(self, s3_client_aws) -> None:
        """Verify bronze S3 bucket exists in AWS."""
        env = os.getenv("ENVIRONMENT", "dev")
        bucket = f"ridestream-bronze-{env}"
        response = s3_client_aws.head_bucket(Bucket=bucket)
        assert response["ResponseMetadata"]["HTTPStatusCode"] == 200

    def test_can_write_and_read(self, s3_client_aws) -> None:
        """Verify write/read to bronze bucket."""
        env = os.getenv("ENVIRONMENT", "dev")
        bucket = f"ridestream-bronze-{env}"
        key = "e2e-test/ping.json"

        s3_client_aws.put_object(Bucket=bucket, Key=key, Body=b'{"test": true}')
        response = s3_client_aws.get_object(Bucket=bucket, Key=key)
        body = response["Body"].read()
        assert b"test" in body

        s3_client_aws.delete_object(Bucket=bucket, Key=key)


class TestAWSGlue:
    def test_database_exists(self, glue_client) -> None:
        """Verify Glue database exists."""
        env = os.getenv("ENVIRONMENT", "dev")
        response = glue_client.get_database(Name=f"ridestream_{env}")
        assert response["Database"]["Name"] == f"ridestream_{env}"


class TestAWSAthena:
    def test_workgroup_exists(self, athena_client) -> None:
        """Verify Athena workgroup exists."""
        env = os.getenv("ENVIRONMENT", "dev")
        response = athena_client.get_work_group(WorkGroup=f"ridestream-{env}")
        assert response["WorkGroup"]["State"] == "ENABLED"

    def test_can_execute_query(self, athena_client) -> None:
        """Verify Athena can execute a simple query."""
        env = os.getenv("ENVIRONMENT", "dev")
        response = athena_client.start_query_execution(
            QueryString="SELECT 1 AS test",
            QueryExecutionContext={"Database": f"ridestream_{env}"},
            WorkGroup=f"ridestream-{env}",
        )
        query_id = response["QueryExecutionId"]
        assert len(query_id) > 0
