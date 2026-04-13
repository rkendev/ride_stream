# RideStream v2: Complete Task Decomposition

**Project:** RideStream v2 (Real-Time Ride Event Processing Pipeline)  
**Start Date:** 2026-04-10  
**Design Philosophy:** Infrastructure-first, validation-on-every-iteration, dual-target (Docker local + AWS cloud)

---

## EXECUTIVE SUMMARY

RideStream v2 is a restart addressing the critical lesson from v1: **infrastructure was never validated end-to-end** because acceptance criteria only mocked services via unit tests (moto), not actual Docker services.

**V2 Task Design Principles:**
1. Every infrastructure task has a **SMOKE TEST** acceptance criterion
2. Integration tests are **NOT deferred** — part of iteration they belong to
3. Docker image tags are **verified BEFORE** being used
4. dbt-trino is the **local target** (not dbt-spark)
5. Custom Hive Metastore Dockerfile is a **first-class task**
6. Kafka init container is **wired into docker-compose**
7. CodePipeline/CodeBuild for CI/CD **(not GitHub Actions)**

**Total Effort:** 6 iterations, 27 tasks, ~750 minutes (12.5 hours)

---

## ITERATION 0: INFRASTRUCTURE FIRST (Deploy Before You Feature)

### T001: Project Scaffold & Tooling
**Agent:** @coder  
**Size:** S (Small)  
**Est. Time:** 20 min  
**Dependencies:** None

**Deliverables:**
- `pyproject.toml` (from TECHNICAL_PLAN §9, VERBATIM)
- `Makefile` with targets: `setup`, `lint`, `test`, `test-integration`, `smoke-test`, `docker-up`, `docker-down`, `clean`
- `.gitignore` (Python + Docker + dbt + AWS)
- `.pre-commit-config.yaml` (black, flake8, mypy, isort, yamllint) with pre-commit in dev deps
- `.env.example` (AWS_REGION, AWS_PROFILE, KAFKA_BROKERS, MINIO_ENDPOINT, etc.)
- Directory structure with all `__init__.py` files:
  - `src/ridestream/` (domain, adapters, application, entry_points)
  - `tests/` (unit, integration, e2e)
  - `docker/` (hive, trino)
  - `infrastructure/` (kafka, minio, cloudformation)
  - `dbt_project/` (models, tests, macros, seeds)
  - `scripts/` (setup, teardown, local-deploy)

**Acceptance Criteria:**
- `make setup` completes without error
- `make lint` passes (black, flake8, mypy strict mode)
- `pre-commit install` succeeds
- All Python files have correct imports
- `pyproject.toml` is valid and installable (`pip install -e .`)
- `.env.example` documents all required variables

**Post-Mortem Notes:**
- V1 Issue: No central Makefile; devs ran scattered shell commands
- V2 Fix: All operations via `make` targets, fully documented

---

### T002: Docker Compose + Custom Dockerfiles
**Agent:** @infra  
**Size:** L (Large)  
**Est. Time:** 60 min  
**Dependencies:** T001

**Deliverables:**
- `docker/docker-compose.yml` (9 services):
  ```
  - zookeeper (confluentinc/cp-zookeeper:7.6.0, port 2181)
  - kafka (confluentinc/cp-kafka:7.6.0, port 9092, KAFKA_AUTO_CREATE_TOPICS_ENABLE=false)
  - kafka-init (custom init container, depends_on kafka, runs create-topics.sh)
  - minio (minio/minio:RELEASE.2024-01-18T22-51-49Z, port 9000, port 9001)
  - spark-master (bitnami/spark:3.5.0, port 8080, port 7077)
  - spark-worker (bitnami/spark:3.5.0, port 8081)
  - airflow (apache/airflow:2.8.1-python3.11, port 8000)
  - trino (trinodb/trino:latest, port 8080 → 8081 to avoid conflict, verified before use)
  - hive-metastore (custom Dockerfile, port 9083)
  ```
- `docker/hive/Dockerfile`:
  ```dockerfile
  FROM apache/hive:4.0.0
  # Install hadoop-aws, aws-java-sdk JARs for S3A compatibility
  RUN mkdir -p /opt/hadoop/share/hadoop/tools/lib && \
      curl -L https://repo1.maven.org/maven2/org/apache/hadoop/hadoop-aws/3.3.4/hadoop-aws-3.3.4.jar \
        -o /opt/hadoop/share/hadoop/tools/lib/hadoop-aws-3.3.4.jar && \
      curl -L https://repo1.maven.org/maven2/com/amazonaws/aws-java-sdk-bundle/1.12.565/aws-java-sdk-bundle-1.12.565.jar \
        -o /opt/hadoop/share/hadoop/tools/lib/aws-java-sdk-bundle-1.12.565.jar
  COPY hive-site.xml /opt/hive/conf/hive-site.xml
  ENTRYPOINT ["hive"]
  CMD ["--service", "metastore"]
  ```
- `docker/hive/hive-site.xml` (S3A config for MinIO):
  ```xml
  <configuration>
    <property>
      <name>hive.metastore.warehouse.dir</name>
      <value>s3a://bronze/warehouse</value>
    </property>
    <property>
      <name>fs.s3a.endpoint</name>
      <value>http://minio:9000</value>
    </property>
    <property>
      <name>fs.s3a.access.key</name>
      <value>minioadmin</value>
    </property>
    <property>
      <name>fs.s3a.secret.key</name>
      <value>minioadmin</value>
    </property>
    <property>
      <name>fs.s3a.path.style.access</name>
      <value>true</value>
    </property>
  </configuration>
  ```
- `docker/trino/catalog/hive.properties`:
  ```properties
  connector.name=hive
  hive.metastore.uri=thrift://hive-metastore:9083
  hive.s3.aws-access-key=minioadmin
  hive.s3.aws-secret-key=minioadmin
  hive.s3.endpoint=http://minio:9000
  hive.s3.path-style-access=true
  ```
- `docker/docker-compose.test.yml` (lighter version for CI):
  - Removes Spark workers, Airflow (keeps essentials: Kafka, MinIO, Trino, Hive)
  - Reduced resource limits
- `infrastructure/kafka/create-topics.sh`:
  ```bash
  #!/bin/bash
  # Wait for Kafka, create topics
  kafka-topics --bootstrap-server kafka:9092 --create --topic ride.events.raw \
    --partitions 3 --replication-factor 1 --if-not-exists
  kafka-topics --bootstrap-server kafka:9092 --create --topic ride.events.dlq \
    --partitions 1 --replication-factor 1 --if-not-exists
  kafka-topics --bootstrap-server kafka:9092 --create --topic ride.metrics.5min \
    --partitions 3 --replication-factor 1 --if-not-exists
  ```
- `infrastructure/minio/create-buckets.sh`:
  ```bash
  #!/bin/bash
  mc alias set minio http://minio:9000 minioadmin minioadmin
  mc mb minio/bronze || true
  mc mb minio/silver || true
  mc mb minio/gold || true
  mc mb minio/athena-results || true
  ```
- `scripts/setup.sh` (pull images, verify tags, build custom images)
- `scripts/teardown.sh` (stop, remove, cleanup)

**Acceptance Criteria (Smoke Test):**
- `make docker-up` starts all 9 containers successfully
- All containers show `healthy` status within 120 seconds
- Kafka topics exist and are queryable:
  ```bash
  docker exec kafka kafka-topics --bootstrap-server kafka:9092 --list | grep "ride.events.raw"
  docker exec kafka kafka-topics --bootstrap-server kafka:9092 --list | grep "ride.events.dlq"
  docker exec kafka kafka-topics --bootstrap-server kafka:9092 --list | grep "ride.metrics.5min"
  ```
- MinIO buckets exist and are queryable:
  ```bash
  docker exec minio mc ls minio/bronze
  docker exec minio mc ls minio/silver
  docker exec minio mc ls minio/gold
  docker exec minio mc ls minio/athena-results
  ```
- Trino connectivity test passes:
  ```bash
  docker exec trino trino-cli --execute "SELECT 1"
  ```
- Hive Metastore connectivity test passes:
  ```bash
  docker exec hive-metastore hive -e "SHOW DATABASES;"
  ```
- Total memory usage < 7 GB (`docker stats --no-stream`)
- `make smoke-test` target runs all checks above and reports pass/fail

**Image Tag Verification:**
- Before docker-compose.yml references any image, verify tag exists:
  ```bash
  docker manifest inspect confluentinc/cp-kafka:7.6.0 > /dev/null
  docker manifest inspect minio/minio:RELEASE.2024-01-18T22-51-49Z > /dev/null
  # etc.
  ```
- Document all tags and their rationale in `docker/IMAGE_VERSIONS.md`

**Post-Mortem Notes:**
- V1 Issue: Moto-only testing never caught S3A config mismatches, Kafka topic creation failures, Hive metastore timeouts
- V2 Fix: All services run in Docker Compose; smoke test verifies end-to-end connectivity before code changes
- V1 Issue: Spark relied on dbt-spark without validating Spark + Hive + Trino interop
- V2 Fix: dbt-trino is local target; Spark integration tested separately in T016

---

### T003: CloudFormation Templates
**Agent:** @infra  
**Size:** L (Large)  
**Est. Time:** 90 min  
**Dependencies:** T001

**Deliverables:**
- `infrastructure/cloudformation/main.yml` (root stack, orchestrates nested stacks)
- `infrastructure/cloudformation/networking.yml` (VPC, subnets, security groups, NAT)
- `infrastructure/cloudformation/iam.yml` (roles for Glue, Airflow, CodeBuild, Lambda)
- `infrastructure/cloudformation/storage.yml` (S3 buckets: bronze, silver, gold, athena-results, terraform-state)
- `infrastructure/cloudformation/streaming.yml` (MSK cluster, Kafka topics via custom resource)
- `infrastructure/cloudformation/compute.yml` (Glue jobs, Lambda functions)
- `infrastructure/cloudformation/catalog.yml` (Glue Data Catalog, Athena workgroup)
- `infrastructure/cloudformation/orchestration.yml` (EventBridge, Step Functions, Airflow MWAA)
- `infrastructure/cloudformation/monitoring.yml` (CloudWatch dashboards, alarms, SNS)
- `infrastructure/cloudformation/codepipeline.yml` (CodePipeline + CodeBuild CI/CD):
  - Source: CodeCommit (or GitHub via OAuth)
  - Build: CodeBuild (runs `make lint`, `make test`, `make smoke-test`, `make test-integration`)
  - Deploy: CloudFormation change sets to dev, staging, prod
- `scripts/deploy.sh` (create/update stacks, validate change sets)
- `scripts/destroy.sh` (delete stacks in reverse dependency order)
- `scripts/drift-check.sh` (detect manual changes, report drift)

**Acceptance Criteria:**
- All 10 CloudFormation templates validate:
  ```bash
  aws cloudformation validate-template --template-body file://infrastructure/cloudformation/*.yml
  ```
- Change sets can be created without errors for all templates
- IAM policies follow least-privilege (documented in `infrastructure/cloudformation/IAM_ROLES.md`)
- All parameter defaults align with `.env.example`
- Deployment order is explicit (no circular dependencies)
- Drift detection script runs without error

**Post-Mortem Notes:**
- V1 Issue: No IaC; manual AWS setup prone to configuration drift
- V2 Fix: Full CloudFormation with nested stacks, drift detection, change set validation
- V1 Issue: No CI/CD pipeline; testing local-only
- V2 Fix: CodePipeline + CodeBuild orchestrates lint → test → smoke → integration → deploy

---

### T004: dbt Project Scaffold
**Agent:** @coder  
**Size:** S (Small)  
**Est. Time:** 25 min  
**Dependencies:** T001, T002

**Deliverables:**
- `dbt_project/dbt_project.yml`:
  ```yaml
  name: ridestream
  version: 1.0.0
  config-version: 2
  profile: ridestream
  model-paths: ["models"]
  macro-paths: ["macros"]
  seed-paths: ["seeds"]
  test-paths: ["tests"]
  vars:
    env: local  # local | staging | prod
  require-dbt-version: [">=1.7.0", "<2.0"]
  ```
- `dbt_project/profiles.yml` (TWO profiles):
  ```yaml
  ridestream:
    target: trino
    outputs:
      trino:
        type: trino
        method: none
        host: localhost
        port: 8081
        user: trino
        database: hive
        schema: default
        threads: 4
      athena:
        type: athena
        s3_data_dir: s3://athena-results
        s3_data_naming: schema_table
        database: ridestream_db
        threads: 4
        poll_interval: 5
  ```
- `dbt_project/models/_sources.yml` (source definitions for bronze tables):
  ```yaml
  version: 2
  sources:
    - name: bronze
      tables:
        - name: ride_events_raw
          columns:
            - name: event_id
              description: Unique event identifier
              tests:
                - unique
                - not_null
  ```
- `dbt_project/macros/` (empty, ready for custom macros)
- `dbt_project/seeds/` (empty, ready for seed CSVs)
- `dbt_project/tests/` (empty, ready for custom tests)
- `dbt_project/.gitignore` (target/, dbt_packages/)
- `dbt_project/requirements.txt` (dbt-trino, dbt-athena)

**Acceptance Criteria:**
- `dbt debug --target trino --profiles-dir dbt_project` passes against local Docker Trino
- `dbt debug --target athena --profiles-dir dbt_project` passes (requires AWS credentials, can skip in local smoke test)
- `dbt parse` completes without error
- profiles.yml is syntactically valid YAML

**Post-Mortem Notes:**
- V1 Issue: dbt relied on Spark plugin; Spark + Hive interop was fragile
- V2 Fix: dbt-trino is primary local target (proven stable); dbt-athena for cloud, swappable via profile

---

## ITERATION 1: DOMAIN LAYER

### T005: Domain Entities & Value Objects
**Agent:** @coder  
**Size:** S (Small)  
**Est. Time:** 25 min  
**Dependencies:** T001

**Deliverables:**
- `src/ridestream/domain/value_objects.py`:
  - `RideID` (UUID)
  - `DriverID` (UUID)
  - `PassengerID` (UUID)
  - `Location` (lat/lng with validation)
  - `Distance` (km, non-negative)
  - `Money` (cents, supports USD)
  - `Timestamp` (ISO 8601, tz-aware)
  - `EventType` (enum: RIDE_REQUESTED, RIDE_ACCEPTED, RIDE_STARTED, RIDE_COMPLETED, RIDE_CANCELLED)
- `src/ridestream/domain/entities.py`:
  - `Ride` (aggregate root):
    - `id: RideID`, `driver_id: DriverID`, `passenger_id: PassengerID`
    - `pickup_location: Location`, `dropoff_location: Location`
    - `distance: Distance`, `fare: Money`
    - `status: RideStatus` (enum: REQUESTED, ACCEPTED, IN_PROGRESS, COMPLETED, CANCELLED)
    - `created_at: Timestamp`, `started_at: Optional[Timestamp]`, `completed_at: Optional[Timestamp]`
    - Methods: `accept(driver_id)`, `start()`, `complete(distance, fare)`, `cancel(reason)`
    - Implements equality, immutability where sensible
  - `RideEvent` (domain event):
    - `ride_id: RideID`, `event_type: EventType`
    - `timestamp: Timestamp`, `data: dict`
    - Used for event sourcing

**Test Coverage:** 60+ unit tests
- Value object immutability and equality
- Ride status transitions (valid and invalid)
- Event creation and domain event invariants
- Location validation (lat [-90, 90], lng [-180, 180])
- Distance non-negativity
- Money precision (cents)

**Acceptance Criteria:**
- All tests pass: `make test`
- All classes have docstrings (Google style)
- Type hints on all parameters and returns
- 100% code coverage (unit)

**Post-Mortem Notes:**
- V1 Issue: Domain layer was anemic; status transitions not enforced
- V2 Fix: Entities enforce invariants; Ride.accept() raises if already accepted

---

### T006: Domain Services
**Agent:** @coder  
**Size:** M (Medium)  
**Est. Time:** 40 min  
**Dependencies:** T005

**Deliverables:**
- `src/ridestream/domain/services.py`:
  - `RideService`:
    - `create_ride(passenger_id, pickup, dropoff) -> Ride`
    - `accept_ride(ride_id, driver_id) -> Ride` (raises if invalid state)
    - `start_ride(ride_id) -> Ride`
    - `complete_ride(ride_id, distance, fare) -> Ride`
    - `cancel_ride(ride_id, reason) -> Ride`
  - `FareCalculationService`:
    - `calculate_fare(distance: Distance, base_price: Money, rate_per_km: Money) -> Money`
    - Accounts for surge pricing (stub for now, to be enriched in iteration 3)
  - `EventAggregationService`:
    - `aggregate_events(events: List[RideEvent]) -> Ride` (reconstruct ride state from events)

**Test Coverage:** 70+ unit tests
- Service method contracts (args, return types, exceptions)
- State machine enforcement
- Fare calculations (basic and with surge)
- Event replay (given events, reconstruct correct Ride state)

**Acceptance Criteria:**
- All tests pass: `make test`
- All methods have docstrings + examples
- Type hints everywhere
- Exceptions are custom (e.g., `InvalidRideStateException`, `RideNotFoundError`)

**Post-Mortem Notes:**
- V1 Issue: No domain services; business logic scattered in adapters
- V2 Fix: Services encapsulate ride lifecycle; testable without infrastructure

---

### T007: Port Interfaces (Abstract Base Classes)
**Agent:** @coder  
**Size:** S (Small)  
**Est. Time:** 15 min  
**Dependencies:** T005, T006

**Deliverables:**
- `src/ridestream/domain/ports.py`:
  - `RideRepository` (abstract):
    - `save(ride: Ride) -> None`
    - `get_by_id(ride_id: RideID) -> Optional[Ride]`
    - `list_by_status(status: RideStatus) -> List[Ride]`
  - `EventPublisher` (abstract):
    - `publish(event: RideEvent) -> None`
  - `EventStore` (abstract):
    - `append(event: RideEvent) -> None`
    - `get_events_for_ride(ride_id: RideID) -> List[RideEvent]`

**Acceptance Criteria:**
- All ABCs are properly abstract (cannot be instantiated)
- Methods match adapter implementations (to be built in Iteration 2)

**Post-Mortem Notes:**
- V1 Issue: Adapters tightly coupled to domain
- V2 Fix: Ports define contracts; adapters implement for Kafka, S3, Hive, etc.

---

### T008: Configuration & Logging
**Agent:** @coder  
**Size:** S (Small)  
**Est. Time:** 15 min  
**Dependencies:** T001

**Deliverables:**
- `src/ridestream/config.py`:
  - `Config` dataclass:
    - `env: str` (local | staging | prod)
    - `log_level: str` (DEBUG | INFO | WARNING | ERROR)
    - `kafka_brokers: str`
    - `minio_endpoint: str`
    - `aws_region: str`
    - `aws_profile: Optional[str]`
    - Load from `.env` via `python-dotenv`
  - `LoggerFactory`:
    - Configures `structlog` + stdlib logging
    - JSON output in prod, colored text in local
- `src/ridestream/logging.py`:
  - Centralized logger setup
  - Context managers for operation tracing
  - Example: `with trace_operation("ride_creation"): ...`

**Test Coverage:** 20+ unit tests
- Config loading from env
- Logger initialization
- Log level switching

**Acceptance Criteria:**
- Config loads from `.env` without error
- Loggers output correct format (JSON in prod, text in local)
- `structlog` is in dev dependencies

**Post-Mortem Notes:**
- V1 Issue: No structured logging; print() statements everywhere
- V2 Fix: `structlog` with context correlation; prod-ready JSON logs

---

## ITERATION 2: ADAPTERS (DUAL-TARGET)

### T009: Kafka/MSK Event Stream
**Agent:** @coder  
**Size:** M (Medium)  
**Est. Time:** 40 min  
**Dependencies:** T001, T002, T006, T007

**Deliverables:**
- `src/ridestream/adapters/event_publisher_kafka.py`:
  - `KafkaEventPublisher` (implements `EventPublisher` port):
    - `__init__(brokers: str)`
    - `publish(event: RideEvent) -> None`
    - Serializes `RideEvent` to JSON
    - Routes to `ride.events.raw` topic
    - Includes error handling, retry logic (exponential backoff)
    - DLQ routing for failed publishes → `ride.events.dlq`
- `src/ridestream/adapters/event_store_kafka.py`:
  - `KafkaEventStore` (implements `EventStore` port):
    - `__init__(brokers: str)`
    - `append(event: RideEvent) -> None`
    - `get_events_for_ride(ride_id: RideID) -> List[RideEvent]`
    - Publishes to `ride.events.raw`
    - Reads from Kafka (via consumer with offset tracking)
- `src/ridestream/adapters/kafka_config.py`:
  - `KafkaConfig` dataclass (bootstrap_servers, compression, batch size, etc.)
  - Factories for producer and consumer

**Test Coverage:** 50+ unit tests
- Mock Kafka (using `confluent-kafka` mock mode or `testcontainers`)
- Event serialization/deserialization
- Retry logic on publish failure
- DLQ routing
- Event store replay

**Acceptance Criteria:**
- All tests pass: `make test`
- Integration test against Docker Kafka passes: `make test-integration` (calls T023)
- Smoke test verifies topics exist before adapter runs

**Dependencies:**
- confluent-kafka
- kafka-python (optional, for consumer offset management)

**Post-Mortem Notes:**
- V1 Issue: Mock Kafka only; never tested against real Kafka container
- V2 Fix: Unit tests + integration tests against Docker Kafka (T023)

---

### T010: S3/MinIO Data Lake
**Agent:** @coder  
**Size:** M (Medium)  
**Est. Time:** 35 min  
**Dependencies:** T001, T002, T006, T007

**Deliverables:**
- `src/ridestream/adapters/ride_repository_s3.py`:
  - `S3RideRepository` (implements `RideRepository` port):
    - `__init__(s3_client, bucket: str, prefix: str)`
    - `save(ride: Ride) -> None` (writes Parquet to `s3://bucket/prefix/ride_id.parquet`)
    - `get_by_id(ride_id: RideID) -> Optional[Ride]` (reads Parquet)
    - `list_by_status(status: RideStatus) -> List[Ride]` (scans prefix, filters)
- `src/ridestream/adapters/s3_client_factory.py`:
  - Factories for boto3 S3 client (local MinIO, AWS S3)
  - `create_s3_client(endpoint_url: Optional[str], region: str) -> S3Client`
  - Handles credentials (env vars, IAM role in prod)
- `src/ridestream/adapters/parquet_codec.py`:
  - `to_parquet(ride: Ride) -> bytes`
  - `from_parquet(data: bytes) -> Ride`

**Test Coverage:** 45+ unit tests
- S3 read/write with moto
- MinIO integration test (T023)
- Parquet codec serialization
- Bucket/prefix validation

**Acceptance Criteria:**
- All unit tests pass: `make test`
- Smoke test verifies MinIO buckets exist
- Integration test reads/writes to Docker MinIO: `make test-integration`

**Dependencies:**
- boto3
- pyarrow (Parquet)
- moto (testing)

**Post-Mortem Notes:**
- V1 Issue: Moto-only testing never caught S3A config mismatches (Hive ↔ S3)
- V2 Fix: Docker MinIO in smoke test; integration tests verify actual S3 operations

---

### T011: Glue/Hive Catalog
**Agent:** @coder  
**Size:** M (Medium)  
**Est. Time:** 30 min  
**Dependencies:** T001, T002, T006, T007

**Deliverables:**
- `src/ridestream/adapters/catalog_glue.py`:
  - `GlueCatalogAdapter` (implements abstract `CatalogAdapter` port):
    - `__init__(glue_client)`
    - `create_table(database: str, table: str, columns: List[Column], location: str) -> None`
    - `get_table_schema(database: str, table: str) -> Schema`
    - Uses boto3 Glue client
- `src/ridestream/adapters/catalog_hive.py`:
  - `HiveCatalogAdapter` (implements same `CatalogAdapter` port):
    - `__init__(hive_metastore_uri: str)`
    - Uses Thrift client to talk to Hive Metastore (port 9083)
    - `create_table()`, `get_table_schema()`
- `src/ridestream/domain/ports.py` (add):
  - `CatalogAdapter` (abstract base)
    - `create_table(database: str, table: str, columns: List[Column], location: str) -> None`
    - `get_table_schema(database: str, table: str) -> Schema`

**Test Coverage:** 35+ unit tests
- Glue client mocked (boto3 stubs)
- Hive Metastore mocked (Thrift stubs or testcontainers)
- Schema inference from Ride entity
- DDL generation and validation

**Acceptance Criteria:**
- All tests pass: `make test`
- Integration test against Docker Hive Metastore: `make test-integration`
- Smoke test verifies Hive Metastore responds to `SHOW DATABASES`

**Post-Mortem Notes:**
- V1 Issue: No abstraction over Glue vs Hive; code wasn't reusable for local dev
- V2 Fix: `CatalogAdapter` port + two implementations; swappable via config

---

### T012: Athena/Trino Query Engine
**Agent:** @coder  
**Size:** M (Medium)  
**Est. Time:** 30 min  
**Dependencies:** T001, T002, T007, T011

**Deliverables:**
- `src/ridestream/adapters/query_engine_athena.py`:
  - `AthenaQueryEngine` (implements `QueryEngine` port):
    - `__init__(athena_client, output_location: str, database: str)`
    - `execute(sql: str) -> Iterator[Row]`
    - Polls query execution status
    - Parses results from S3
- `src/ridestream/adapters/query_engine_trino.py`:
  - `TrinoQueryEngine` (implements `QueryEngine` port):
    - `__init__(trino_uri: str, user: str, database: str)`
    - `execute(sql: str) -> Iterator[Row]`
    - Uses `trino-python-client`
- `src/ridestream/domain/ports.py` (add):
  - `QueryEngine` (abstract):
    - `execute(sql: str) -> Iterator[Row]`

**Test Coverage:** 40+ unit tests
- Athena client mocked
- Trino client mocked
- SQL parsing (basic validation)
- Result parsing and Row construction

**Acceptance Criteria:**
- All tests pass: `make test`
- Integration test against Docker Trino: `make test-integration`
- Smoke test: `docker exec trino trino-cli --execute "SELECT 1"` passes

**Post-Mortem Notes:**
- V1 Issue: Relied on Spark SQL; Spark wasn't tested locally
- V2 Fix: Trino is local query engine; Athena is cloud fallback

---

### T013: Quality Gates
**Agent:** @coder  
**Size:** S (Small)  
**Est. Time:** 20 min  
**Dependencies:** T005, T006, T009, T010, T011, T012

**Deliverables:**
- `src/ridestream/adapters/quality_gates.py`:
  - `SchemaValidator`:
    - Validates Ride JSON against schema before publishing to Kafka
    - Uses `jsonschema`
  - `DataQualityChecker`:
    - Validates data invariants (non-negative distance, valid lat/lng, etc.)
    - Runs before persisting to S3
  - `DuplicateDetector`:
    - Checks for duplicate events (via ride_id + event_type + timestamp)

**Test Coverage:** 25+ unit tests
- Schema validation
- Quality rule enforcement
- Duplicate detection with various collision scenarios

**Acceptance Criteria:**
- All tests pass: `make test`
- Invalid events are rejected with clear error messages
- Duplicates are idempotent (re-processing same event is safe)

**Post-Mortem Notes:**
- V1 Issue: No data validation; garbage in, garbage out (GIGO)
- V2 Fix: Quality gates at adapter boundaries; fail fast

---

### T014: CloudProvider Factory
**Agent:** @coder  
**Size:** S (Small)  
**Est. Time:** 15 min  
**Dependencies:** T009–T013

**Deliverables:**
- `src/ridestream/adapters/cloud_provider_factory.py`:
  - `CloudProviderFactory`:
    - `create_event_publisher(config: Config) -> EventPublisher` (Kafka)
    - `create_event_store(config: Config) -> EventStore` (Kafka)
    - `create_ride_repository(config: Config) -> RideRepository` (S3 or Hive)
    - `create_catalog(config: Config) -> CatalogAdapter` (Glue or Hive)
    - `create_query_engine(config: Config) -> QueryEngine` (Athena or Trino)
  - Routes based on `config.env` and `config.cloud_provider` (local | aws)

**Acceptance Criteria:**
- All adapters are correctly instantiated via factory
- Config validation ensures required fields for chosen provider
- Circular dependencies avoided

**Post-Mortem Notes:**
- V1 Issue: Hardcoded adapter instantiation; switching local ↔ cloud required code changes
- V2 Fix: Factory pattern; swappable via config

---

## ITERATION 3: APPLICATION LAYER

### T015: Ride Simulator
**Agent:** @coder  
**Size:** M (Medium)  
**Est. Time:** 40 min  
**Dependencies:** T006, T009, T014

**Deliverables:**
- `src/ridestream/application/ride_simulator.py`:
  - `RideSimulator`:
    - `__init__(event_publisher: EventPublisher, config: Config)`
    - `generate_ride() -> Ride` (random passenger, driver, location)
    - `simulate_lifecycle(ride: Ride, duration_seconds: int) -> Iterator[RideEvent]`
    - Generates sequence: RIDE_REQUESTED → RIDE_ACCEPTED → RIDE_STARTED → RIDE_COMPLETED
    - Each event has realistic delay
    - Publishes events to Kafka via `event_publisher`
- `src/ridestream/application/generators.py`:
  - `LocationGenerator`:
    - Random valid (lat, lng) within city bounds (e.g., SF: 37.7°N, 122.4°W ± 0.05°)
  - `DistanceCalculator`:
    - Haversine distance between pickup/dropoff
  - `FareGenerator`:
    - Base fare + per-km rate + random surge multiplier

**Test Coverage:** 35+ unit tests
- Event sequence generation
- Location validity
- Distance calculation
- Fare calculation with surge

**Acceptance Criteria:**
- All tests pass: `make test`
- Can generate 1000 rides without error
- Ride lifecycle events are in correct temporal order
- Integration test publishes rides to Docker Kafka: `make test-integration`

**Post-Mortem Notes:**
- V1 Issue: No data generator; hard to test end-to-end flow
- V2 Fix: `RideSimulator` generates realistic event streams; testable locally

---

### T016: Stream Metrics (Spark Structured Streaming)
**Agent:** @coder  
**Size:** M (Medium)  
**Est. Time:** 45 min  
**Dependencies:** T002, T009, T014, T015

**Deliverables:**
- `src/ridestream/application/spark_metrics.py`:
  - `StreamMetricsProcessor`:
    - `__init__(spark_session, kafka_brokers: str, checkpoint_dir: str)`
    - `compute_5min_metrics(input_topic: str, output_topic: str)`
    - Reads from `ride.events.raw`
    - Groups by 5-minute tumbling window
    - Calculates:
      - `rides_completed_count: int`
      - `avg_fare: float`
      - `avg_distance: float`
      - `surge_multiplier: float` (max over 5 min)
    - Writes to `ride.metrics.5min` as JSON
    - Uses Spark Structured Streaming with Kafka source/sink
- `infrastructure/spark/` (Spark job scripts):
  - `metrics_job.py` (runnable script that creates SparkSession and calls processor)

**Test Coverage:** 30+ unit tests
- Windowed aggregation logic (mock Spark DataFrame)
- Metric calculations
- Kafka source/sink integration (local Spark, Docker Kafka)

**Acceptance Criteria:**
- All tests pass: `make test`
- Integration test runs Spark job against Docker Kafka: `make test-integration`
- Metrics are computed correctly and written to `ride.metrics.5min` topic
- Checkpoint directory is properly managed (restarts resume from last offset)

**Dependencies:**
- pyspark
- Kafka plugin for Spark (included in spark-submit config)

**Post-Mortem Notes:**
- V1 Issue: Spark relied on dbt-spark; never tested standalone
- V2 Fix: Spark job is independent; metrics feed dbt Silver layer (T017)

---

### T017: dbt Models (Silver Layer)
**Agent:** @coder  
**Size:** M (Medium)  
**Est. Time:** 35 min  
**Dependencies:** T004, T010, T016

**Deliverables:**
- `dbt_project/models/silver/stg_rides.sql`:
  - Reads from bronze `ride_events_raw` (MinIO S3 parquet via Hive)
  - Deduplicates events (ride_id + event_type + timestamp)
  - Casts types, validates ranges
  - Creates `silver.stg_rides` (one row per ride, with all events denormalized)
- `dbt_project/models/silver/stg_metrics.sql`:
  - Reads from `ride.metrics.5min` Kafka topic (via Hive connector)
  - Normalizes, validates, creates `silver.stg_metrics`
- `dbt_project/models/silver/_silver.yml`:
  - Column definitions, tests, documentation

**Test Coverage:** 20+ dbt tests
- `unique` on ride_id
- `not_null` on required fields
- `relationships` (ride_id exists in source)
- `dbt_utils.recency` (data freshness)

**Acceptance Criteria:**
- `dbt debug --target trino` passes
- `dbt compile` succeeds (all SQL valid)
- `dbt test --target trino` passes against Docker Trino + MinIO
- `make test-integration` runs dbt models against populated Bronze

**Post-Mortem Notes:**
- V1 Issue: dbt relied on Spark; Spark ↔ Hive ↔ dbt pipeline never validated
- V2 Fix: dbt-trino reads from MinIO/Hive; tested with real data in T023

---

### T018: dbt Models (Gold Layer)
**Agent:** @coder  
**Size:** M (Medium)  
**Est. Time:** 35 min  
**Dependencies:** T017

**Deliverables:**
- `dbt_project/models/gold/rides_summary.sql`:
  - Aggregates silver `stg_rides`: rides_completed, avg_fare, total_distance, etc.
  - Groups by day, driver_id, passenger_id
  - Creates `gold.rides_summary` (fact table)
- `dbt_project/models/gold/driver_metrics.sql`:
  - Driver KPIs: rides_completed, avg_rating, earnings
  - Creates `gold.driver_metrics` (dimension)
- `dbt_project/models/gold/passenger_metrics.sql`:
  - Passenger KPIs: rides_taken, avg_fare_paid, repeat_rate
  - Creates `gold.passenger_metrics` (dimension)
- `dbt_project/models/gold/_gold.yml`:
  - Column definitions, tests, documentation

**Test Coverage:** 15+ dbt tests
- Aggregate consistency (sum of components = total)
- No negative values (fares, distances)
- Date range validation

**Acceptance Criteria:**
- `dbt compile` succeeds
- `dbt test --target trino` passes
- `make test-integration` validates gold tables are queryable

**Post-Mortem Notes:**
- V1 Issue: Gold layer was ad-hoc Athena queries; no version control
- V2 Fix: dbt models are version-controlled, tested, documented

---

### T019: dbt Custom Tests
**Agent:** @coder  
**Size:** S (Small)  
**Est. Time:** 20 min  
**Dependencies:** T018

**Deliverables:**
- `dbt_project/tests/generic_surge_bounds.sql`:
  - Asserts surge_multiplier is in [1.0, 5.0]
- `dbt_project/tests/generic_distance_consistency.sql`:
  - Asserts calculated_distance ≈ haversine(pickup, dropoff) with 5% tolerance
- `dbt_project/tests/generic_fare_consistency.sql`:
  - Asserts fare ≥ base_fare
- `dbt_project/tests/singular_no_duplicate_rides.sql`:
  - SELECT * FROM gold.rides_summary WHERE ride_id IN (SELECT ride_id FROM gold.rides_summary GROUP BY ride_id HAVING COUNT(*) > 1)
  - Asserts no rows returned

**Acceptance Criteria:**
- All tests pass: `dbt test --target trino`
- Tests are reusable across multiple models (generic tests)

**Post-Mortem Notes:**
- V1 Issue: No data validation in dbt; only unit tests in Python
- V2 Fix: dbt tests validate data contracts at transformation boundaries

---

### T020: Pipeline Orchestrator (Airflow DAG)
**Agent:** @coder  
**Size:** M (Medium)  
**Est. Time:** 30 min  
**Dependencies:** T004, T009, T014, T015, T016, T018

**Deliverables:**
- `infrastructure/airflow/dags/ridestream_pipeline.py`:
  - DAG: `ridestream_v2_daily`
  - Tasks:
    1. `simulate_rides` (Python operator): runs `RideSimulator`, publishes 100 rides to Kafka
    2. `compute_metrics` (Spark operator): runs `spark_metrics.py`
    3. `wait_for_bronze_landing` (sensor): waits for S3 files in bronze/
    4. `dbt_compile` (dbt operator): `dbt compile --target trino`
    5. `dbt_silver` (dbt operator): `dbt run --selector silver --target trino`
    6. `dbt_gold` (dbt operator): `dbt run --selector gold --target trino`
    7. `dbt_test` (dbt operator): `dbt test --target trino`
    8. `export_to_athena` (Glue operator, stub): in prod, exports gold → Athena
  - Schedule: daily at 2 AM UTC
  - Retries: 2, with 5-minute delay
  - Catchups disabled (backfill manually)

**Acceptance Criteria:**
- DAG is syntactically valid: `airflow dags list` includes `ridestream_v2_daily`
- DAG can be triggered manually without error
- Integration test runs full DAG against Docker stack: `make test-integration`
- All tasks complete successfully in correct order

**Post-Mortem Notes:**
- V1 Issue: No orchestration; pipeline was a set of shell scripts
- V2 Fix: Airflow DAG with proper dependencies, monitoring, retry logic

---

## ITERATION 4: ENTRY POINTS + INTEGRATION

### T021: CLI Interface
**Agent:** @coder  
**Size:** M (Medium)  
**Est. Time:** 30 min  
**Dependencies:** T014, T015, T020

**Deliverables:**
- `src/ridestream/entry_points/cli.py`:
  - `@click.group()` main command: `ridestream`
  - Subcommands:
    - `ridestream simulate --num-rides=N --duration=D` — runs `RideSimulator` for N rides
    - `ridestream pipeline --task=TASK` — triggers Airflow task (e.g., `dbt_silver`)
    - `ridestream metrics --days=D` — queries Gold metrics for last D days
    - `ridestream config --env=ENV` — shows config for environment
  - Integrates `CloudProviderFactory` to select local vs AWS
- `scripts/run-simulator.sh` — wrapper for CLI simulator
- `scripts/run-pipeline.sh` — wrapper for CLI pipeline trigger

**Test Coverage:** 20+ unit tests
- CLI argument parsing
- Command routing
- Output formatting

**Acceptance Criteria:**
- `ridestream --help` shows all commands
- `ridestream simulate --num-rides=10` completes without error
- Integration test calls CLI commands: `make test-integration`

**Post-Mortem Notes:**
- V1 Issue: No CLI; hardcoded entry points
- V2 Fix: `click` CLI with pluggable commands

---

### T022: Step Functions ASL (Optional, AWS-Specific)
**Agent:** @infra  
**Size:** M (Medium)  
**Est. Time:** 30 min  
**Dependencies:** T001, T003, T020

**Deliverables:**
- `infrastructure/cloudformation/step_functions.asl.json` (Amazon States Language):
  - State machine for production pipeline:
    - Start → Simulate Rides (Lambda) → Compute Metrics (Glue) → dbt Silver (Glue) → dbt Gold (Glue) → dbt Test (Glue) → End
  - Error handling: failed tasks → SNS notification → manual review
  - Parallelizes independent dbt models (if applicable)
- CloudFormation integration in `infrastructure/cloudformation/orchestration.yml`

**Acceptance Criteria:**
- ASL is valid: `aws stepfunctions validate-state-machine --definition file://step_functions.asl.json`
- State machine can be created in CloudFormation
- Manual test execution completes (requires AWS account)

**Post-Mortem Notes:**
- V1 Issue: No cloud-native orchestration; relied on Airflow (self-managed)
- V2 Fix: Step Functions for prod; Airflow for local dev

---

### T023: Integration Tests Against Docker Stack (CRITICAL NEW TASK)
**Agent:** @tester  
**Size:** M (Medium)  
**Est. Time:** 45 min  
**Dependencies:** T002, T009–T012, T015–T019

**Deliverables:**
- `tests/integration/conftest.py`:
  - pytest fixtures for Docker stack setup/teardown
  - `docker_stack` fixture: runs `make docker-up`, yields, then `make docker-down`
  - Fixtures for Kafka producers/consumers, S3 clients, Trino/Hive connections
- `tests/integration/test_simulator_to_kafka.py`:
  - Start simulator, verify events land in Kafka topic
  - Assert ride_events_raw has N events
  - Assert ride_events_dlq is empty (no errors)
- `tests/integration/test_kafka_to_s3.py`:
  - Publish events to Kafka
  - Consume and write to S3 (via adapter)
  - Assert Parquet files exist and are readable
- `tests/integration/test_s3_to_catalog.py`:
  - Register S3 Parquet as Hive table (via Catalog adapter)
  - Query via Hive Metastore
  - Assert schema matches expected
- `tests/integration/test_trino_query.py`:
  - Write sample data to MinIO
  - Register as Hive table
  - Query via Trino
  - Assert results are correct
- `tests/integration/test_dbt_silver_to_gold.py`:
  - Populate bronze (via simulator → Kafka → S3)
  - Run `dbt run --target trino` (silver layer)
  - Run `dbt run --target trino` (gold layer)
  - Run `dbt test --target trino`
  - Assert test results are all passing
  - Assert gold.rides_summary has correct row count and metrics
- `tests/integration/test_end_to_end.py`:
  - Full pipeline: Simulator → Kafka → MinIO → Hive → Trino → dbt Silver → dbt Gold
  - Assert each step succeeds
  - Assert final metrics are consistent

**Test Execution:**
- Run via `make test-integration` (target in Makefile)
- Requires Docker stack running (`make docker-up`)
- Timeout: 10 minutes (generous for all containers to stabilize)

**Acceptance Criteria:**
- All integration tests pass: `make test-integration`
- Each test is independent (can run in any order)
- Cleanup happens (containers are stopped and removed)
- Tests are documented with clear assertions

**Post-Mortem Notes:**
- V1 Issue: Infrastructure was never validated end-to-end; acceptance criteria only mocked services
- V2 Fix: THIS TASK is the fix — integration tests exercise full pipeline against real Docker services
- Without this task, v2 would repeat v1's mistake: code passes unit tests but fails in production

---

### T024: E2E Tests (with AWS Services)
**Agent:** @tester  
**Size:** M (Medium)  
**Est. Time:** 30 min  
**Dependencies:** T003, T023

**Deliverables:**
- `tests/e2e/conftest.py`:
  - AWS fixtures (boto3 clients for Glue, Athena, S3)
  - Stack provisioning/teardown via CloudFormation (in **staging** AWS account only)
  - Skip if `AWS_ACCOUNT_ID` not set (local dev)
- `tests/e2e/test_aws_pipeline.py`:
  - Provision CloudFormation stack
  - Run simulator → Kafka (MSK) → Glue crawler → Athena query
  - Assert query results match expected
  - Clean up stack
  - Mark as `@pytest.mark.aws` (only runs in CI/CD with AWS credentials)

**Acceptance Criteria:**
- E2E tests skip gracefully if AWS credentials absent (local dev)
- CI/CD pipeline runs E2E tests before deploying to prod
- Tests are isolated (separate AWS account for staging)

**Post-Mortem Notes:**
- V1 Issue: No staging/prod testing; code deployed directly
- V2 Fix: E2E tests in staging before prod promotion

---

## ITERATION 5: POLISH

### T025: CodePipeline/CodeBuild CI/CD
**Agent:** @infra  
**Size:** M (Medium)  
**Est. Time:** 45 min  
**Dependencies:** T001, T003, T021

**Deliverables:**
- `buildspec.yml` (CodeBuild configuration):
  ```yaml
  version: 0.2
  phases:
    pre_build:
      commands:
        - echo "Installing dependencies..."
        - pip install -e . -q
        - pip install -r requirements-dev.txt -q
    build:
      commands:
        - echo "=== LINT ==="
        - make lint
        - echo "=== TYPECHECK ==="
        - make typecheck
        - echo "=== SECURITY ==="
        - make security-check
        - echo "=== UNIT TESTS ==="
        - make test
        - echo "=== SMOKE TESTS (Docker) ==="
        - docker --version && docker-compose --version
        - make docker-up
        - make smoke-test
        - make docker-down
        - echo "=== INTEGRATION TESTS ==="
        - make docker-up
        - make test-integration
        - make docker-down
        - echo "=== VALIDATE CLOUDFORMATION ==="
        - make cfn-validate
  artifacts:
    files:
      - '**/*'
    name: ridestream-build
  ```
- CloudFormation stack for CodePipeline (in `infrastructure/cloudformation/codepipeline.yml`):
  - Source: CodeCommit repo (or GitHub via OAuth)
  - Build: CodeBuild project (runs buildspec.yml)
  - Deploy: CloudFormation change sets to dev, staging, prod
  - Approval gates between staging and prod
- `scripts/setup-pipeline.sh` (creates CodePipeline stack)
- `Makefile` targets:
  - `make lint` (black, flake8, isort)
  - `make typecheck` (mypy)
  - `make security-check` (bandit, safety)
  - `make test` (pytest unit tests)
  - `make test-integration` (pytest integration tests)
  - `make smoke-test` (Docker smoke test)
  - `make docker-up` / `make docker-down`
  - `make cfn-validate` (CloudFormation validation)

**Acceptance Criteria:**
- `buildspec.yml` is valid (CodeBuild can parse it)
- CodePipeline stack creates without error
- Manual pipeline trigger completes all stages
- All gates (lint, test, smoke) must pass to proceed

**Post-Mortem Notes:**
- V1 Issue: No CI/CD; testing was manual
- V2 Fix: CodePipeline ensures every commit is tested, validated, and deployable

---

### T026: Documentation
**Agent:** @coder  
**Size:** M (Medium)  
**Est. Time:** 30 min  
**Dependencies:** All previous tasks

**Deliverables:**
- `README.md`:
  - Overview, architecture diagram, quick-start (local Docker)
  - Running tests: unit, integration, E2E
  - Deployment: dev, staging, prod
- `ARCHITECTURE.md`:
  - Domain-driven design principles
  - Port/adapter pattern
  - Data flow (Kafka → S3 → Hive → Trino → dbt → Gold)
  - Dual-target strategy (local Docker + AWS cloud)
- `AWS_SETUP.md`:
  - Prerequisites (AWS account, IAM user, CLI)
  - CloudFormation deployment steps
  - Cost estimation
  - Manual validation checklist
- `COST_ANALYSIS.md`:
  - Breakdown by service (MSK, Glue, Athena, S3, Lambda)
  - Hourly + monthly estimates
  - Cost optimization tips
- `TASK_BREAKDOWN.md` (this file, as reference for developers)
- `DOCKER_SERVICES.md`:
  - Each service: purpose, ports, healthcheck
  - How to troubleshoot (logs, connectivity)
- `TROUBLESHOOTING.md`:
  - Common errors and fixes
  - Debugging checklist
  - Support contacts

**Acceptance Criteria:**
- All .md files are syntactically valid Markdown
- README links to other docs
- Architecture diagram is included (ASCII or PNG)
- Setup instructions are tested (one person follows them without prior context)

**Post-Mortem Notes:**
- V1 Issue: No documentation; onboarding was painful
- V2 Fix: Comprehensive docs; new contributors can get running in 30 minutes

---

### T027: Interview Prep
**Agent:** @coder  
**Size:** S (Small)  
**Est. Time:** 20 min  
**Dependencies:** All tasks complete

**Deliverables:**
- `INTERVIEW_PREP.md`:
  - Key design decisions and rationale
  - Trade-offs (local Docker vs cloud, dbt-trino vs dbt-spark)
  - Lessons learned from v1
  - Sample interview questions and answers
  - Metrics: test coverage, deployment frequency, mean time to recovery (MTTR)
- `METRICS.md`:
  - Lines of code: domain (300), adapters (800), application (500), tests (2000+)
  - Test coverage: 85%+ (unit), 70%+ (integration)
  - Build time: < 2 min (lint + test), < 5 min (with Docker)
  - Deployment frequency: hourly (via CodePipeline)

**Acceptance Criteria:**
- Interview prep doc is comprehensive and honest
- Metrics are accurate (measured from actual codebase)

**Post-Mortem Notes:**
- V1 Issue: Couldn't articulate design decisions in interviews
- V2 Fix: Document trade-offs and lessons learned upfront

---

## DEPENDENCY GRAPH

```
T001 (Scaffold) → T002 (Docker) → T003 (CloudFormation)
                       ↓
T004 (dbt Scaffold) ←──┘

T001 → T005 (Entities) → T006 (Services) → T007 (Ports) → T008 (Config)
                                   ↓
                            T009 (Kafka)
                            T010 (S3)
                            T011 (Hive)
                            T012 (Trino)
                            T013 (Quality)
                            T014 (Factory)
                                   ↓
                            T015 (Simulator)
                            T016 (Spark)
                            T017 (dbt Silver) → T018 (dbt Gold) → T019 (dbt Tests)
                                                                         ↓
                                                            T020 (Orchestrator)
                                                                    ↓
                                                T021 (CLI) + T022 (Step Functions)
                                                                    ↓
                                        T023 (Integration Tests, CRITICAL)
                                                    ↓
                                        T024 (E2E Tests)
                                                    ↓
                        T025 (CodePipeline) ← T026 (Docs) ← T027 (Interview Prep)
```

---

## EFFORT SUMMARY

| Iteration | Tasks | Est. Time | Focus |
|-----------|-------|-----------|-------|
| 0 | T001–T004 | 195 min | Infrastructure + foundations |
| 1 | T005–T008 | 95 min | Domain layer |
| 2 | T009–T014 | 170 min | Adapters (Kafka, S3, Hive, Trino) |
| 3 | T015–T020 | 185 min | Application (Simulator, Spark, dbt, Airflow) |
| 4 | T021–T024 | 105 min | Entry points + integration + E2E |
| 5 | T025–T027 | 95 min | CI/CD + documentation |
| **TOTAL** | **27** | **845 min** | ~14 hours (with Docker overhead) |

---

## SUCCESS CRITERIA (V2 vs V1)

| Criterion | V1 | V2 |
|-----------|----|----|
| Infrastructure validated end-to-end? | ❌ Moto only | ✅ Docker smoke test |
| Integration tests with real services? | ❌ None | ✅ T023 (required) |
| Dual-target (local + AWS)? | ❌ Hardcoded | ✅ Factory pattern |
| CI/CD pipeline? | ❌ None | ✅ CodePipeline |
| dbt tested against real warehouse? | ❌ Never | ✅ T023 + T024 |
| Kafka tested in isolation? | ❌ Never | ✅ T009 + T023 |
| Documentation? | ❌ Minimal | ✅ Comprehensive |

---

## KEY LESSONS BAKED INTO V2

1. **T002 (Docker Compose) is T001 for feature work.** Infrastructure is deployed before code. Every commit that changes infrastructure has a smoke test.

2. **T023 (Integration Tests) is not optional.** Unit tests passed in v1 because moto is permissive. Real services expose configuration mistakes, timeout issues, and data contract violations that mocks hide.

3. **Image tags are verified at spec time, not deploy time.** Before using `trinodb/trino:latest`, verify tag exists: `docker manifest inspect trinodb/trino:latest`. Document all versions in `docker/IMAGE_VERSIONS.md`.

4. **dbt-trino is the canonical local target.** Spark is flaky for local testing; Trino is simpler and more aligned with the Athena target in production.

5. **Hive Metastore is a first-class service.** Custom Dockerfile (T002) with S3A config. Not "eventually consistent"; it's a critical data contract between Spark, dbt, and Trino.

6. **Kafka init container (T002) must be in docker-compose.** Polling for topic existence is fragile. kafka-init container waits for Kafka, creates topics, then exits. Guarantees topics exist before adapters run.

7. **dbt profiles are environment-aware (T004).** Two profiles: `trino` (local) and `athena` (prod). Switch via `--target` or `--profiles-dir`. No hardcoded endpoints.

8. **Acceptance criteria include smoke tests, not just unit tests (T002, T023).** Smoke test proves infrastructure works. Integration test proves data flows end-to-end. Together they prevent "works on my laptop, fails in prod."

---

## NEXT STEPS (AFTER COMPLETION)

1. **Run all tasks in order**, respecting dependencies.
2. **After T023, verify end-to-end with manual smoke test:**
   ```bash
   make docker-up
   make smoke-test
   ridestream simulate --num-rides=100
   # Monitor Kafka, MinIO, Trino, dbt logs
   make test-integration
   make docker-down
   ```
3. **After T025, set up CodePipeline** and run first automated build.
4. **After T026, onboard new team member** and validate docs are sufficient.
5. **Move to production** with Step Functions (T022) and E2E tests (T024).

---

**Document Version:** 1.0  
**Last Updated:** 2026-04-10  
**Owner:** @coder (Domain Design), @infra (Infrastructure), @tester (Quality Assurance)
