"""Spark Structured Streaming metrics processor.

Computes 5-minute tumbling window aggregations over ride events.
Reads from Kafka, writes metrics back to Kafka.

Note: This module requires pyspark to be installed.
In local dev, Spark runs in Docker (rs2-spark-master).
Unit tests mock the Spark APIs.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MetricsConfig:
    """Configuration for the stream metrics processor.

    Args:
        kafka_brokers: Kafka bootstrap servers.
        input_topic: Topic to read ride events from.
        output_topic: Topic to write aggregated metrics to.
        checkpoint_dir: Spark checkpoint directory.
        window_duration: Tumbling window duration (e.g. '5 minutes').
    """

    kafka_brokers: str = "localhost:9092"
    input_topic: str = "ride.events.raw"
    output_topic: str = "ride.metrics.5min"
    checkpoint_dir: str = "/tmp/spark-checkpoints/ridestream"  # nosec B108
    window_duration: str = "5 minutes"


class StreamMetricsProcessor:
    """Computes 5-minute tumbling window metrics over ride events.

    Reads from Kafka input topic, groups by tumbling window, calculates:
    - rides_completed_count: number of RIDE_COMPLETED events
    - avg_fare: average fare in cents
    - avg_distance: average distance in km
    - max_surge: maximum surge multiplier

    Writes results to Kafka output topic as JSON.

    Args:
        spark_session: Active SparkSession.
        config: Metrics configuration.
    """

    def __init__(self, spark_session: Any, config: MetricsConfig) -> None:
        self._spark = spark_session
        self._config = config

    def compute_5min_metrics(self) -> object:
        """Start streaming query that computes 5-minute metrics.

        Returns:
            Spark StreamingQuery handle.
        """
        from pyspark.sql import functions as F  # noqa: N812
        from pyspark.sql.types import (
            DoubleType,
            IntegerType,
            StringType,
            StructField,
            StructType,
            TimestampType,
        )

        # Define schema for ride events
        event_schema = StructType(
            [
                StructField("event_id", StringType(), False),
                StructField("ride_id", StringType(), False),
                StructField("event_type", StringType(), False),
                StructField("timestamp", TimestampType(), False),
                StructField("data", StringType(), True),
            ]
        )

        # Read from Kafka
        raw_stream = (
            self._spark.readStream.format("kafka")
            .option("kafka.bootstrap.servers", self._config.kafka_brokers)
            .option("subscribe", self._config.input_topic)
            .option("startingOffsets", "latest")
            .load()
        )

        # Parse JSON values
        events = (
            raw_stream.select(
                F.from_json(F.col("value").cast("string"), event_schema).alias("event")
            )
            .select("event.*")
            .filter(F.col("event_type") == "ride_completed")
        )

        # Parse nested data field for metrics
        events_with_data = (
            events.withColumn(
                "fare_cents", F.get_json_object(F.col("data"), "$.fare_cents").cast(IntegerType())
            )
            .withColumn(
                "distance_km", F.get_json_object(F.col("data"), "$.distance_km").cast(DoubleType())
            )
            .withColumn(
                "surge_multiplier",
                F.get_json_object(F.col("data"), "$.surge_multiplier").cast(DoubleType()),
            )
        )

        # Tumbling window aggregation
        metrics = (
            events_with_data.withWatermark("timestamp", "10 minutes")
            .groupBy(F.window("timestamp", self._config.window_duration))
            .agg(
                F.count("*").alias("rides_completed_count"),
                F.avg("fare_cents").alias("avg_fare_cents"),
                F.avg("distance_km").alias("avg_distance_km"),
                F.max("surge_multiplier").alias("max_surge_multiplier"),
            )
            .select(
                F.col("window.start").alias("window_start"),
                F.col("window.end").alias("window_end"),
                "rides_completed_count",
                "avg_fare_cents",
                "avg_distance_km",
                "max_surge_multiplier",
            )
        )

        # Write to Kafka output topic
        query = (
            metrics.select(F.to_json(F.struct("*")).alias("value"))
            .writeStream.format("kafka")
            .option("kafka.bootstrap.servers", self._config.kafka_brokers)
            .option("topic", self._config.output_topic)
            .option("checkpointLocation", self._config.checkpoint_dir)
            .outputMode("update")
            .start()
        )

        logger.info(
            "streaming_metrics_started",
            extra={
                "input_topic": self._config.input_topic,
                "output_topic": self._config.output_topic,
                "window": self._config.window_duration,
            },
        )

        return query


def compute_batch_metrics(events: list[dict]) -> dict:
    """Compute metrics from a batch of completed ride events (no Spark required).

    Used for unit testing and lightweight local computation.

    Args:
        events: List of completed ride event dicts with data.fare_cents,
                data.distance_km, data.surge_multiplier.

    Returns:
        Aggregated metrics dict.
    """
    if not events:
        return {
            "rides_completed_count": 0,
            "avg_fare_cents": 0.0,
            "avg_distance_km": 0.0,
            "max_surge_multiplier": 0.0,
        }

    fares = []
    distances = []
    surges = []

    for event in events:
        data = event.get("data", {})
        if isinstance(data, dict):
            fares.append(float(data.get("fare_cents", 0)))
            distances.append(float(data.get("distance_km", 0)))
            surges.append(float(data.get("surge_multiplier", 1.0)))

    return {
        "rides_completed_count": len(events),
        "avg_fare_cents": sum(fares) / len(fares) if fares else 0.0,
        "avg_distance_km": sum(distances) / len(distances) if distances else 0.0,
        "max_surge_multiplier": max(surges) if surges else 0.0,
    }
