# RideStream v2 Project Memory

## Project Identity
**RideStream v2**: Real-time ride-sharing pipeline. Complete restart from v1, applying DDD + SDD + lessons learned from StockStream (363 tests, 8K lines, proven patterns).

## Architecture
- **Pattern**: Hexagonal (ports/adapters) + Medallion (bronze/silver/gold dbt layers)
- **Domain**: Frozen Pydantic models, static services, zero external imports (except pydantic/datetime/hashlib/math)
- **Adapters**: Dual-target for every external dependency (local mock + AWS real)
- **Data**: dbt transforms (Trino local, Spark production), no f-string SQL
- **API**: FastAPI, structured logging, timeout/retry on all external calls

## Key Commands
```bash
make setup              # Initialize local env (pip, dbt, Docker)
make all               # Full build (lint, type check, tests, dbt build)
make smoke-test        # Health checks (API, dbt, DB)
make docker-up         # Start full stack (Postgres, Redis, dbt runner)
make docker-down       # Tear down stack
make test-integration  # Full integration suite (Docker required)
make ruff              # Format + lint (zero errors required)
make mypy              # Type check --strict
make pytest            # Run all tests
```

## Top Gotchas from v1 (Critical)
1. **Smoke tests missing**: Infrastructure broke mid-pipeline. ALWAYS run `make smoke-test` after infra changes.
2. **f-String SQL**: 3 injection risks found pre-deploy. ALL SQL lives in dbt, zero f-strings in src/.
3. **Bare exceptions**: 5+ `except Exception` blocks masked real bugs. Always catch specific exceptions.
4. **Docker image tags**: Used `python:latest`, broke on VPS. VERIFY tags with `docker manifest inspect` before build.
5. **Integration tests mocked too much**: Missed real Postgres/dbt issues. Tests MUST exercise Docker stack.

## Coding Conventions
- **Pydantic models**: `frozen=True`, no setattr after instantiation
- **Services**: Static methods only, dependency injection via ports
- **SQL**: All in dbt (ref(), source(), Jinja macros). Zero f-strings or query builders in Python.
- **Exceptions**: Specific catches only (psycopg2.DatabaseError, TimeoutError, etc.), never `except Exception`
- **Adapters**: Both local (moto, testcontainers) and AWS (boto3, psycopg2) from day 1
- **Testing**: Unit (100% domain coverage), integration (full Docker stack), E2E (API + dbt + DB)

## Session State
- Working directory: `/home/ridestream/`
- Artifacts: `.logs/`, `artifacts/` (dbt state, compiled models)
- Secrets: AWS Secrets Manager only (no local .env with credentials)
- Git: Commits after each gate (Phase 1, 2, 3, 4)
