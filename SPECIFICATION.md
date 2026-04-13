# RideStream v2 — Complete Specification

**Project Version:** 2.0  
**Status:** Specification Phase  
**Last Updated:** 2026-04-10  
**Post-Mortem Score (v1):** 7.65/10 (application excellent, infrastructure untested end-to-end)

---

## 1. PROBLEM DOMAIN

### 1.1 Problem Statement

RideStream v1 achieved excellent application code quality (scoring 9.2/10 for features and logic) but failed critical validation during deployment. The post-mortem identified ten infrastructure-level gaps that prevented end-to-end testing:

- Docker image tags were never verified to exist before deployment
- Kafka topic initialization was manual, not automated
- Hive Metastore lacked required S3A JARs for AWS integration
- dbt adapters were not explicitly specified in dependencies
- Local development environment (dbt-spark via Thrift Server) was unreliable
- Integration tests were empty stubs, not exercising the Docker stack
- dbt configuration was not validated against target adapters
- Pre-commit hooks were missing as dev dependencies
- Trino had no pre-configured Hive catalog
- No automated acceptance criteria for infrastructure readiness

These gaps meant the application logic worked in isolation but the full data pipeline—from Python event ingestion through Spark transformations to Athena queries—was never validated as a functioning system.

### 1.2 Solution Overview

RideStream v2 is a **restart with mandatory infrastructure validation**. We build the same real-time ride analytics lakehouse using AWS services, but with:

1. **Bronze/Silver/Gold data layers** (replacing v1's Raw/Refined/Business naming)
2. **Docker Compose + automated smoke tests** as local development environment
3. **dbt-trino as the local target** (proven reliable, versus dbt-spark's Thrift Server issues)
4. **Custom Hive Metastore Dockerfile** with hadoop-aws JARs pre-installed
5. **Kafka init container** wired into docker-compose for auto topic creation
6. **CodePipeline/CodeBuild** for CI/CD (AWS-native, not GitHub Actions)
7. **Image tag verification** as an automated acceptance gate
8. **Integration tests that exercise the full Docker stack** (not empty)
9. **All ten v1 findings codified as explicit gotchas with automated mitigations**

**Target deployment:** AWS (CloudFormation IaC + CodePipeline/CodeBuild CI/CD)  
**Target local dev:** Docker Compose (Kafka, Trino, Hive Metastore, dbt CLI)

### 1.3 Target Users

- **Ride operators:** Query real-time and historical trip metrics via Athena dashboards
- **Data engineers:** Transform raw events into analytics-ready fact tables using dbt
- **Platform engineers:** Deploy and maintain the lakehouse infrastructure on AWS
- **Analysts:** Run ad-hoc SQL queries on the Glue catalog via Athena
- **Data scientists:** Access curated data for ML pipeline feature engineering

### 1.4 Post-Mortem Lessons Applied (V1 Findings → V2 Mitigations)

All ten v1 post-mortem findings are codified into v2 as explicit requirements with automated gates:

#### F-001: Docker Image Tag Verification
**Finding:** Airflow image tag was not verified to exist before being placed in specs, leading to deployment failures.  
**Mitigation:**
- Acceptance criterion: "All image tags referenced in docker-compose.yml and CloudFormation templates must be verified against Docker Hub / AWS ECR registry before commit"
- Automated gate: Pre-commit hook validates image availability via `docker pull --dry-run` or registry API
- Applies to: Kafka, Trino, Hive Metastore, Spark, Python runtime images
- Documentation: Specification §7 Success Criteria, item #8

#### F-002: Kafka Topic Auto-Initialization
**Finding:** Topics were created manually via scripts, not guaranteed to exist before producers started, causing message loss.  
**Mitigation:**
- Solution: Kafka init container in docker-compose runs `kafka-topics.sh --create` before broker is marked healthy
- All required topics listed explicitly in specifications §4 Data Entities
- Health check in docker-compose ensures topics exist before application starts
- Documentation: Implementation section in docker-compose.yml

#### F-003: pyparsing Explicit Dev Dependency
**Finding:** moto Glue mocking requires pyparsing but it was implicit, causing test failures.  
**Mitigation:**
- Explicitly listed in `requirements-dev.txt`
- pinned version: `pyparsing>=3.0.0,<4.0.0`
- Test matrix includes "verify moto glue mocks work with pyparsing"
- Documentation: Dependencies section

#### F-004: Pre-commit Hooks as Dev Dependency
**Finding:** Pre-commit was not in dev dependencies, causing hook failures in CI/CD.  
**Mitigation:**
- Explicitly listed in `requirements-dev.txt`
- `.pre-commit-config.yaml` checked into version control
- CI/CD runs pre-commit hooks before build; blocks on failure
- Documentation: CI/CD section

#### F-005: dbt Adapter Extras Explicit
**Finding:** dbt adapters' extra dependencies (e.g., SQL dependencies for dbt-trino) were not specified, leading to import errors at runtime.  
**Mitigation:**
- `requirements-dbt.txt` explicitly specifies `dbt-trino[all]>=1.5.0` (local) and `dbt-spark[all]>=1.5.0` (AWS)
- Extras are documented in adapter notes
- Test gate: "dbt parse" succeeds for each target (trino locally, spark in EMR)
- Documentation: Dependencies section

#### F-006: dbt Source Config Validation Against Target
**Finding:** dbt source YAML defined Iceberg table formats not supported by dbt-spark, causing runtime failures.  
**Mitigation:**
- Every dbt source definition tested against the target adapter in integration tests
- Local: `dbt parse` against dbt-trino + Trino Hive catalog
- AWS: `dbt compile` against dbt-spark metadata (mocked in tests)
- Acceptance criterion: "All dbt sources validate successfully in both local (trino) and AWS (spark) targets"
- Documentation: dbt configuration section

#### F-007: No Duplicate dbt Model/Seed Names
**Finding:** Duplicate model names in different schemas caused Manifest conflicts.  
**Mitigation:**
- dbt parse enforces unique model identifiers (errors on duplicates)
- Pre-commit hook runs `dbt parse --manifest` to detect early
- Test: `dbt list` command includes assertions on unique names
- Documentation: dbt project structure

#### F-008: Trino Hive Catalog Pre-configured
**Finding:** Trino lacked Hive catalog configuration, making `select * from hive.default.table` fail in local dev.  
**Mitigation:**
- Trino configuration volume-mounted from `./docker/trino/catalog/hive.properties`
- Properties include metastore connection and S3A warehouse path
- Initialized before Trino container starts
- Acceptance criterion: "Trino can query tables via Hive catalog (`select * from hive.default.*`)"
- Documentation: Local environment setup

#### F-009: Hive Metastore Custom Dockerfile with S3A JARs
**Finding:** Hive Metastore Dockerfile was generic (no hadoop-aws JARs), so it couldn't connect to S3 in AWS.  
**Mitigation:**
- Custom Dockerfile at `./docker/hive-metastore/Dockerfile`
- Base image: `apache/hive:4.0.0`
- Adds JARs: `hadoop-aws-3.3.4.jar`, `aws-java-sdk-bundle-1.12.x.jar`
- Entrypoint: starts Hive Metastore Server
- Documentation: Docker build section, Infrastructure requirements

#### F-010: dbt-trino for Local Dev (not dbt-spark)
**Finding:** dbt-spark with Thrift Server was unreliable locally (port conflicts, session crashes); blocks fast iteration.  
**Mitigation:**
- Local target explicitly set to `dbt-trino` (target: `trino_local`)
- AWS target is `dbt-spark` (target: `spark_aws`) via EMR step
- profiles.yml separates configs clearly
- Acceptance criterion: "All dbt transformations run successfully via `dbt run --target trino_local`"
- Documentation: Local environment section, dbt configuration

---

## 2. USER STORIES

Nine user stories, each with infrastructure validation criteria:

### US-001: Ingest Real-Time Ride Events

**As a** ride event producer  
**I want to** send structured ride event messages to MSK Kafka  
**So that** the lakehouse can process events with sub-second latency

**Acceptance Criteria:**
- AC-1: Python producer connects to MSK broker and publishes RideEvent (trip_id, rider_id, driver_id, timestamp, lat, lon, status)
- AC-2: Messages are published to `rides.raw` topic with partition key = trip_id
- AC-3: At least 1000 messages/minute throughput sustained
- AC-4: **Infrastructure:** Kafka init container auto-creates `rides.raw` topic with 3 partitions before producer starts
- AC-5: **Integration test:** `test_ride_producer.py` validates end-to-end message flow to MSK (mocked locally with testcontainers-kafka)

**Estimation:** 13 points

---

### US-002: Stream Enriched Ride Events to S3 Bronze Layer

**As a** data engineer  
**I want to** deliver raw Kafka events to S3 Bronze via Kinesis Data Firehose  
**So that** events are durable and available for batch processing

**Acceptance Criteria:**
- AC-1: Firehose sinks `rides.raw` Kafka topic to s3://ridestream-{env}/bronze/rides/ (partitioned by date/hour)
- AC-2: Firehose buffer: 5 MB or 60 seconds (whichever first)
- AC-3: S3 objects are uncompressed JSON-lines format (1 event per line)
- AC-4: **Infrastructure:** Firehose IAM role has s3:PutObject and s3:PutObjectAcl on bronze prefix
- AC-5: **CloudFormation:** Firehose resource defined with explicit dependency on MSK cluster being available
- AC-6: **Smoke test:** Upload 100 test events to Kafka, verify S3 objects appear within 70 seconds

**Estimation:** 8 points

---

### US-003: Catalog Bronze Data with AWS Glue

**As a** data engineer  
**I want to** register S3 Bronze tables in the Glue Data Catalog  
**So that** EMR Spark and Athena can query bronze data without manual schema definition

**Acceptance Criteria:**
- AC-1: Glue Crawler scans s3://ridestream-{env}/bronze/ hourly, detects partition structure (year=2026/month=04/day=10/hour=15)
- AC-2: Crawler populates Glue database `ridestream_{env}_bronze` with table `rides` (schema inferred from Parquet/JSON)
- AC-3: Glue partition index tracks S3 data, enabling fast Athena partition pruning
- AC-4: **Infrastructure:** Glue Crawler IAM role includes s3:GetObject, s3:ListBucket, glue:UpdatePartition
- AC-5: **CloudFormation:** Crawler defined with explicit dependency on S3 bronze location
- AC-6: **Integration test:** `test_glue_catalog.py` (mocked with moto) validates Glue DDL and partition detection

**Estimation:** 8 points

---

### US-004: Transform Bronze → Silver with EMR Spark

**As a** data engineer  
**I want to** denormalize, deduplicate, and enrich raw ride events in Spark  
**So that** downstream models work with clean, consistent data

**Acceptance Criteria:**
- AC-1: Spark job (pyspark) reads from `ridestream_{env}_bronze.rides`
- AC-2: Applies transformations: deduplication by (trip_id, event_timestamp), NULL handling, geospatial validation
- AC-3: Outputs to s3://ridestream-{env}/silver/rides/ (partitioned by date)
- AC-4: EMR cluster (m5.xlarge, 3 nodes) provisions via CloudFormation
- AC-5: **Infrastructure:** EMR cluster IAM role includes s3:GetObject, s3:PutObject on bronze/silver paths
- AC-6: **Infrastructure:** EMR step includes explicit Spark config: `--conf spark.sql.adaptive.enabled=true --conf spark.sql.shuffle.partitions=200`
- AC-7: **Integration test:** `test_spark_bronze_silver.py` runs Spark job against local Trino (simulating S3 via volume mount)

**Estimation:** 13 points

---

### US-005: Build Fact Tables in Silver with dbt

**As a** data engineer  
**I want to** transform Silver ride events into fact tables (FactTrip, FactDriverDaily)  
**So that** business users can query meaningful metrics without writing ETL code

**Acceptance Criteria:**
- AC-1: dbt project transforms `ridestream_{env}_silver.rides` into `ridestream_{env}_silver.fact_trip` (grain: trip)
- AC-2: FactTrip includes: trip_id, driver_id, rider_id, distance, fare, duration, started_at, completed_at
- AC-3: dbt project also builds `ridestream_{env}_silver.fact_driver_daily` (grain: driver_date, aggregated metrics)
- AC-4: All dbt models pass `dbt test` (schema tests: not_null, unique, relationships)
- AC-5: **Infrastructure:** dbt configured with target=trino_local (local) and target=spark_aws (AWS EMR)
- AC-6: **Infrastructure:** dbt-trino extras explicitly specified: `dbt-trino[all]>=1.5.0`
- AC-7: **Infrastructure:** dbt source configs tested against Trino catalog (AC: `dbt parse --select source:*` succeeds)
- AC-8: **Smoke test:** `test_dbt_models.py` runs `dbt run --target trino_local` against Docker stack

**Estimation:** 13 points

---

### US-006: Refine Gold Layer Analytics Tables

**As a** a business analyst  
**I want to** query pre-aggregated metrics (daily trip counts, average fare, driver utilization)  
**So that** dashboards load quickly without complex joins

**Acceptance Criteria:**
- AC-1: dbt project builds `ridestream_{env}_gold.trips_daily` (date, count, total_fare, avg_distance)
- AC-2: Gold tables are indexed by date for fast queries
- AC-3: dbt lineage shows: Bronze → Silver → Gold (validated by `dbt docs generate`)
- AC-4: All Gold models include dbt snapshot for slowly-changing dimensions (driver_daily snapshot)
- AC-5: **Infrastructure:** dbt runs on EMR step or locally (profiles.yml supports both)
- AC-6: **Integration test:** `test_dbt_lineage.py` validates Gold tables have correct upstream dependencies

**Estimation:** 8 points

---

### US-007: Query Data via Athena

**As a** business analyst  
**I want to** run SQL queries on Glue catalog tables via Athena  
**So that** I can answer business questions without requiring data engineer support

**Acceptance Criteria:**
- AC-1: Athena workgroup `ridestream_{env}` queries Glue database `ridestream_{env}_bronze` (Bronze), `ridestream_{env}_silver` (Silver), `ridestream_{env}_gold` (Gold)
- AC-2: Query example: `SELECT DATE(started_at) as trip_date, COUNT(*) FROM ridestream_{env}_gold.trips_daily GROUP BY 1`
- AC-3: Athena output written to s3://ridestream-{env}/athena-results/
- AC-4: **Infrastructure:** Athena IAM role includes s3:GetObject on all layer paths, glue:GetTable, glue:GetDatabase
- AC-5: **CloudFormation:** Athena workgroup and output S3 location defined
- AC-6: **Integration test:** `test_athena_queries.py` (mocked with moto) validates query syntax and Glue schema

**Estimation:** 8 points

---

### US-008: Orchestrate ETL Steps with AWS Step Functions

**As a** platform engineer  
**I want to** define a state machine that chains Glue Crawler → Spark EMR → dbt on EMR → Athena query  
**So that** the full pipeline runs daily without manual intervention

**Acceptance Criteria:**
- AC-1: Step Functions state machine orchestrates: Glue Crawler start → wait for completion → EMR Spark step → dbt run → Athena query
- AC-2: State machine includes error handling: retry on transient Spark failures, SNS alert on critical failure
- AC-3: Scheduled to run daily at 2 AM UTC via CloudWatch Events
- AC-4: **Infrastructure:** Step Functions IAM role includes iam:PassRole for EMR, glue:StartCrawler, athena:StartQueryExecution
- AC-5: **CloudFormation:** State machine defined; includes explicit depends_on EMR cluster and Glue resources
- AC-6: **Integration test:** `test_orchestration.py` (mocked services) validates state transitions and error paths

**Estimation:** 13 points

---

### US-009: Deploy Infrastructure with CloudFormation and CI/CD

**As a** platform engineer  
**I want to** deploy the entire lakehouse stack (MSK, Firehose, EMR, Glue, Athena, Step Functions) via CloudFormation  
**And I want to** automate deployments through CodePipeline when code is merged to main  
**So that** infrastructure changes are version-controlled, auditable, and repeatable

**Acceptance Criteria:**
- AC-1: CloudFormation template (YAML) defines all AWS resources: MSK cluster, Kinesis Firehose, EMR cluster, Glue database/crawler, S3 buckets, IAM roles
- AC-2: Template includes parameters for environment (dev/staging/prod) and region
- AC-3: CodePipeline triggered on git push to main; stages: Source (CodeCommit) → Build (CodeBuild) → Deploy (CloudFormation)
- AC-4: CodeBuild project runs: `cfn-lint` on template, `pytest` on integration tests, `docker build` for custom images
- AC-5: **Infrastructure gate:** Image tag verification runs in CodeBuild before deployment (AC: Docker image refs verified against registries)
- AC-6: **Infrastructure gate:** Integration tests pass (Docker stack, dbt models, Glue mocks) before CloudFormation deploy stage
- AC-7: **Smoke test:** CodeBuild post-deploy runs: Kafka message injection, S3 object check, Athena query (validates end-to-end)
- AC-8: **CloudFormation:** Stack outputs include: MSK broker endpoint, Firehose delivery stream name, EMR cluster ID, Glue database name

**Estimation:** 21 points

---

## 3. DATA FLOW ARCHITECTURE

### 3.1 System Diagram

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│  LOCAL DEVELOPMENT (Docker Compose)                                             │
│  ────────────────────────────────────────────────────────────────────────────── │
│                                                                                  │
│  ┌─────────────────┐     ┌──────────────┐     ┌──────────────────────────────┐ │
│  │  Python Local   │     │ Kafka Broker │     │  Hive Metastore + Trino     │ │
│  │  (ride_producer)├────→│  (3 brokers) │────→│  (dbt-trino target)         │ │
│  │                 │     │              │     │                              │ │
│  └─────────────────┘     │ init: auto   │     │  Trino Hive Catalog         │ │
│                          │ topics       │     │  (volume-mounted)           │ │
│                          └──────────────┘     └──────────────────────────────┘ │
│                                  ↓                             ↓               │
│                          ┌───────────────────┐          ┌──────────────────┐   │
│                          │  S3 Mount (Local) │←─────────│  dbt Transforms  │   │
│                          │  Bronze/Silver    │          │  (dbt-trino CLI) │   │
│                          └───────────────────┘          └──────────────────┘   │
│                                                                                  │
└─────────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────────┐
│  AWS PRODUCTION (CloudFormation / CodePipeline)                                 │
│  ────────────────────────────────────────────────────────────────────────────── │
│                                                                                  │
│  ┌──────────────────────┐       ┌────────────────────┐                         │
│  │ Python Producer      │       │ MSK Kafka Cluster  │                         │
│  │ (EC2 / ECS Task)     ├──────→│ (3 brokers, AZ)   │                         │
│  │ rides.raw events     │       │                    │                         │
│  └──────────────────────┘       └────────┬───────────┘                         │
│                                           ↓                                    │
│                              ┌────────────────────────┐                        │
│                              │ Kinesis Data Firehose │                        │
│                              │ rides.raw → S3        │                        │
│                              │ buffer 5MB/60s        │                        │
│                              └────────────┬───────────┘                        │
│                                           ↓                                    │
│         ┌─────────────────────────────────────────────────────────────┐       │
│         │ S3 Lakehouse (Bronze/Silver/Gold)                           │       │
│         ├─────────────────────────────────────────────────────────────┤       │
│         │ s3://ridestream-prod/bronze/rides/year=2026/month=04/...   │       │
│         │ s3://ridestream-prod/silver/rides/year=2026/month=04/...   │       │
│         │ s3://ridestream-prod/gold/trips_daily/year=2026/month=04/..│       │
│         └────┬────────────────────────────┬────────────────────────┬─┘       │
│              ↓                            ↓                        ↓         │
│      ┌──────────────┐           ┌──────────────────┐     ┌──────────────┐   │
│      │ Glue Crawler │           │ EMR Spark Cluster│     │ Athena Workgroup
│      │ auto-detects │           │ Bronze → Silver  │     │ Queries: SQL   │   │
│      │ partitions   │           │ dbt on EMR step  │     └──────────────┘   │
│      └──────────────┘           └──────────────────┘                        │
│              ↓                          ↓                                    │
│      ┌──────────────┐           ┌──────────────────┐                        │
│      │ Glue Catalog │           │ dbt artifacts    │                        │
│      │ ridestream_* │           │ (manifest, run_  │                        │
│      │ databases    │           │  results.json)   │                        │
│      └──────────────┘           └──────────────────┘                        │
│                                           ↑                                  │
│                              ┌────────────┴───────────┐                     │
│                              │ Step Functions Executor│                     │
│                              │ Orchestrates pipeline  │                     │
│                              │ (daily 2 AM UTC)      │                     │
│                              └────────────────────────┘                     │
│                                           ↑                                  │
│                              ┌────────────┴───────────┐                     │
│                              │ CloudWatch Events      │                     │
│                              │ Scheduled trigger      │                     │
│                              └────────────────────────┘                     │
│                                                                              │
└──────────────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────────────┐
│ CI/CD PIPELINE (CodePipeline / CodeBuild)                                   │
│ ──────────────────────────────────────────────────────────────────────────── │
│                                                                               │
│  Source (CodeCommit)                                                         │
│  ↓                                                                            │
│  Build Stage (CodeBuild):                                                    │
│    ├─ cfn-lint template.yml                                                 │
│    ├─ Image tag verification (docker pull --dry-run)                        │
│    ├─ pytest integration tests (Docker stack)                               │
│    ├─ docker build hive-metastore:custom                                    │
│    └─ dbt parse --target trino_local                                        │
│  ↓                                                                            │
│  Deploy Stage (CloudFormation):                                              │
│    ├─ Create/Update stack in AWS                                            │
│    └─ Post-deploy smoke tests (Kafka, S3, Athena)                           │
│                                                                               │
└──────────────────────────────────────────────────────────────────────────────┘
```

### 3.2 Data Layer Definitions

**Bronze (Raw):**
- Source: Kafka → Firehose → S3
- Format: JSON-lines, uncompressed
- Schema: Inferred by Glue Crawler
- Retention: 90 days
- Partitioning: `year=YYYY/month=MM/day=DD/hour=HH`

**Silver (Cleaned/Enriched):**
- Source: Spark EMR job reads Bronze, transforms
- Format: Parquet, snappy compression
- Schema: Explicitly defined in Spark DDL
- Retention: 1 year
- Partitioning: `year=YYYY/month=MM/day=DD` (daily grain)
- Key transformations: dedup, NULL handling, geospatial validation

**Gold (Aggregated Analytics):**
- Source: dbt models from Silver
- Format: Parquet, snappy compression
- Schema: Defined in dbt models
- Retention: 2 years
- Partitioning: `year=YYYY/month=MM/day=DD` (daily grain for fact_daily; yearly for snapshots)
- Key tables: `fact_trip`, `fact_driver_daily`, `dim_driver`, `dim_rider`

---

## 4. DATA ENTITIES

### 4.1 RideEvent (Bronze Source)

```yaml
RideEvent:
  grain: event (one row per ride status change)
  schema:
    - trip_id: STRING (primary key)
    - event_timestamp: TIMESTAMP (event time, microsecond precision)
    - rider_id: STRING
    - driver_id: STRING (NULL if ride cancelled before driver assigned)
    - latitude: DOUBLE (nullable)
    - longitude: DOUBLE (nullable)
    - status: STRING (enum: requested, accepted, started, completed, cancelled)
    - fare_cents: BIGINT (NULL until completed; in USD cents)
    - distance_meters: DOUBLE (NULL until started; updated on completion)
    - event_version: INT (schema version, default 1)
  
  kafka_topic: rides.raw
  kafka_partitions: 3 (partition key = trip_id)
  
  sample:
    trip_id: "trip-2026-04-10-001"
    event_timestamp: "2026-04-10T14:23:45.123456Z"
    rider_id: "rider-user-5672"
    driver_id: NULL  # requested, not yet accepted
    latitude: 37.7749
    longitude: -122.4194
    status: "requested"
    fare_cents: NULL
    distance_meters: NULL
    event_version: 1
```

### 4.2 StagedRideEvent (Silver, deduplicated)

```yaml
StagedRideEvent:
  grain: event (one row per unique trip/timestamp, deduplicated)
  schema:
    - trip_id: STRING (primary key)
    - event_timestamp: TIMESTAMP (primary key)
    - rider_id: STRING
    - driver_id: STRING
    - latitude: DOUBLE
    - longitude: DOUBLE
    - status: STRING
    - fare_cents: BIGINT
    - distance_meters: DOUBLE
    - event_version: INT
    - _loaded_at: TIMESTAMP (when row was inserted to Silver; UTC)
    - _source_file: STRING (S3 path from Bronze)
  
  primary_key: (trip_id, event_timestamp)
  tests:
    - not_null: [trip_id, event_timestamp, rider_id, status]
    - unique: [trip_id, event_timestamp]
    - accepted_values: status in [requested, accepted, started, completed, cancelled]
    - relationships: trip_id references fact_trip.trip_id
```

### 4.3 FactTrip (Gold, fact table)

```yaml
FactTrip:
  grain: trip (one row per completed trip)
  schema:
    - trip_key: INT (surrogate primary key, dbt generated)
    - trip_id: STRING (business key, unique)
    - rider_id: STRING
    - driver_id: STRING
    - started_at: TIMESTAMP
    - completed_at: TIMESTAMP
    - duration_seconds: INT (completed_at - started_at)
    - distance_meters: DOUBLE
    - fare_cents: BIGINT
    - trip_date: DATE (started_at, for partitioning)
  
  primary_key: trip_key
  unique: trip_id
  tests:
    - not_null: [trip_key, trip_id, started_at, distance_meters, fare_cents]
    - unique: trip_id
    - relationships: rider_id → dim_rider, driver_id → dim_driver
  
  dbt_config:
    materialized: table
    partition_by:
      field: trip_date
      data_type: date
    cluster_by: [driver_id, rider_id]
```

### 4.4 FactDriverDaily (Gold, aggregate fact table)

```yaml
FactDriverDaily:
  grain: driver + date (one row per driver per day)
  schema:
    - driver_key: INT (surrogate primary key)
    - driver_id: STRING (business key)
    - activity_date: DATE (date of trips, primary key)
    - trips_completed: INT (count of completed trips)
    - trips_cancelled: INT (count of cancelled trips)
    - total_earnings_cents: BIGINT (sum of fares)
    - total_distance_meters: DOUBLE
    - avg_trip_duration_seconds: DOUBLE
    - online_hours: DOUBLE (approximate)
  
  primary_key: (driver_key, activity_date)
  unique: (driver_id, activity_date)
  tests:
    - not_null: [driver_key, driver_id, activity_date]
    - unique: (driver_id, activity_date)
  
  dbt_config:
    materialized: table
    partition_by:
      field: activity_date
      data_type: date
```

### 4.5 DimDriver (Gold, dimension)

```yaml
DimDriver:
  grain: driver (one row per driver, type-2 slowly-changing dimension)
  schema:
    - driver_key: INT (surrogate primary key)
    - driver_id: STRING (business key)
    - driver_name: STRING (PII, hashed in production)
    - vehicle_type: STRING (enum: sedan, suv, xl)
    - verification_date: DATE (when driver verified)
    - active_flag: BOOLEAN (current status)
    - effective_date: DATE (SCD type-2: start of validity)
    - end_date: DATE (SCD type-2: end of validity, NULL for current)
  
  primary_key: driver_key
  unique: (driver_id, effective_date)
  tests:
    - not_null: [driver_key, driver_id, active_flag]
  
  dbt_config:
    materialized: table
    +dbt_utils.generate_surrogate_key:
      - driver_id
      - effective_date
```

### 4.6 DimRider (Gold, dimension)

```yaml
DimRider:
  grain: rider (one row per rider, current state)
  schema:
    - rider_key: INT (surrogate primary key)
    - rider_id: STRING (business key)
    - rider_name: STRING (PII, hashed in production)
    - account_status: STRING (enum: active, suspended, deleted)
    - preferred_payment: STRING (enum: card, cash, account_balance)
    - account_created_date: DATE
    - _dbt_valid_from: TIMESTAMP (dbt snapshot)
    - _dbt_valid_to: TIMESTAMP
  
  primary_key: rider_key
  unique: rider_id
  tests:
    - not_null: [rider_key, rider_id, account_status]
```

### 4.7 Kafka Topics (Auto-Created by Init Container)

| Topic Name           | Partitions | Replication Factor | Key         | Value Format | Retention |
|----------------------|------------|-------------------|-------------|--------------|-----------|
| `rides.raw`          | 3          | 2                 | trip_id     | JSON         | 7 days    |
| `rides.enriched`     | 3          | 2                 | trip_id     | JSON         | 7 days    |
| `rides.dlq`          | 1          | 2                 | trip_id     | JSON         | 30 days   |

---

## 5. DATA VOLUME & COST ESTIMATES

### 5.1 Volume Projections (Annual, Production)

| Component | Daily Volume | Annual Volume | Notes |
|-----------|--------------|---------------|-------|
| Ride events (raw) | 500K events | 182.5M events | ~10 events per trip (status changes) |
| Trips completed | 50K trips | 18.25M trips | avg ~10 status events per trip |
| Bronze raw data | ~1.5 GB/day | ~547 GB/year | JSON-lines, uncompressed |
| Silver cleaned | ~300 MB/day | ~109 GB/year | Parquet, snappy (5:1 compression) |
| Gold aggregates | ~50 MB/day | ~18 GB/year | Pre-aggregated tables |
| Athena queries | ~100 queries/day | ~36.5K queries/year | avg 5 GB scanned per query |

### 5.2 AWS Cost Breakdown (Monthly, Development Environment)

| Service | Unit | Monthly Cost | Notes |
|---------|------|--------------|-------|
| MSK Kafka | 3 brokers × m5.large | $220 | Minimal traffic in dev |
| Kinesis Firehose | per GB delivered | $5 | ~150 GB/month in dev |
| S3 Storage (all layers) | per GB | $24 | ~700 GB across all layers |
| S3 Data Transfer (Firehose→S3) | per GB | $2 | 150 GB/month |
| Glue Crawler | per DPU hour | $8 | Hourly runs, ~10 min duration |
| EMR (daily Spark job) | m5.xlarge × 3 + 1 master | $15/day | ~5-hour daily runtime |
| dbt on EMR | (included in EMR) | $0 | Runs as part of Spark cluster |
| Athena (queries) | per TB scanned | $6 | ~5 TB/month scan volume |
| Step Functions | per state transition | $2 | ~30 transitions/day × ~30 days |
| CloudFormation | no charge | $0 | |
| CodePipeline | per pipeline | $1 | One pipeline, ~2 deployments/week |
| CodeBuild | per minute | $2 | ~100 min/month build time |
| **Total Monthly** | — | **$285** | All-in dev environment |

### 5.3 Scaling Notes

- **100 events/sec sustained:** Kafka cluster scales to r6i.2xlarge brokers (cost +$400/month)
- **10x volume:** All S3 costs, Athena, EMR, Firehose scale proportionally
- **Production multi-region:** +$1K/month for cross-region replication

---

## 6. NON-FUNCTIONAL REQUIREMENTS

### 6.1 Reliability

- **Target Availability:** 99.5% uptime (RTO: 4 hours, RPO: 1 hour)
- **Data Durability:** S3 replication (multi-AZ automatic); MSK replication factor = 2
- **Circuit Breaker Pattern:** Firehose delivery stream has DLQ for failed records; monitored by CloudWatch alarms
- **Backpressure Handling:** Kafka topics auto-scale partitions; Firehose buffer triggers at 5 MB or 60 sec (whichever first)
- **Failure Scenarios:**
  - Kafka broker down: 2/3 quorum continues, automatic leader election
  - EMR Spark job fails: Step Functions retries up to 3 times before SNS alert
  - Athena query timeout: Workgroup enforces 5-minute timeout; user sees error (no cascading failure)

### 6.2 Performance

- **Event-to-Athena Latency:** < 5 minutes (raw event published → queryable in Bronze/Silver)
  - Firehose buffer: 60 sec → S3 object
  - Glue Crawler: 60 sec → Glue Catalog
  - Athena: immediate query
- **Query Response Time:** < 10 seconds for Gold tables (pre-aggregated); < 60 seconds for Bronze scans
- **Throughput:** 1000 events/sec sustained (Kafka partition × 3 = 333 events/sec per partition)
- **Data Freshness:** Daily refresh (Spark → Silver, dbt → Gold); Athena always queries latest partition

### 6.3 Cost Optimization

- **S3 Lifecycle:** Bronze (90 days) → Silver (1 year) → Glacier (2 years) → delete (3 years)
- **Glue Crawler Schedule:** Hourly (only runs if S3 has new objects; on-demand cost ~$8/month)
- **EMR Spot Instances:** Use on-demand for master, spot for workers (savings ~40%)
- **Athena Partitioning:** All queries must filter by date to avoid full scans
- **Reserved Capacity:** Consider Kafka reserved capacity if production usage >40K events/hour

### 6.4 Security

- **Network Isolation:** MSK deployed in private VPC subnets; no public internet access
- **Encryption at Rest:** S3 (AES-256), MSK (KMS), Glue (KMS)
- **Encryption in Transit:** TLS 1.2 for all service-to-service communication; Kafka SASL/TLS
- **IAM Least Privilege:** Each service role has minimal permissions (principal of least privilege)
- **Audit Trail:** CloudTrail logs all API calls; CloudWatch logs for application events
- **Data Masking:** PII fields (rider_name, driver_name) hashed before Silver layer
- **GDPR Compliance:** 90-day Bronze retention allows deletion requests within compliance window

### 6.5 Observability

- **Metrics:** CloudWatch dashboards for Kafka lag, Firehose delivery rate, EMR job duration, Athena query performance
- **Logs:** CloudWatch Logs aggregates application logs, Spark driver logs, dbt run logs
- **Traces:** X-Ray disabled by default (cost); opt-in per Step Functions state
- **Alerts:** SNS topics for critical events (Spark job failure, Kafka consumer lag > 1 hour, S3 budget threshold)
- **Dashboards:**
  - Operational: Kafka lag, Firehose throughput, EMR cluster health
  - Business: Trips completed, revenue (aggregated), driver utilization

---

## 7. SCOPE BOUNDARIES

### 7.1 In Scope

1. **Data Ingestion:** Python producer → MSK Kafka (rides.raw topic)
2. **Stream Delivery:** Kinesis Firehose → S3 Bronze layer
3. **Data Cataloging:** AWS Glue Crawler → Glue Data Catalog
4. **Transformations:** Spark (Bronze → Silver), dbt (Silver → Gold)
5. **Query Interface:** Athena (SQL queries on Glue catalog)
6. **Orchestration:** Step Functions (daily pipeline)
7. **Infrastructure:** CloudFormation (IaC) + CodePipeline/CodeBuild (CI/CD)
8. **Local Development:** Docker Compose (Kafka, Hive Metastore, Trino, dbt-cli)
9. **Testing:** Integration tests (Docker stack), unit tests (dbt models), smoke tests (end-to-end)

### 7.2 Out of Scope

- **Real-time OLAP:** Drill-down dashboarding (use Redshift/BI tool post-MVP)
- **Stream Processing (Flink/Spark Streaming):** Currently batch-only (daily Spark jobs)
- **ML Feature Store:** Feature engineering is manual (future: SageMaker Feature Store)
- **Advanced Analytics:** Geospatial clustering, demand forecasting (future phases)
- **Multi-tenancy:** Single-tenant deployment (future: tenant isolation via S3 prefixes)
- **Compliance Automation:** Manual compliance reviews (future: automated guardrails)

### 7.3 Future Extensions

1. **Real-Time Dashboards:** Streaming aggregation (Spark Structured Streaming → DynamoDB → QuickSight)
2. **ML Models:** Trip demand prediction, surge pricing optimization (SageMaker)
3. **Data Monetization:** Public API for historical trip analytics (API Gateway + Lambda)
4. **Geographic Expansion:** Multi-region setup with cross-region replication
5. **Advanced Monitoring:** Custom metrics (trip duration distribution, fraud detection)

---

## 8. SUCCESS CRITERIA

The project is complete when all eleven criteria are met:

1. **Bronze layer ingests 90% of events within SLA (< 5 min Kafka → S3)**
   - Measured: CloudWatch metric on Firehose delivery latency
   - Gate: Smoke test validates 100 events appear in S3 within 70 seconds

2. **Silver layer deduplicates and validates data (zero duplicates in fact tables)**
   - Measured: dbt test on FactTrip.trip_id uniqueness
   - Gate: `dbt test` passes with 0 failures

3. **Gold layer aggregates correctly (daily trip counts match source events)**
   - Measured: dbt test comparing FactDriverDaily.trips_completed vs. raw event counts
   - Gate: Manual validation: 1 day of data, counts match within 0.1%

4. **Athena queries complete in < 10 seconds (Gold tables) / < 60 seconds (Bronze scans)**
   - Measured: CloudWatch metric Athena.TotalExecutionTime
   - Gate: Smoke test queries Gold/Bronze; assert response time < threshold

5. **Step Functions orchestrates pipeline end-to-end with no manual intervention**
   - Measured: Manual execution; 100% success rate for 5 consecutive days
   - Gate: Step Functions execution history shows all states completed successfully

6. **CloudFormation deployment is idempotent (update-stack succeeds, no rollback)**
   - Measured: Manual test; deploy stack twice, both succeed
   - Gate: `cfn-lint` passes; stack update creates no errors

7. **All infrastructure is version-controlled (no manual AWS console changes)**
   - Measured: Code review shows all resources in CloudFormation templates
   - Gate: Enforce via CodePipeline (no manual stack edits allowed)

8. **Docker image tags are verified before deployment**
   - Measured: Pre-commit hook validates image refs via registry API
   - Gate: CodeBuild stage runs `docker pull --dry-run` for all images; blocks on failure

9. **Integration tests pass (Docker stack exercises Kafka → dbt → Trino)**
   - Measured: pytest suite runs; all tests pass
   - Gate: `pytest integration_tests/` returns exit code 0

10. **Smoke tests pass (end-to-end: Kafka event → S3 object → Athena query)**
    - Measured: CodeBuild post-deploy phase runs smoke tests
    - Gate: All smoke tests pass; alerts if any fail

11. **dbt models parse and validate against target adapters (trino_local and spark_aws)**
    - Measured: `dbt parse --select *` succeeds for both targets
    - Gate: Integration test validates dbt source configs against Trino catalog

---

## 9. RISKS AND MITIGATIONS

### 9.1 Infrastructure Risks

| # | Risk | Likelihood | Impact | Mitigation |
|---|------|-----------|--------|-----------|
| R-001 | Kafka topic creation fails (init container not healthy) | Medium | High | Health check in docker-compose; test topic creation in integration tests |
| R-002 | Docker image tag doesn't exist (broken pull) | Medium | Critical | Pre-commit hook validates tags; CodeBuild dry-run before deploy |
| R-003 | Hive Metastore can't connect to S3 (missing JARs) | Low | Critical | Custom Dockerfile adds hadoop-aws JARs; smoke test validates S3 access |
| R-004 | dbt source config incompatible with adapter | Medium | High | Integration test validates dbt parse against Trino; separate profiles for each adapter |
| R-005 | Trino Hive catalog not configured (empty catalog) | Low | High | Volume-mount catalog config before Trino starts; validate in smoke test |
| R-006 | EMR Spark job fails mid-stream (no retry) | Medium | Medium | Step Functions retry policy (3 attempts, exponential backoff) |
| R-007 | CloudFormation stack update causes rollback | Low | Medium | cfn-lint validates template; test update on dev stack first |
| R-008 | dbt-spark Thrift Server crashes during CI/CD | Medium | Critical | Use dbt-trino locally (proven stable); dbt-spark only on EMR |
| R-009 | Firehose DLQ not monitored (data loss silent) | Low | High | CloudWatch alarm on DLQ message count; SNS alert |
| R-010 | Integration tests are empty (no real validation) | High | Critical | Populate with end-to-end test cases; require >80% code coverage |

### 9.2 Data Quality Risks

| # | Risk | Likelihood | Impact | Mitigation |
|---|------|-----------|--------|-----------|
| D-001 | Duplicate events in Silver (dedup logic fails) | Medium | Medium | dbt test on unique(trip_id, event_timestamp); manual QA on 1 day sample |
| D-002 | NULL values in fact tables (unexpected NULLs) | Low | Medium | dbt test not_null on critical columns; Spark DDL enforces schema |
| D-003 | Geospatial validation misses invalid coordinates | Low | Medium | Spark job validates lat/lon bounds; test with edge cases (0,0, poles) |
| D-004 | Partition pruning fails (Athena scans full table) | Medium | Low | All queries must include date filter; query validation in Athena workgroup |

### 9.3 Operational Risks

| # | Risk | Likelihood | Impact | Mitigation |
|---|------|-----------|--------|-----------|
| O-001 | Cost overrun (unexpected AWS charges) | Medium | Medium | CloudWatch budget alerts ($300/month dev); reserved capacity for production |
| O-002 | Kafka lag grows unchecked (events delay) | Low | Medium | CloudWatch alarm on consumer lag > 1 hour; auto-scale partitions |
| O-003 | Glue Crawler stuck (partition detection halts) | Low | Medium | Crawler timeout = 10 minutes; Step Functions timeout = 20 minutes; alerting |
| O-004 | S3 storage quota exceeded (Bronze layer fills) | Low | Medium | Lifecycle policy (90-day delete); CloudWatch alert on bucket size trend |

---

## 10. ZACHMAN FRAMEWORK DIMENSIONS

The RideStream v2 specification addresses Zachman Framework views:

| Dimension | View | Coverage |
|-----------|------|----------|
| **What (Data)** | Data entity definitions (§4), schema with lineage, partitioning strategy | Complete |
| **How (Process)** | Data flow architecture (§3), orchestration (Step Functions), transformation logic (Spark + dbt) | Complete |
| **Where (Location)** | S3 layer structure (Bronze/Silver/Gold), Kafka topics, Glue catalog, Athena workgroups, AWS regions | Complete |
| **Who (People)** | User stories (§2) identify stakeholders; IAM roles define access control | Complete |
| **When (Time)** | Pipeline scheduling (daily 2 AM UTC), event timestamps, SLAs (§6), data freshness | Complete |
| **Why (Motivation)** | Problem domain (§1) explains business need; success criteria (§8) define value delivery | Complete |

---

## 11. DUAL ENVIRONMENT STRATEGY

### 11.1 Local Development Environment (Docker Compose)

**Purpose:** Fast iteration, debugging, no AWS costs

**Components:**
- Kafka (3 brokers): testcontainers-kafka or docker-compose kafka image
- Hive Metastore: Custom Dockerfile (apache/hive:4.0.0 + hadoop-aws JARs)
- Trino: trino:latest with Hive catalog (volume-mounted config)
- dbt: dbt-trino CLI (installed in Python venv)
- Python: Custom image with ride_producer.py (or run locally)

**Configuration:**
```yaml
version: '3.8'
services:
  kafka:
    image: confluentinc/cp-kafka:7.5.0  # verified tag
    environment:
      KAFKA_BROKER_ID: 1
      KAFKA_AUTO_CREATE_TOPICS_ENABLE: "false"
    healthcheck:
      test: ["CMD", "kafka-topics.sh", "--list", "--bootstrap-server", "localhost:9092"]
      interval: 10s
      timeout: 5s
      retries: 5

  kafka-init:  # init container creates topics before brokers fully start
    image: confluentinc/cp-kafka:7.5.0
    depends_on:
      kafka:
        condition: service_healthy
    command: |
      kafka-topics.sh --create --topic rides.raw --partitions 3 --replication-factor 1 \
        --bootstrap-server kafka:29092 --if-not-exists

  hive-metastore:
    build: ./docker/hive-metastore  # custom image with hadoop-aws JARs
    image: ridestream:hive-metastore-custom
    environment:
      SERVICE_NAME: metastore
    ports:
      - "9083:9083"
    healthcheck:
      test: ["CMD", "nc", "-zv", "localhost", "9083"]

  trino:
    image: trinodb/trino:latest  # verified tag
    ports:
      - "8080:8080"
    volumes:
      - ./docker/trino/catalog:/etc/trino/catalog
    depends_on:
      hive-metastore:
        condition: service_healthy
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8080/ui/"]

  s3-local:
    image: localstack/localstack:latest  # optional: moto S3 mock
    ports:
      - "4566:4566"
    environment:
      SERVICES: s3
```

**Workflow:**
1. `docker-compose up --wait` (waits for health checks)
2. `python ride_producer.py` (publishes events to local Kafka)
3. `dbt run --target trino_local` (transforms via Trino)
4. `dbt test --target trino_local` (validates)
5. Integration tests: `pytest integration_tests/ --docker-compose-up`

### 11.2 AWS Production Environment (CloudFormation)

**Purpose:** Durable data platform with cost-optimized AWS services

**Stack Definition:** CloudFormation template deploys:
- MSK Kafka cluster (3 brokers, r6i.large, private subnets)
- Kinesis Firehose delivery stream (rides.raw → S3 bronze)
- S3 buckets (bronze, silver, gold, logs, artifacts)
- IAM roles (Firehose, EMR, Glue, Athena)
- Glue database, crawler, partitions
- EMR cluster (on-demand master, spot workers) for Spark transformations
- Step Functions state machine (orchestrates daily pipeline)
- CloudWatch alarms, SNS topics
- CodePipeline/CodeBuild for CI/CD

**Deployment:**
1. CodePipeline triggered on git push to main
2. CodeBuild stage: lint, test, build images, verify image tags
3. CloudFormation stage: create/update stack
4. Post-deploy smoke tests: Kafka event → S3 → Athena

### 11.3 Equivalence Across Environments

| Layer | Local (Docker) | AWS Production |
|-------|---|---|
| Message Queue | Kafka (docker-compose) | MSK Kafka (CloudFormation) |
| Raw Data Store | S3 (moto/localstack) | S3 (CloudFormation) |
| Metastore | Hive Metastore (docker) | Glue Data Catalog (CloudFormation) |
| Query Engine | Trino (docker) | Athena (CloudFormation) |
| Transform Engine | dbt-trino | dbt-spark (via EMR step) |
| Orchestration | Manual (dbt run) | Step Functions (CloudFormation) |

**Key difference:** Local uses Trino (dbt-trino), AWS uses Spark/Athena (dbt-spark + Athena). Configuration is separated via profiles.yml so dbt models run identically on both.

---

## 12. IMPLEMENTATION NOTES

### 12.1 Critical Acceptance Gates (Must Pass Before Merge to Main)

1. **Pre-commit Hook:** Image tag verification
   - Checks docker-compose.yml, CloudFormation template, Dockerfile refs
   - Verifies each image tag exists via `docker pull --dry-run` or registry API
   - Blocks commit if any image is missing
   - **Codifies:** F-001

2. **CodeBuild: Integration Tests**
   - Spins up Docker Compose stack
   - Publishes 100 test events to Kafka
   - Runs `dbt parse --target trino_local` (validates dbt source config)
   - Runs `dbt test --target trino_local`
   - Validates Hive Metastore can connect to S3 (mocked locally)
   - Validates Trino Hive catalog is queryable
   - **Codifies:** F-002, F-006, F-008, F-009, F-010

3. **CodeBuild: dbt Lint**
   - `dbt parse` on both targets (trino_local, spark_aws)
   - `dbt test` on all models
   - Checks for duplicate model names
   - **Codifies:** F-007, F-010

4. **CodeBuild: Dockerfile Build**
   - `docker build ./docker/hive-metastore`
   - Verifies custom Dockerfile has hadoop-aws JARs
   - Verifies docker build succeeds
   - **Codifies:** F-009

5. **CodeBuild: Dependency Checks**
   - `pip install -r requirements-dev.txt` (includes pyparsing, pre-commit)
   - `pip install -r requirements-dbt.txt` (includes dbt-trino[all], dbt-spark[all])
   - Verifies imports succeed
   - **Codifies:** F-003, F-004, F-005

### 12.2 dbt Configuration (profiles.yml)

```yaml
ridestream:
  outputs:
    trino_local:
      type: trino
      method: none  # local, no auth needed
      host: localhost
      port: 8080
      user: trino
      database: hive
      schema: default
      threads: 4

    spark_aws:
      type: spark
      method: odbc
      host: <EMR_CLUSTER_ENDPOINT>
      port: 10000  # Spark Thrift Server port
      user: hadoop
      database: ridestream_silver
      schema: public
      threads: 8

  target: trino_local  # default for local dev
```

**Note:** All dbt models reference `source('rides', 'rides')` which resolves to:
- Local: `hive.default.rides` (Trino Hive catalog)
- AWS: `ridestream_bronze.rides` (Glue catalog, via Spark)

Lineage is identical across both targets.

### 12.3 CloudFormation Stack Parameters

```yaml
Parameters:
  Environment:
    Type: String
    AllowedValues: [dev, staging, prod]
    Default: dev

  MSKBrokerNodeType:
    Type: String
    Default: kafka.m5.large

  EMRMasterNodeType:
    Type: String
    Default: m5.xlarge

  EMRWorkerNodeType:
    Type: String
    Default: m5.large

  EMRWorkerCount:
    Type: Number
    Default: 2

Outputs:
  MSKBrokerEndpoint:
    Value: !GetAtt MSKCluster.BootstrapBrokers
  
  GlueDatabase:
    Value: !Ref GlueDatabase

  S3BronzeBucket:
    Value: !Ref S3BronzeBucket

  AthenaWorkgroup:
    Value: !Ref AthenaWorkgroup
```

### 12.4 Smoke Test Checklist (CodeBuild Post-Deploy)

```bash
#!/bin/bash
set -e

echo "Smoke Test 1: Kafka broker is healthy"
aws kafka describe-cluster --cluster-arn $CLUSTER_ARN | grep -q "ACTIVE"

echo "Smoke Test 2: Publish 100 test events to rides.raw"
python tests/smoke_producer.py --count 100 --topic rides.raw --brokers $MSK_ENDPOINT

echo "Smoke Test 3: Check S3 objects in bronze layer (wait 70 sec)"
sleep 70
aws s3 ls s3://$BUCKET_NAME/bronze/rides/ | grep -q "json"

echo "Smoke Test 4: Glue Crawler detected partitions"
aws glue get-partitions --database-name ridestream_$ENV --table-name rides | grep -q "Partitions"

echo "Smoke Test 5: Athena query completes (Gold layer)"
QUERY_ID=$(aws athena start-query-execution \
  --query-string "SELECT COUNT(*) FROM ridestream_${ENV}_gold.trips_daily" \
  --query-execution-context Database=ridestream_${ENV}_gold \
  --result-configuration OutputLocation=s3://$BUCKET_NAME/athena-results/ | jq -r '.QueryExecutionId')
sleep 10
aws athena get-query-execution --query-execution-id $QUERY_ID | grep -q "SUCCEEDED"

echo "All smoke tests passed!"
```

---

## 13. DEPENDENCIES & TOOLING

### 13.1 Python Requirements

**requirements.txt** (runtime):
```
kafka-python>=2.0.0,<3.0.0
boto3>=1.26.0,<2.0.0
botocore>=1.29.0,<2.0.0
pyspark>=3.3.0,<4.0.0  # optional for local development
```

**requirements-dev.txt** (development):
```
pytest>=7.0.0,<8.0.0
pytest-cov>=4.0.0,<5.0.0
pytest-mock>=3.10.0,<4.0.0
moto>=4.1.0,<5.0.0  # AWS service mocks
moto[glue]>=4.1.0,<5.0.0  # Glue-specific mocks
testcontainers[kafka]>=3.7.0,<4.0.0  # Docker containers for tests
docker-compose>=1.29.0,<2.0.0  # Local dev environment
black>=22.0.0,<23.0.0
flake8>=4.0.0,<5.0.0
mypy>=0.990,<1.0.0
pre-commit>=2.20.0,<3.0.0
pyparsing>=3.0.0,<4.0.0  # required by moto glue
```

**requirements-dbt.txt** (dbt transformation):
```
dbt-core>=1.5.0,<2.0.0
dbt-trino[all]>=1.5.0,<2.0.0  # local target
dbt-spark[all]>=1.5.0,<2.0.0  # AWS target
dbt-utils>=0.9.0,<1.0.0  # macros for surrogate keys, etc
dbt-expectations>=0.5.0,<1.0.0  # data quality tests
```

### 13.2 AWS CLI Version

```
aws --version
# Expected: aws-cli/2.13.x (or later)
```

### 13.3 Docker Images (Verified Tags)

| Service | Image | Tag | Verified On | Notes |
|---------|-------|-----|-------------|-------|
| Kafka | confluentinc/cp-kafka | 7.5.0 | 2026-04-10 | Use Confluent, not Apache |
| Hive Metastore | apache/hive | 4.0.0 | 2026-04-10 | Custom Dockerfile adds JARs |
| Trino | trinodb/trino | 428 (latest) | 2026-04-10 | Verified Hive connector |
| Python | python | 3.11-slim | 2026-04-10 | For producer runtime |
| Spark | apache/spark | 3.3.0 | 2026-04-10 | For EMR (AWS-managed) |

**Verification Procedure:**
1. Before adding to specs: `docker pull <image>:<tag> && docker inspect <image>:<tag>`
2. Before deployment: `docker pull --dry-run <image>:<tag>` (verify availability)
3. Tag verification pre-commit hook blocks commit if image doesn't exist

---

## 14. GLOSSARY

| Term | Definition |
|------|-----------|
| **Bronze Layer** | Raw, unprocessed data from Kafka (JSON-lines, S3) |
| **Silver Layer** | Cleaned, deduplicated data (Parquet, optimized schema) |
| **Gold Layer** | Business-ready aggregates and dimensions (Parquet, pre-computed) |
| **Grain** | Level of detail in a fact table (e.g., event grain = one row per event) |
| **Surrogate Key** | System-generated primary key (dbt generates via `generate_surrogate_key`) |
| **SCD Type-2** | Slowly Changing Dimension: tracks history via effective_date/end_date |
| **dbt** | Data Build Tool; tool for transforming data in warehouses via SQL/Jinja2 |
| **dbt-trino** | dbt adapter for Trino (local development target) |
| **dbt-spark** | dbt adapter for Apache Spark/AWS EMR (AWS production target) |
| **Glue Catalog** | AWS managed data catalog (equivalent to Hive Metastore) |
| **Athena** | AWS SQL query engine over Glue catalog (no provisioning) |
| **Step Functions** | AWS service for orchestrating Lambda, EMR, Glue workflows |
| **Firehose** | AWS managed stream delivery service (Kafka/Kinesis → S3) |
| **MSK** | AWS Managed Streaming for Kafka (fully managed Kafka) |
| **EMR** | AWS Elastic MapReduce (managed Spark clusters) |
| **CloudFormation** | AWS IaC (Infrastructure as Code) using YAML/JSON templates |
| **CodePipeline** | AWS CI/CD orchestration (Source → Build → Deploy stages) |
| **CodeBuild** | AWS managed build service (runs arbitrary scripts) |

---

## APPENDIX A: V1 Post-Mortem Score Breakdown

| Category | Score | Notes |
|----------|-------|-------|
| Application Logic | 9.2/10 | Excellent dbt models, clean Spark code |
| Data Quality | 8.5/10 | Good schema, some edge cases in dedup |
| Infrastructure | 3.0/10 | **Critical gaps:** no image tag verification, no dbt validation, unreliable dbt-spark |
| Testing | 2.0/10 | Integration tests empty stubs; no Docker validation |
| Documentation | 7.0/10 | Architecture clear, but setup manual and error-prone |
| **Overall** | **7.65/10** | Application solid, infrastructure needs hardening |

**v2 Target:** Raise infrastructure score to 9/10 via mandatory image verification, Docker stack validation, and proven tooling (dbt-trino).

---

## APPENDIX B: Deployment Checklist

Before deploying to production:

- [ ] All image tags in docker-compose.yml verified (pre-commit hook)
- [ ] All image tags in CloudFormation template verified (pre-commit hook)
- [ ] Integration tests pass against Docker Compose stack (CodeBuild)
- [ ] dbt parse succeeds for both trino_local and spark_aws targets (CodeBuild)
- [ ] dbt test passes (all models, including data quality tests) (CodeBuild)
- [ ] Smoke tests pass (Kafka → S3 → Athena) (CodeBuild post-deploy)
- [ ] CloudFormation cfn-lint passes (CodeBuild)
- [ ] Code review approved (GitHub)
- [ ] Manual test on staging environment (2 full days of data)
- [ ] Performance baseline measured (query response times, cost)
- [ ] Runbooks created (deployment, rollback, incident response)
- [ ] On-call handoff completed

---

**End of Specification**

---

**Document Version:** 1.0  
**Effective Date:** 2026-04-10  
**Next Review Date:** 2026-05-10 (post-implementation)

