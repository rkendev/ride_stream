# Architecture

## Principles

RideStream v2 applies three architectural patterns:

1. **Domain-Driven Design (DDD)** — Core business logic expressed as
   frozen Pydantic models with no external dependencies.
2. **Hexagonal Architecture (Ports & Adapters)** — Domain and application
   layers depend only on abstract interfaces (ports). Adapters implement
   those interfaces for specific infrastructure (Kafka, S3, Trino, …).
3. **Medallion Architecture** — Data flows through bronze (raw) → silver
   (cleaned) → gold (aggregated) layers, each managed by dbt models.

## Layer Boundaries

```
┌────────────────────────────────────────────────────────────────┐
│                      Entry Points                              │
│          CLI (click) · Airflow DAG · CodeBuild                 │
└──────────────────────────┬─────────────────────────────────────┘
                           │
┌──────────────────────────▼─────────────────────────────────────┐
│                    Application Layer                           │
│  RideSimulator · StreamMetricsProcessor · CloudProviderFactory │
│  (Imports ONLY ports + domain — never adapters)                │
└──────────────────────────┬─────────────────────────────────────┘
                           │
┌──────────────────────────▼─────────────────────────────────────┐
│                      Domain Layer                              │
│  Entities: Ride, RideEvent                                     │
│  Value Objects: RideID, Location, Distance, Money, Timestamp   │
│  Services: RideService, FareCalculationService                 │
│  Ports: EventPublisher, RideRepository, QueryEngine, Catalog   │
│  (Pure Python — only pydantic, datetime, hashlib, math)        │
└──────────────────────────┬─────────────────────────────────────┘
                           │
┌──────────────────────────▼─────────────────────────────────────┐
│                     Adapter Layer                              │
│  local/ (Docker)       │       aws/ (Production)               │
│  ─────────────         │       ──────────────                  │
│  KafkaEventPublisher   │       MSKEventPublisher               │
│  S3RideRepository*     │       S3RideRepository*               │
│  HiveCatalogAdapter    │       GlueCatalogAdapter              │
│  TrinoQueryEngine      │       AthenaQueryEngine               │
│                        │                                       │
│  *shared — different endpoint config                           │
└────────────────────────────────────────────────────────────────┘
```

## Domain Layer

**Location**: `src/ridestream/domain/`

Everything here is pure Python with zero external I/O. Only these imports
are allowed: `pydantic`, `datetime`, `hashlib`, `math`, `enum`, `typing`.

### Core Entities

- **Ride** (aggregate root) — Enforces state machine:
  `REQUESTED → ACCEPTED → IN_PROGRESS → COMPLETED/CANCELLED`.
  All state transitions are pure functions returning a new Ride.
- **RideEvent** — Domain event with deterministic ID from
  `hash(ride_id + event_type + timestamp)`.

### Value Objects

- `RideID`, `DriverID`, `PassengerID` — UUID wrappers with validation.
- `Location` — (lat, lon) with haversine distance method.
- `Distance`, `Money`, `Timestamp` — Frozen with precision controls.
- `EventType`, `RideStatus` — StrEnums.

### Ports (Abstract Base Classes)

`src/ridestream/domain/repositories.py` defines 5 ports:

| Port | Purpose |
|------|---------|
| `EventPublisher` | Publish domain events to Kafka/MSK |
| `EventStore` | Event sourcing append/read |
| `RideRepository` | Persist and retrieve rides |
| `CatalogAdapter` | Register tables with Hive/Glue |
| `QueryEngine` | Execute SQL against Trino/Athena |

## Adapter Layer

**Location**: `src/ridestream/adapters/`

Every port has both a `local/` and `aws/` implementation (Rule #2).

### Local Adapters (Docker)

| Adapter | Backing Service | Container |
|---------|-----------------|-----------|
| `KafkaEventPublisher` | Apache Kafka | `rs2-kafka:9092` |
| `KafkaEventStore` | Apache Kafka | `rs2-kafka:9092` |
| `S3RideRepository` | MinIO (S3-compat) | `rs2-minio:9000` |
| `HiveCatalogAdapter` | Hive via Trino SQL proxy | `rs2-hive-metastore:9083` |
| `TrinoQueryEngine` | Trino SQL engine | `rs2-trino:8888` |

### AWS Adapters (Production)

| Adapter | AWS Service | Notes |
|---------|-------------|-------|
| `MSKEventPublisher` | Amazon MSK | Same Kafka API, TLS + IAM auth |
| `S3RideRepository` | Amazon S3 | Identical boto3 client, no endpoint |
| `GlueCatalogAdapter` | AWS Glue Data Catalog | boto3 Glue client |
| `AthenaQueryEngine` | Amazon Athena | Polls query status, reads results |

All external calls include:
- **Timeout** (never None)
- **Retry logic** (exponential backoff)
- **Structured logging** with context
- **Error mapping** to adapter-level exceptions (`EventBusError`, `StorageError`, etc.)

## Application Layer

**Location**: `src/ridestream/application/`

Orchestrates domain logic and adapters. **Never** imports from `adapters/`;
receives ports via dependency injection.

### Services

- **RideSimulator** — Generates realistic ride lifecycles
  (REQUESTED → ACCEPTED → STARTED → COMPLETED) and publishes via
  `EventPublisher` port.
- **StreamMetricsProcessor** — Spark Structured Streaming job that
  computes 5-minute tumbling window metrics from `ride-events` topic.
- **CloudProviderFactory** — Routes `config.environment` to the correct
  adapter implementations.

## Data Flow (Medallion Architecture)

```
  Kafka Producers
       │
       ▼
  ┌─────────────────┐
  │ ride-events     │   (Kafka topic, 3 partitions)
  │ ride-events-dlq │
  │ ride-metrics-5min│
  └────────┬────────┘
           │
           ▼
  Kinesis Firehose / Kafka Connect
           │
           ▼
  ┌─────────────────────────┐
  │ Bronze (MinIO / S3)     │   Raw Parquet, partitioned by date
  │ s3://bronze/ride-events/│
  └──────────┬──────────────┘
             │  dbt source('bronze', 'ride_events')
             ▼
  ┌─────────────────────────┐
  │ Silver (Hive table)     │   Deduplicated, typed, validated
  │  - stg_rides            │
  │  - stg_metrics          │
  └──────────┬──────────────┘
             │  dbt ref('stg_rides')
             ▼
  ┌─────────────────────────┐
  │ Gold (aggregations)     │   BI-ready, denormalized
  │  - rides_summary        │
  │  - driver_metrics       │
  │  - passenger_metrics    │
  └──────────┬──────────────┘
             │
             ▼
       Analytics / BI
  (Athena SQL, QuickSight, etc.)
```

## Infrastructure

### Docker (Local)

10 services defined in `docker/docker-compose.yml`:

- Zookeeper + Kafka (event streaming)
- MinIO + minio-init (object storage)
- Hive Metastore (custom Dockerfile with hadoop-aws JARs)
- Trino (SQL query engine)
- Spark master + worker (stream processing)
- Airflow (webserver + scheduler + postgres-db)

See [DOCKER_SERVICES.md](DOCKER_SERVICES.md) for service-by-service details.

### AWS (Production)

CloudFormation stacks in `infrastructure/cloudformation/stacks/`:

- `networking.yaml` — VPC, subnets, security groups
- `iam.yaml` — 5 least-privilege roles
- `storage.yaml` — S3 buckets (bronze/silver/gold/athena-results)
- `streaming.yaml` — MSK cluster + Kinesis Firehose
- `compute.yaml` — EMR Serverless application
- `catalog.yaml` — Glue Data Catalog + Athena workgroup
- `orchestration.yaml` — Step Functions state machine (ASL in JSON)
- `monitoring.yaml` — CloudWatch dashboards + SNS alerts
- `codepipeline.yaml` — CodePipeline + CodeBuild
- `main.yaml` — Root nested stack

See [AWS_SETUP.md](AWS_SETUP.md) for deployment instructions.

## Why These Patterns?

### Why Hexagonal?

- **Testability**: Domain logic tested without any infrastructure.
- **Dual-target**: Same application code runs against local Docker or AWS.
- **Swappability**: Can migrate from Hive to Glue without changing application code.

### Why Medallion?

- **Isolation**: Bronze (raw) is immutable; silver/gold can be regenerated.
- **Cost**: dbt incremental models avoid full re-compute.
- **Governance**: Schema validation happens at silver boundary.

### Why Dual Adapters?

- **V1 lesson**: Building against AWS directly meant debugging on VPS.
  V2 builds both local and cloud adapters from day 1.
- **Fast feedback**: Integration tests against Docker catch bugs in seconds.
- **CI/CD friendly**: Tests run in CodeBuild without AWS provisioning.
