"""RideStream v2 — Daily Pipeline DAG.

Schedule: Daily at 2 AM UTC, catchup disabled.
Retries: 2 with 5-minute delay.

Tasks:
  1. simulate_rides      — generate and publish 100 rides to Kafka
  2. compute_metrics     — run Spark streaming metrics job
  3. wait_for_bronze     — wait for bronze data to land in S3/MinIO
  4. dbt_compile         — compile dbt models
  5. dbt_silver          — run silver layer models
  6. dbt_gold            — run gold layer models
  7. dbt_test            — run dbt tests
  8. export_to_athena    — (stub) export gold to Athena for BI
"""

from __future__ import annotations

from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.bash import BashOperator
from airflow.operators.python import PythonOperator

# ── Default args ──────────────────────────────────────────

default_args = {
    "owner": "ridestream",
    "depends_on_past": False,
    "email_on_failure": False,
    "email_on_retry": False,
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
}

# ── DAG definition ────────────────────────────────────────

dag = DAG(
    dag_id="ridestream_v2_daily",
    default_args=default_args,
    description="RideStream v2 daily pipeline: simulate -> transform -> test",
    schedule="0 2 * * *",  # Daily at 2 AM UTC
    start_date=datetime(2026, 1, 1),
    catchup=False,
    tags=["ridestream", "v2", "data-pipeline"],
)


# ── Task functions ────────────────────────────────────────


def _simulate_rides(**context):
    """Generate and publish simulated rides to Kafka."""
    import sys
    import os

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "src"))

    from ridestream.application.ride_simulator import RideSimulator
    from ridestream.application.generators import LocationGenerator
    from ridestream.config import Config

    config = Config()

    # Use a mock publisher for local dev (real Kafka in integration)
    from ridestream.adapters.cloud_provider_factory import CloudProviderFactory
    factory = CloudProviderFactory(config)
    publisher = factory.create_event_publisher()

    simulator = RideSimulator(
        event_publisher=publisher,
        config=config,
    )
    rides = simulator.run(num_rides=100)
    print(f"Generated {len(rides)} rides")


def _export_to_athena(**context):
    """Stub: Export gold tables to Athena for BI (production only)."""
    print("Export to Athena: skipped (local dev mode)")


# ── Tasks ─────────────────────────────────────────────────

# 1. Simulate rides
simulate_rides = PythonOperator(
    task_id="simulate_rides",
    python_callable=_simulate_rides,
    dag=dag,
)

# 2. Compute Spark metrics
compute_metrics = BashOperator(
    task_id="compute_metrics",
    bash_command=(
        "echo 'Spark metrics: would run spark-submit in production. "
        "Skipping in local Airflow.'"
    ),
    dag=dag,
)

# 3. Wait for bronze data
wait_for_bronze = BashOperator(
    task_id="wait_for_bronze_landing",
    bash_command="echo 'Bronze data check: assuming data landed for local dev.'",
    dag=dag,
)

# 4. dbt compile
dbt_compile = BashOperator(
    task_id="dbt_compile",
    bash_command="cd /opt/airflow/dbt && dbt compile --target dev --profiles-dir .",
    dag=dag,
)

# 5. dbt silver layer
dbt_silver = BashOperator(
    task_id="dbt_silver",
    bash_command="cd /opt/airflow/dbt && dbt run --select silver --target dev --profiles-dir .",
    dag=dag,
)

# 6. dbt gold layer
dbt_gold = BashOperator(
    task_id="dbt_gold",
    bash_command="cd /opt/airflow/dbt && dbt run --select gold --target dev --profiles-dir .",
    dag=dag,
)

# 7. dbt test
dbt_test = BashOperator(
    task_id="dbt_test",
    bash_command="cd /opt/airflow/dbt && dbt test --target dev --profiles-dir .",
    dag=dag,
)

# 8. Export to Athena (stub)
export_to_athena = PythonOperator(
    task_id="export_to_athena",
    python_callable=_export_to_athena,
    dag=dag,
)

# ── Task dependencies ─────────────────────────────────────

simulate_rides >> compute_metrics >> wait_for_bronze
wait_for_bronze >> dbt_compile >> dbt_silver >> dbt_gold >> dbt_test >> export_to_athena
