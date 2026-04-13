# Airflow with dbt-trino for local development
# §10.2: MUST use exact tag apache/airflow:2.9.3-python3.12
# Verified via: docker manifest inspect apache/airflow:2.9.3-python3.12

FROM apache/airflow:2.9.3-python3.12

RUN pip install --no-cache-dir \
    dbt-core>=1.7.0 \
    dbt-trino>=1.7.0
