# Docker Services Reference

RideStream v2 runs 10 services locally via `docker/docker-compose.yml`.
All containers use the `rs2-` prefix to avoid conflicts with other projects.

## Service Overview

| Service | Container | Image | Host Port | Memory | Healthcheck |
|---------|-----------|-------|-----------|--------|-------------|
| Zookeeper | `rs2-zookeeper` | `confluentinc/cp-zookeeper:7.6.0` | 2181 | 512M | `echo srvr \| nc 2181` |
| Kafka | `rs2-kafka` | `confluentinc/cp-kafka:7.6.0` | 9092, 29092 | 1G | `kafka-broker-api-versions` |
| kafka-init | `rs2-kafka-init` | `confluentinc/cp-kafka:7.6.0` | — | 256M | exit 0 |
| MinIO | `rs2-minio` | `minio/minio:latest` | 9000, 9001 | 512M | `mc ready local` |
| minio-init | `rs2-minio-init` | `minio/mc:latest` | — | 128M | exit 0 |
| Hive Metastore | `rs2-hive-metastore` | Custom (from `apache/hive:4.0.0`) | 9083 | 1G | TCP 9083 |
| Trino | `rs2-trino` | `trinodb/trino:430` | 8888 | 1G | HTTP `/v1/info` |
| Spark Master | `rs2-spark-master` | `apache/spark:3.5.1-python3` | 7077, 8090 | 2G | HTTP `:8080` |
| Spark Worker | `rs2-spark-worker` | `apache/spark:3.5.1-python3` | 8091 | 2G | HTTP `:8081` |
| Airflow DB | `rs2-airflow-db` | `postgres:15-alpine` | 5433 | 512M | `pg_isready` |
| Airflow Web | `rs2-airflow-webserver` | `apache/airflow:2.9.3-python3.12` | 8085 | 1G | HTTP `/health` |
| Airflow Sched | `rs2-airflow-scheduler` | `apache/airflow:2.9.3-python3.12` | — | 1G | (via Airflow) |

**Total memory allocation: ~10 GB**

## Lifecycle

```bash
make docker-up      # Start all services (waits 120s for health)
make docker-down    # Stop + remove volumes
make smoke-test     # Verify all services healthy (6 checks)
```

Init containers (`kafka-init`, `minio-init`) run once and exit.
They create topics and buckets respectively.

## Service Details

### Zookeeper (port 2181)

- Kafka coordination (leader election, metadata).
- Confluent CP distribution for Kafka compatibility.
- 4LW commands whitelisted: `ruok`, `srvr`, `stat`.

### Kafka (ports 9092, 29092)

- Topics created by `kafka-init` on startup (`auto.create.topics.enable=false`).
- Topics: `ride-events`, `ride-events-dlq`, `ride-metrics-5min`, `gps-data`, `enriched-rides`.
- Dual listener config:
  - `localhost:9092` — for host tools (pytest, CLI)
  - `kafka:29092` — for other containers (kafka-init, Spark, Airflow)

### MinIO (ports 9000, 9001)

- S3-compatible object storage (`minioadmin`/`minioadmin` credentials).
- Console UI: http://localhost:9001
- Buckets: `bronze`, `silver`, `gold`, `athena-results` (created by `minio-init`).
- Uses path-style access (required for Hive S3A compatibility).

### Hive Metastore (port 9083)

- **Custom Dockerfile** (ADR-012): extends `apache/hive:4.0.0` with:
  - `hadoop-aws-3.3.6.jar`
  - `aws-java-sdk-bundle-1.12.565.jar`
- `hive-site.xml` configures S3A to talk to MinIO.
- Embedded Derby metastore (local dev only; production uses RDS).

### Trino (port 8888)

- **Trino 430** (pinned; 480 changed S3 config property names).
- Catalog: `hive` (mounted from `volumes/trino/catalogs/hive.properties`).
- Connects to Hive Metastore via Thrift (port 9083).
- Web UI: http://localhost:8888

### Spark (ports 7077, 8090, 8091)

- **apache/spark:3.5.1-python3** (matches EMR Serverless in prod).
- Master: port 7077 (cluster), 8090 (UI).
- Worker: port 8091 (UI).
- Runs in foreground via `spark-class` (not daemon mode).

### Airflow (port 8085)

- **apache/airflow:2.9.3-python3.12** (Python version pinned — §10.2).
- Postgres backend on port 5433 (host).
- Default login: `admin`/`admin`.
- DAGs mounted from `volumes/airflow/dags/`.
- LocalExecutor (single-node).

## Troubleshooting

### Container not starting

```bash
# Check status
docker ps -a --filter "name=rs2-"

# View logs
docker logs rs2-<service>

# Restart a single service
docker compose -f docker/docker-compose.yml restart <service>
```

### Port conflicts

If a host port is already in use, you'll see `bind: address already in use`.
Check what's using it:

```bash
sudo lsof -i :9092   # or netstat -tlnp | grep 9092
```

Either stop the conflicting process or edit `docker/docker-compose.yml` to
remap the host port.

### OOM errors (especially Trino)

If Trino crashes with `OutOfMemoryError`:

```bash
# Check overall memory
free -m

# Stop non-essential services temporarily
docker stop rs2-airflow-scheduler rs2-spark-worker

# Restart Trino
docker restart rs2-trino
```

Trino needs ~1GB heap minimum. On constrained VPS (<15GB RAM), run only
the services you need.

### Kafka can't connect

From a host client (pytest), use `localhost:9092`.
From inside Docker (kafka-init, Spark), use `kafka:29092`.

### Hive Metastore unhealthy

The healthcheck uses `/dev/tcp/localhost/9083` via bash. Verify:

```bash
docker exec rs2-hive-metastore bash -c "echo > /dev/tcp/localhost/9083 && echo OK"
```

If that works but the container shows unhealthy, the initial schema
initialization (Derby) may still be in progress. Wait 60-90s.

### Trino query fails: "Configuration property 'hive.s3.X' was not used"

You're probably on Trino 480+, which renamed these properties.
Either pin to `trinodb/trino:430` (current setup) or update
`volumes/trino/catalogs/hive.properties` to use `fs.native-s3.*` + `s3.*`.

### MinIO: Access Denied listing buckets

MinIO requires an authenticated client. Use the `mc` CLI via a separate
container:

```bash
docker run --rm --network docker_default --entrypoint sh minio/mc:latest \
  -c "mc alias set minio http://minio:9000 minioadmin minioadmin && mc ls minio/"
```

## Volume Mounts

| Mount | Purpose |
|-------|---------|
| `volumes/kafka-topics/create-topics.sh` | kafka-init reads this script |
| `volumes/trino/catalogs/hive.properties` | Trino catalog config |
| `volumes/airflow/dags/` | Airflow DAG directory |
| `rs2-minio-data` (named volume) | MinIO persistent storage |
| `rs2-airflow-db-data` (named volume) | Airflow metadata DB |

## Image Versions

Pinned in `docker/IMAGE_VERSIONS.md` — never use `latest` for production-bound images.

All images verified via `bash scripts/verify-images.sh` before being added
to `docker-compose.yml` (Rule #10).

## Cleanup

```bash
# Stop + remove containers + volumes
make docker-down

# Full cleanup (containers + volumes + images)
docker compose -f docker/docker-compose.yml down -v --rmi local
```
