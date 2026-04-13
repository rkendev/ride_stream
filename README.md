# RideStream v2

Real-time ride-sharing event pipeline built with hexagonal architecture, Kafka,
S3/MinIO, Hive, Trino, dbt, and Spark Structured Streaming.

[![Tests](https://img.shields.io/badge/tests-305%20passing-brightgreen)]()
[![Coverage](https://img.shields.io/badge/coverage-measured-blue)]()
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)]()

## Quick Start

```bash
# 1. Clone and enter project
cd ride_stream_v2

# 2. Install Python 3.12 dependencies
make setup

# 3. Start Docker stack (10 services)
make docker-up

# 4. Verify everything is running
make smoke-test

# 5. Run full test suite
make test                   # unit tests, lint, type-check, security
make test-integration       # integration tests (requires Docker)
```

## Architecture at a Glance

```
┌─────────────────────────────────────────────────────────────────┐
│  RideStream v2 — Hexagonal + Medallion Architecture             │
└─────────────────────────────────────────────────────────────────┘

  Simulator ──> Kafka ──> MinIO (bronze) ──> Hive Catalog
                                                  │
                                                  ▼
                                              Trino/Athena
                                                  │
                                                  ▼
                                    dbt Silver ──> dbt Gold
                                                  │
                                                  ▼
                                          Analytics / BI
```

See [ARCHITECTURE.md](ARCHITECTURE.md) for full details.

## Project Structure

```
ride_stream_v2/
├── src/ridestream/
│   ├── domain/              # Frozen Pydantic models, pure business logic
│   ├── ports/               # Abstract interfaces (ABCs)
│   ├── adapters/
│   │   ├── local/           # Docker adapters (Kafka, MinIO, Trino, Hive)
│   │   └── aws/             # AWS adapters (MSK, S3, Athena, Glue)
│   ├── application/         # Orchestration layer (simulator, metrics)
│   └── entry_points/cli.py  # Click-based CLI
│
├── dbt/
│   ├── models/
│   │   ├── staging/         # Bronze sources
│   │   ├── silver/          # Cleaned + deduplicated
│   │   └── gold/            # Aggregated + analytics-ready
│   └── tests/               # Custom dbt data tests
│
├── docker/
│   ├── docker-compose.yml   # 10-service stack
│   ├── hive/Dockerfile      # Custom Hive Metastore (ADR-012)
│   └── airflow/Dockerfile   # Airflow 2.9.3-python3.12 (§10.2)
│
├── infrastructure/
│   ├── cloudformation/      # 10 stacks + root
│   ├── airflow/dags/        # Pipeline orchestration DAGs
│   └── spark/               # spark-submit jobs
│
├── tests/
│   ├── unit/                # 284 unit tests (domain + adapters + application)
│   ├── integration/         # 21 tests against real Docker containers
│   └── e2e/                 # AWS-only tests (skip without credentials)
│
└── scripts/                 # smoke-test.sh, setup.sh, verify-*.sh
```

## CLI Usage

```bash
# Generate simulated rides
ridestream simulate --num-rides 100

# Trigger pipeline task
ridestream pipeline --task silver

# Query Gold metrics
ridestream metrics --days 7

# Show current config
ridestream config --env local
```

## Running Tests

| Command | What it does |
|---------|--------------|
| `make test` | Lint + type-check + security + unit tests |
| `make test-unit` | Just unit tests with coverage |
| `make test-integration` | Integration tests against Docker stack |
| `make smoke-test` | Health checks (Kafka, MinIO, Trino, Hive, Spark, Airflow) |
| `cd dbt && dbt test` | dbt data quality tests |

## Deployment

1. **Local Docker**: `make docker-up` — 10 services run locally
2. **Staging**: `bash scripts/setup-pipeline.sh staging` — CloudFormation stack
3. **Production**: `bash scripts/setup-pipeline.sh prod` — manual approval gate
4. **Smoke test after every deploy**: `make smoke-test` (Rule #9)

See [AWS_SETUP.md](AWS_SETUP.md) for the full deployment procedure.

## Documentation

- [ARCHITECTURE.md](ARCHITECTURE.md) — Domain model, ports/adapters, data flow
- [AWS_SETUP.md](AWS_SETUP.md) — CloudFormation stacks, cost estimates
- [DOCKER_SERVICES.md](DOCKER_SERVICES.md) — Service-by-service reference
- [INTERVIEW_PREP.md](INTERVIEW_PREP.md) — Design decisions, trade-offs
- [METRICS.md](METRICS.md) — Codebase metrics and test counts
- [TECHNICAL_PLAN.md](TECHNICAL_PLAN.md) — Original spec with gotchas

## Key Design Decisions

| Decision | Rationale | ADR |
|----------|-----------|-----|
| Hexagonal architecture | Domain isolated from infra, dual-target adapters | — |
| dbt-trino local, dbt-athena prod | Thrift Server was unreliable in v1 | ADR-004 |
| CloudFormation over Terraform | Native AWS, no state file mgmt | ADR-006 |
| CodePipeline over GitHub Actions | VPC access to MSK/S3 | ADR-010 |
| Custom Hive Metastore Dockerfile | hadoop-aws JARs not bundled | ADR-012 |
| Smoke tests as CI/CD gate | v1 passed mocks, failed in prod | ADR-011 |

## Contributing

- All code must pass `make test` before merge
- Integration tests must exercise real Docker containers (no mocks)
- All SQL lives in dbt — zero f-string SQL in Python
- Follow port/adapter pattern: domain depends on ports, never adapters
- See `.claude/rules/` for layer-specific coding rules

## License

Apache 2.0
