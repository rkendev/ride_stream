# Docker Image Versions

All images verified via `scripts/verify-images.sh` (Rule #10).

| Service | Image | Tag | Verified |
|---------|-------|-----|----------|
| Zookeeper | `zookeeper` | `3.8.0` | Yes |
| Kafka | `confluentinc/cp-kafka` | `7.6.0` | Yes |
| kafka-init | `confluentinc/cp-kafka` | `7.6.0` | Yes (same as Kafka) |
| MinIO | `minio/minio` | `RELEASE.2024-11-07T00-52-07Z` | Yes |
| MinIO Client | `minio/mc` | `latest` | Yes |
| Hive Metastore | `apache/hive` | `4.0.0` | Yes (custom Dockerfile) |
| Spark Master | `bitnami/spark` | `3.5.0-debian-12` | Yes |
| Spark Worker | `bitnami/spark` | `3.5.0-debian-12` | Yes (same as Master) |
| Trino | `trinodb/trino` | `430` | Yes |
| Airflow | `apache/airflow` | `2.9.3-python3.12` | Yes (custom Dockerfile) |
| Airflow DB | `postgres` | `15-alpine` | Yes |

## Rationale

- **Kafka 7.6.0**: Latest Confluent Platform at spec time. auto.create.topics=false enforced.
- **MinIO RELEASE.2024-11-07**: Pinned release, S3-compatible.
- **Hive 4.0.0**: Custom Dockerfile adds hadoop-aws JARs (ADR-012).
- **Spark 3.5.0**: Matches EMR Serverless version in production.
- **Trino 430**: Stable, Starburst-tested. dbt-trino compatible.
- **Airflow 2.9.3-python3.12**: Pinned Python version (§10.2).
- **Postgres 15-alpine**: Lightweight, for Airflow metadata only.
