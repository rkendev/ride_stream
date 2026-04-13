"""Unit tests for Spark metrics processor.

Tests use compute_batch_metrics (no Spark needed) and mock the Spark APIs.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from ridestream.application.spark_metrics import (
    MetricsConfig,
    StreamMetricsProcessor,
    compute_batch_metrics,
)

# ── MetricsConfig ─────────────────────────────────────────


class TestMetricsConfig:
    def test_defaults(self) -> None:
        cfg = MetricsConfig()
        assert cfg.kafka_brokers == "localhost:9092"
        assert cfg.input_topic == "ride.events.raw"
        assert cfg.output_topic == "ride.metrics.5min"
        assert cfg.window_duration == "5 minutes"

    def test_custom(self) -> None:
        cfg = MetricsConfig(kafka_brokers="kafka:29092", window_duration="10 minutes")
        assert cfg.kafka_brokers == "kafka:29092"
        assert cfg.window_duration == "10 minutes"

    def test_frozen(self) -> None:
        cfg = MetricsConfig()
        with pytest.raises(AttributeError):
            cfg.kafka_brokers = "changed"  # type: ignore[misc]


# ── StreamMetricsProcessor ────────────────────────────────


class TestStreamMetricsProcessor:
    def test_init(self) -> None:
        spark = MagicMock()
        cfg = MetricsConfig()
        proc = StreamMetricsProcessor(spark_session=spark, config=cfg)
        assert proc._spark is spark
        assert proc._config is cfg


# ── compute_batch_metrics ─────────────────────────────────


class TestComputeBatchMetrics:
    def test_empty_events(self) -> None:
        result = compute_batch_metrics([])
        assert result["rides_completed_count"] == 0
        assert result["avg_fare_cents"] == 0.0
        assert result["avg_distance_km"] == 0.0
        assert result["max_surge_multiplier"] == 0.0

    def test_single_event(self) -> None:
        events = [
            {
                "event_type": "ride_completed",
                "data": {"fare_cents": 1500, "distance_km": 5.0, "surge_multiplier": 1.0},
            }
        ]
        result = compute_batch_metrics(events)
        assert result["rides_completed_count"] == 1
        assert result["avg_fare_cents"] == 1500.0
        assert result["avg_distance_km"] == 5.0
        assert result["max_surge_multiplier"] == 1.0

    def test_multiple_events(self) -> None:
        events = [
            {"data": {"fare_cents": 1000, "distance_km": 4.0, "surge_multiplier": 1.0}},
            {"data": {"fare_cents": 2000, "distance_km": 8.0, "surge_multiplier": 1.5}},
            {"data": {"fare_cents": 3000, "distance_km": 12.0, "surge_multiplier": 2.0}},
        ]
        result = compute_batch_metrics(events)
        assert result["rides_completed_count"] == 3
        assert result["avg_fare_cents"] == pytest.approx(2000.0)
        assert result["avg_distance_km"] == pytest.approx(8.0)
        assert result["max_surge_multiplier"] == 2.0

    def test_surge_max(self) -> None:
        events = [
            {"data": {"fare_cents": 100, "distance_km": 1, "surge_multiplier": 1.0}},
            {"data": {"fare_cents": 100, "distance_km": 1, "surge_multiplier": 3.5}},
            {"data": {"fare_cents": 100, "distance_km": 1, "surge_multiplier": 2.0}},
        ]
        result = compute_batch_metrics(events)
        assert result["max_surge_multiplier"] == 3.5

    def test_missing_data_fields(self) -> None:
        events = [{"data": {}}]
        result = compute_batch_metrics(events)
        assert result["rides_completed_count"] == 1
        assert result["avg_fare_cents"] == 0.0

    def test_no_data_key(self) -> None:
        events = [{}]
        result = compute_batch_metrics(events)
        assert result["rides_completed_count"] == 1

    def test_large_batch(self) -> None:
        events = [
            {"data": {"fare_cents": i * 100, "distance_km": i * 0.5, "surge_multiplier": 1.0}}
            for i in range(1, 101)
        ]
        result = compute_batch_metrics(events)
        assert result["rides_completed_count"] == 100
        assert result["avg_fare_cents"] == pytest.approx(5050.0)
        assert result["avg_distance_km"] == pytest.approx(25.25)

    def test_all_same_surge(self) -> None:
        events = [
            {"data": {"fare_cents": 1000, "distance_km": 5, "surge_multiplier": 1.5}}
            for _ in range(10)
        ]
        result = compute_batch_metrics(events)
        assert result["max_surge_multiplier"] == 1.5

    def test_zero_distance(self) -> None:
        events = [{"data": {"fare_cents": 500, "distance_km": 0, "surge_multiplier": 1.0}}]
        result = compute_batch_metrics(events)
        assert result["avg_distance_km"] == 0.0

    def test_result_keys(self) -> None:
        result = compute_batch_metrics(
            [{"data": {"fare_cents": 1000, "distance_km": 5, "surge_multiplier": 1.0}}]
        )
        expected_keys = {
            "rides_completed_count",
            "avg_fare_cents",
            "avg_distance_km",
            "max_surge_multiplier",
        }
        assert set(result.keys()) == expected_keys

    def test_negative_values_handled(self) -> None:
        # Edge case: negative values shouldn't crash
        events = [{"data": {"fare_cents": -100, "distance_km": -1, "surge_multiplier": 0.5}}]
        result = compute_batch_metrics(events)
        assert result["rides_completed_count"] == 1

    def test_float_precision(self) -> None:
        events = [
            {"data": {"fare_cents": 1000, "distance_km": 3.33, "surge_multiplier": 1.0}},
            {"data": {"fare_cents": 2000, "distance_km": 6.67, "surge_multiplier": 1.0}},
        ]
        result = compute_batch_metrics(events)
        assert result["avg_distance_km"] == pytest.approx(5.0)
