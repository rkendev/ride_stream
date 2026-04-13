"""Spark metrics job — runnable entry point.

Submit via spark-submit:
  spark-submit --packages org.apache.spark:spark-sql-kafka-0-10_2.12:3.5.1 \\
    infrastructure/spark/metrics_job.py

Or via Docker:
  docker exec rs2-spark-master spark-submit ...
"""

from __future__ import annotations

import os
import sys


def main() -> None:
    """Entry point for the Spark metrics job."""
    from pyspark.sql import SparkSession

    # Add src to path for imports
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

    from ridestream.application.spark_metrics import MetricsConfig, StreamMetricsProcessor

    spark = (
        SparkSession.builder.appName("RideStream-5min-Metrics")
        .config("spark.sql.shuffle.partitions", "4")
        .getOrCreate()
    )

    config = MetricsConfig(
        kafka_brokers=os.getenv("KAFKA_BOOTSTRAP_SERVERS", "kafka:29092"),
        input_topic=os.getenv("KAFKA_INPUT_TOPIC", "ride-events"),
        output_topic=os.getenv("KAFKA_OUTPUT_TOPIC", "ride-metrics-5min"),
        checkpoint_dir=os.getenv("SPARK_CHECKPOINT_DIR", "/tmp/spark-checkpoints/ridestream"),
    )

    processor = StreamMetricsProcessor(spark_session=spark, config=config)
    query = processor.compute_5min_metrics()

    print(f"Streaming query started: {query}")
    query.awaitTermination()


if __name__ == "__main__":
    main()
