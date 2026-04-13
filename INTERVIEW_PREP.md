# Interview Prep — RideStream v2

Design decisions, trade-offs, and sample Q&A for technical interviews.

## Project Summary

RideStream v2 is a real-time ride-sharing event pipeline built on three patterns:

1. **Hexagonal architecture** — Domain logic isolated from infrastructure.
2. **Medallion data architecture** — Bronze (raw) → Silver (cleaned) → Gold (aggregated).
3. **Dual-target adapters** — Every external dependency has a local Docker and an AWS cloud implementation.

**Scale**: 2,861 lines of Python, 305 tests passing (284 unit + 21 integration),
29 dbt data tests, 92.74% code coverage, 10 Docker services, 10 CloudFormation stacks.

## Key Design Decisions

### 1. Why Hexagonal Architecture for a Data Pipeline?

Traditional data pipelines are wired directly against Kafka, S3, and SQL engines.
This makes them:

- Hard to test (requires real infrastructure)
- Hard to port (Kafka client → MSK client = code changes)
- Hard to reason about (business logic scattered across adapters)

Hexagonal flips this: domain logic is pure Python, depends only on abstract
ports. Adapters implement those ports for specific infrastructure.

**Benefits**:
- Domain layer has 100% test coverage with no Docker needed
- Same application code runs against local Docker or AWS
- New adapters (e.g., Azure Event Hubs) can be added without touching domain

**Cost**: ~10% extra code for abstractions. Worth it.

### 2. Why dbt-trino Local + dbt-athena Prod (Not dbt-spark)?

In v1, dbt-spark relied on Spark Thrift Server, which had:
- Connection pooling bugs
- Session timeouts
- Intermittent "connection refused" errors

The fix: use dbt-trino locally (Starburst-maintained, battle-tested) and
dbt-athena in production. Both target SQL engines — not Spark DataFrames —
so models are portable.

This decision removed an entire class of flaky tests.

### 3. Why CloudFormation Over Terraform?

- **Native AWS**: No state file management, no locking issues
- **Nested stacks**: Modular updates without full re-deploys
- **Change sets**: Review before applying (prevents accidental damage)
- **Drift detection**: Catches manual console changes

Terraform is better for multi-cloud, but we're AWS-only. CloudFormation's
tighter integration wins here.

### 4. Why CodePipeline Over GitHub Actions?

CodePipeline runs in our VPC. That means CodeBuild agents can:
- Access MSK (which is in a private subnet)
- Access RDS (no public endpoint)
- Pull from internal ECR repos without IAM gymnastics

GitHub Actions runners sit outside our VPC. Giving them access would
require opening holes in security groups or running self-hosted runners.

### 5. Why a Custom Hive Metastore Dockerfile?

`apache/hive:4.0.0` does not include the `hadoop-aws` and `aws-java-sdk`
JARs needed for S3A filesystem support. Without them, any write to
`s3a://bronze/...` fails at runtime with:

```
java.io.IOException: No FileSystem for scheme s3a
```

V1 discovered this at runtime, mid-pipeline. V2 bakes the JARs into a
custom Dockerfile, documented as ADR-012. Smoke test verifies S3A
connectivity before any code runs.

### 6. Why Smoke Tests as a CI/CD Gate?

V1's acceptance criteria were "unit tests pass." They did. But the pipeline
still failed in staging because:
- Hive Metastore couldn't find hadoop-aws JARs
- Kafka topics didn't exist (auto-create disabled)
- Trino catalog config was missing

Unit tests with moto mocks don't validate any of that. Smoke tests, which
run against real Docker containers, do. V2 requires them to pass in CI.

## Trade-offs

### Docker Local vs AWS Cloud

| Dimension | Docker Local | AWS Cloud |
|-----------|--------------|-----------|
| Setup cost | 1 `make docker-up` | CloudFormation deploy (20+ min) |
| Runtime cost | Free | ~$170/month (dev) |
| Fidelity | High (same protocols) | 100% |
| Iteration speed | Seconds | Minutes |
| Resource footprint | ~10 GB RAM | Unbounded |

**Strategy**: Develop local, validate in staging, deploy to prod.
Integration tests run against local Docker. E2E tests run against
staging AWS.

### Kafka vs Kinesis

Chose MSK (Managed Kafka) over Kinesis because:
- **API portability**: Same `kafka-python` client works local and cloud
- **Ecosystem**: Connect, Streams, Schema Registry all mature
- **Cost predictability**: Per-broker pricing, not per-shard

Downside: MSK costs ~$108/month for 3 brokers vs. Kinesis's pay-per-use.
For bursty workloads <1GB/month, Kinesis is cheaper.

### dbt Incremental vs Full Refresh

Gold models (`rides_summary`, `driver_metrics`) use `materialized='table'`
with nightly full refresh. This is slower than incremental but:
- Simpler to reason about
- Handles late-arriving data correctly
- Idempotent re-runs

For 100M+ row tables, we'd switch to incremental with `unique_key`.
At current scale (~1M rides/day), full refresh runs in seconds.

### Synchronous vs Async Application Services

All application services (RideSimulator, Spark metrics) are synchronous.
Async would give better throughput for the simulator, but:
- Simpler debugging
- Easier testing (no event loop management)
- Single-threaded by design (dedicated worker containers)

If we needed 10K events/sec from a single process, we'd add asyncio.
Current target is 1K events/sec, achievable synchronously.

## Lessons Learned (v1 → v2)

| # | v1 Mistake | v2 Fix |
|---|------------|--------|
| 1 | Deployed at task 20/26 (infrastructure last) | Iteration 0 is infrastructure-first |
| 2 | Built against AWS only, debugged on VPS | Dual-target adapters from day 1 |
| 3 | f-string SQL (3 injection risks found pre-deploy) | All SQL in dbt, zero f-strings |
| 4 | Untested dbt models | Every model has tests; CI halts on failure |
| 5 | No version pinning (local/CI drift) | `verify-versions.sh` enforces parity |
| 6 | Bare `except Exception:` everywhere | Specific catches only; error mapping |
| 7 | Hardcoded passwords in `.env` | AWS Secrets Manager |
| 8 | No smoke tests (broke mid-pipeline) | Smoke tests after every infra change |
| 9 | Used `python:latest` in Dockerfile | Pinned tags, verified with `docker manifest inspect` |
| 10 | Integration tests were moto-only | Real Docker stack exercised |
| 11 | No orchestration (shell scripts) | Airflow DAG + Step Functions |
| 12 | Spark Thrift Server was flaky | dbt-trino for local, dbt-athena for cloud |

## Sample Interview Q&A

### Q1: How would you explain hexagonal architecture to a non-engineer?

Imagine a coffee shop. The **core business** is making coffee — that's the
domain. The **ports** are things like "a way to receive orders" and
"a way to receive payment." The **adapters** are concrete implementations:
one adapter takes orders via the counter, another via mobile app. One
adapter takes payment via cash, another via card.

The core coffee-making logic doesn't care which adapter is used. Swap
the mobile app for a drive-through window (new adapter) without retraining
baristas (domain unchanged).

### Q2: How do you ensure data quality?

Four layers:
1. **Schema validation at Kafka boundary**: `SchemaValidator` rejects malformed events.
2. **Pydantic models**: Immutable with field validators (e.g., `latitude: float = Field(ge=-90, le=90)`).
3. **dbt tests**: 29 tests on silver/gold tables (unique, not_null, custom).
4. **Quality gates at adapter boundaries**: `DataQualityChecker`, `DuplicateDetector`.

Failures short-circuit fast with specific exceptions, not silent data corruption.

### Q3: How do you handle late-arriving data?

Spark Structured Streaming uses watermarks. Our window aggregation:

```python
events_with_data
    .withWatermark("timestamp", "10 minutes")
    .groupBy(F.window("timestamp", "5 minutes"))
```

This tolerates up to 10 minutes of lateness. Events arriving later than
that are dropped from windowed aggregates but still land in bronze.
dbt gold models re-aggregate nightly to catch any stragglers.

### Q4: What happens if Kafka is down when the simulator runs?

`KafkaEventPublisher.publish()` retries up to 3 times with exponential
backoff (2s, 4s, 8s). If all retries fail:
1. Event is routed to DLQ topic `ride-events-dlq`
2. `EventBusError` is raised
3. Application can decide: abort, log-and-continue, or re-queue

The DLQ pattern ensures no data is silently dropped.

### Q5: How do you test the AWS adapter without an AWS account?

Three approaches:
1. **Unit tests** with `moto`: mocks S3, Glue, Athena at the boto3 level. Fast.
2. **Integration tests** with MinIO (S3-compatible): same boto3 client,
   different endpoint URL. Proves the adapter works end-to-end.
3. **E2E tests** (skipped without `AWS_ACCOUNT_ID`): run against real AWS
   in staging before prod promotion.

This matches the real deployment ladder: local → staging → prod.

### Q6: How would you scale this to 100K events/sec?

1. **Kafka**: Increase partitions on `ride-events` to 100+. Add more brokers.
2. **Simulator**: Batch produces (not one at a time). Use async producer.
3. **Firehose**: Already handles this — buffer size auto-scales.
4. **Spark**: More executors, smaller micro-batches. Dynamic allocation.
5. **Trino**: Scale coordinator memory; add workers.
6. **dbt**: Switch to incremental materialization with `unique_key`.

The bottleneck would likely be Hive Metastore (single Derby DB) — swap
to Postgres-backed metastore (already in AWS RDS for prod).

### Q7: Why Pydantic with `frozen=True`?

Immutability by default. Without `frozen=True`, you can do:

```python
ride.status = RideStatus.COMPLETED  # allowed, bypasses state machine
```

With `frozen=True`:

```python
ride.status = RideStatus.COMPLETED  # ValidationError
new_ride = ride.complete(distance, fare)  # only way to change state
```

This makes the entire domain layer thread-safe and reasoning-friendly.
Cost: `.model_copy(update={...})` is slightly slower than mutation.

### Q8: What's the dev experience like?

```bash
git clone ... && cd ride_stream_v2
make setup         # installs Python 3.12 deps in .venv
make docker-up     # starts 10 services (~90s)
make smoke-test    # verifies everything is healthy
make test          # lint + type-check + security + 284 unit tests (~15s)
```

Total: 2-3 minutes from clone to "ready to code." All subsequent commands
are <30s (unit tests) or <30s (integration tests).

### Q9: What's your CI/CD strategy?

CodeBuild runs `buildspec.yml`:

1. **install**: `pip install -e .[dev]`
2. **pre_build**: `verify-versions.sh`, CloudFormation validation, Docker image tag verification
3. **build**: lint → type-check → security → unit tests → dbt parse → integration tests (if privileged mode)
4. **post_build**: coverage report

Three environments: dev (auto-deploy on merge), staging (auto-deploy with smoke tests),
prod (manual approval gate). Rollback via CloudFormation change sets.

### Q10: If you had more time, what would you add?

1. **Observability**: Prometheus metrics from the simulator, Grafana dashboards
2. **Schema registry**: Confluent Schema Registry for Avro events (currently JSON)
3. **Feature store**: Feast integration for ML feature serving
4. **Chaos engineering**: Litmus or Chaos Mesh to simulate broker failures
5. **Multi-region**: MSK MirrorMaker 2 for DR
6. **Fine-grained IAM**: Per-role policies instead of broad least-privilege buckets

## Quick Facts to Remember

- **Start date**: 2026-04-10
- **Completion date**: 2026-04-12
- **Total tasks**: 27 (T001-T027)
- **Iterations**: 5
- **Test count**: 339 (284 unit + 21 integration + 5 e2e + 29 dbt)
- **Coverage**: 92.74%
- **Docker services**: 10 (+ 2 init)
- **CloudFormation stacks**: 10 (+ root + ASL)
- **Gotchas from v1**: 20+ (see TECHNICAL_PLAN §10)
- **Lines of Python**: 2,861 source + 3,229 tests
- **Lines of dbt**: 1,047
- **Lines of CloudFormation**: 935
- **Total project**: ~14K lines (code + tests + docs + infra)

## References

- [ARCHITECTURE.md](ARCHITECTURE.md) — Full architecture
- [METRICS.md](METRICS.md) — Measured codebase stats
- [TECHNICAL_PLAN.md](TECHNICAL_PLAN.md) — Original spec with 12 ADRs and 20+ gotchas
