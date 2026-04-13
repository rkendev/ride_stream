"""Unit tests for S3/MinIO storage adapters using moto."""

from __future__ import annotations

import boto3
import pytest
from moto import mock_aws

from ridestream.adapters.errors import StorageError
from ridestream.adapters.local.minio_storage import S3RideRepository, create_s3_client
from ridestream.domain.entities import Ride
from ridestream.domain.value_objects import (
    Distance,
    DriverID,
    Location,
    Money,
    PassengerID,
    RideID,
    RideStatus,
)

BUCKET = "test-bronze"
PREFIX = "rides/"


# ── Fixtures ──────────────────────────────────────────────


@pytest.fixture
def s3_client():
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        yield client


@pytest.fixture
def repo(s3_client) -> S3RideRepository:
    return S3RideRepository(s3_client=s3_client, bucket=BUCKET, prefix=PREFIX)


@pytest.fixture
def sample_ride() -> Ride:
    return Ride(
        id=RideID(value="ride-001"),
        passenger_id=PassengerID(value="p1"),
        pickup_location=Location(latitude=40.7128, longitude=-74.0060),
        dropoff_location=Location(latitude=40.7580, longitude=-73.9855),
    )


@pytest.fixture
def completed_ride() -> Ride:
    ride = Ride(
        id=RideID(value="ride-002"),
        passenger_id=PassengerID(value="p2"),
        pickup_location=Location(latitude=40.7128, longitude=-74.0060),
        dropoff_location=Location(latitude=40.7580, longitude=-73.9855),
    )
    ride = ride.accept(DriverID(value="d1"))
    ride = ride.start()
    return ride.complete(Distance(value_km=5.0), Money(cents=1500))


# ── create_s3_client ──────────────────────────────────────


class TestCreateS3Client:
    @mock_aws
    def test_creates_client_with_endpoint(self) -> None:
        client = create_s3_client(endpoint_url="http://localhost:9000")
        assert client is not None

    @mock_aws
    def test_creates_client_without_endpoint(self) -> None:
        client = create_s3_client(endpoint_url=None)
        assert client is not None


# ── S3RideRepository.save ─────────────────────────────────


class TestS3Save:
    def test_save_ride(self, repo: S3RideRepository, s3_client, sample_ride: Ride) -> None:
        repo.save(sample_ride)

        response = s3_client.get_object(Bucket=BUCKET, Key="rides/ride-001.json")
        body = response["Body"].read().decode("utf-8")
        assert "ride-001" in body

    def test_save_overwrites_existing(
        self, repo: S3RideRepository, s3_client, sample_ride: Ride
    ) -> None:
        repo.save(sample_ride)
        repo.save(sample_ride)  # should not raise

        response = s3_client.list_objects_v2(Bucket=BUCKET, Prefix=PREFIX)
        assert len(response["Contents"]) == 1

    def test_save_completed_ride(self, repo: S3RideRepository, completed_ride: Ride) -> None:
        repo.save(completed_ride)
        fetched = repo.get_by_id(completed_ride.id)
        assert fetched is not None
        assert fetched.status == RideStatus.COMPLETED

    def test_save_to_nonexistent_bucket_raises(self, s3_client, sample_ride: Ride) -> None:
        repo = S3RideRepository(s3_client=s3_client, bucket="nonexistent", prefix=PREFIX)
        with pytest.raises(StorageError, match="Failed to save"):
            repo.save(sample_ride)


# ── S3RideRepository.get_by_id ────────────────────────────


class TestS3GetById:
    def test_get_existing_ride(self, repo: S3RideRepository, sample_ride: Ride) -> None:
        repo.save(sample_ride)
        fetched = repo.get_by_id(sample_ride.id)

        assert fetched is not None
        assert str(fetched.id) == "ride-001"
        assert str(fetched.passenger_id) == "p1"
        assert fetched.status == RideStatus.REQUESTED

    def test_get_nonexistent_returns_none(self, repo: S3RideRepository) -> None:
        result = repo.get_by_id(RideID(value="nonexistent"))
        assert result is None

    def test_roundtrip_preserves_data(self, repo: S3RideRepository, completed_ride: Ride) -> None:
        repo.save(completed_ride)
        fetched = repo.get_by_id(completed_ride.id)

        assert fetched is not None
        assert fetched.distance is not None
        assert fetched.distance.value_km == 5.0
        assert fetched.fare is not None
        assert fetched.fare.cents == 1500
        assert str(fetched.driver_id) == "d1"


# ── S3RideRepository.list_by_status ───────────────────────


class TestS3ListByStatus:
    def test_list_filters_by_status(
        self, repo: S3RideRepository, sample_ride: Ride, completed_ride: Ride
    ) -> None:
        repo.save(sample_ride)
        repo.save(completed_ride)

        requested = repo.list_by_status(RideStatus.REQUESTED)
        assert len(requested) == 1
        assert str(requested[0].id) == "ride-001"

        completed = repo.list_by_status(RideStatus.COMPLETED)
        assert len(completed) == 1
        assert str(completed[0].id) == "ride-002"

    def test_list_empty_prefix(self, repo: S3RideRepository) -> None:
        result = repo.list_by_status(RideStatus.REQUESTED)
        assert result == []

    def test_list_no_matches(self, repo: S3RideRepository, sample_ride: Ride) -> None:
        repo.save(sample_ride)
        result = repo.list_by_status(RideStatus.CANCELLED)
        assert result == []


# ── S3RideRepository.delete ───────────────────────────────


class TestS3Delete:
    def test_delete_existing(self, repo: S3RideRepository, sample_ride: Ride) -> None:
        repo.save(sample_ride)
        repo.delete(sample_ride.id)
        assert repo.get_by_id(sample_ride.id) is None

    def test_delete_nonexistent_no_error(self, repo: S3RideRepository) -> None:
        repo.delete(RideID(value="nonexistent"))  # should not raise


# ── Key Generation ────────────────────────────────────────


class TestKeyGeneration:
    def test_key_format(self, repo: S3RideRepository) -> None:
        key = repo._key(RideID(value="abc-123"))
        assert key == "rides/abc-123.json"

    def test_custom_prefix(self, s3_client) -> None:
        repo = S3RideRepository(s3_client=s3_client, bucket=BUCKET, prefix="data/v2/")
        key = repo._key(RideID(value="xyz"))
        assert key == "data/v2/xyz.json"


# ── AWS Factory ──────────────────────────────────────────


class TestAWSFactory:
    @mock_aws
    def test_create_aws_repository(self) -> None:
        from ridestream.adapters.aws.s3_storage import create_aws_s3_repository

        repo = create_aws_s3_repository(bucket="my-bucket")
        assert isinstance(repo, S3RideRepository)
        assert repo._bucket == "my-bucket"
