# Tooling Rules

## Core Principle
Version pinning is identical across all tooling files. No surprises between local and AWS CodeBuild. All tooling must run within CodeBuild's 30-minute timeout window.

## Rule 5: PYPROJECT.TOML FROM TECHNICAL_PLAN
Copy exact dependencies from verified sources. All changes go through architecture review. No ad-hoc `uv pip install`.

```toml
# pyproject.toml (locked versions)
[project]
name = "ridestream"
version = "2.0.0"
description = "Real-time ride-sharing data pipeline"
requires-python = ">=3.12"
dependencies = [
    "pydantic>=2.0",
    "pydantic-settings>=2.0",
    "kafka-python>=2.0.2",
    "boto3>=1.28.0",
    "minio>=7.2.0",
    "dbt-core>=1.7.0",
    "dbt-trino>=1.7.0",
    "sqlalchemy>=2.0",
    "python-dateutil>=2.8.2",
    "pytz>=2024.1",
    "loguru",
    "python-json-logger",
]

[project.optional-dependencies]
dev = [
    "pytest==8.0.0",
    "pytest-asyncio==0.24.0",
    "pytest-cov==5.0.0",
    "pytest-mock==3.14.0",
    "ruff==0.8.0",
    "mypy==1.13.0",
    "bandit==1.8.0",
    "black==24.10.0",
    "testcontainers==4.7.0",
    "moto==5.0.0",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
addopts = "--cov=src --cov-report=term-missing --cov-fail-under=85"

[tool.mypy]
strict = true
warn_return_any = true
warn_unused_configs = true
python_version = "3.12"

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "W", "I", "UP", "B", "PIE"]
ignore = ["E501"]

[tool.coverage.run]
branch = true
source = ["src"]

[tool.coverage.report]
exclude_lines = [
    "pragma: no cover",
    "def __repr__",
    "raise AssertionError",
    "raise NotImplementedError",
    "if __name__ == .__main__.:",
]
```

## Rule 6: NO ASYNC FRAMEWORKS
RideStream v2 uses batch/event-driven architecture, NOT FastAPI or async HTTP servers:
- Kafka consumers run in subprocess pools (kafka-python)
- dbt orchestration via scheduled jobs (AWS EventBridge)
- Data transformations are synchronous SQL (dbt + Trino)
- No uvicorn, no async web server required

If you need HTTP endpoints, use a minimal WSGI server (Gunicorn + Flask), but this is NOT the primary interface.

## Pre-commit Hooks
Enforce rules locally before commit.

```yaml
# .pre-commit-config.yaml (MUST match pyproject.toml versions)
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.8.0  # MUST match ruff==0.8.0 in pyproject.toml
    hooks:
      - id: ruff
        args: [--fix]
      - id: ruff-format

  - repo: https://github.com/pre-commit/mirrors-mypy
    rev: v1.13.0  # MUST match mypy==1.13.0 in pyproject.toml
    hooks:
      - id: mypy
        additional_dependencies:
          - pydantic>=2.0
          - types-python-dateutil
        args: [--strict]

  - repo: https://github.com/PyCQA/bandit
    rev: 1.8.0  # MUST match bandit==1.8.0 in pyproject.toml
    hooks:
      - id: bandit
        args: [-r, src/, -ll]

  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v4.5.0
    hooks:
      - id: trailing-whitespace
      - id: end-of-file-fixer
      - id: check-yaml
      - id: check-added-large-files
      - id: detect-private-key
```

## Version Pinning Verification
Ensure no version mismatches between local and CI/CD (CodeBuild).

```bash
#!/bin/bash
# scripts/verify-versions.sh
# CRITICAL: Run this before every commit and in CI/CD pipeline

set -e

echo "Checking version parity across tooling files..."

# Extract versions from pyproject.toml
RUFF_VERSION=$(grep -oP "ruff==\K[0-9.]+|ruff>=[0-9.]+" pyproject.toml | head -1)
MYPY_VERSION=$(grep -oP "mypy==\K[0-9.]+|mypy>=[0-9.]+" pyproject.toml | head -1)
BANDIT_VERSION=$(grep -oP "bandit==\K[0-9.]+|bandit>=[0-9.]+" pyproject.toml | head -1)
PYTEST_VERSION=$(grep -oP "pytest==\K[0-9.]+|pytest>=[0-9.]+" pyproject.toml | head -1)

# Extract versions from .pre-commit-config.yaml
PRE_COMMIT_RUFF=$(grep -A2 'astral-sh/ruff-pre-commit' .pre-commit-config.yaml | grep 'rev:' | awk '{print $2}' | tr -d 'v')
PRE_COMMIT_MYPY=$(grep -A2 'mirrors-mypy' .pre-commit-config.yaml | grep 'rev:' | awk '{print $2}' | tr -d 'v')
PRE_COMMIT_BANDIT=$(grep -A2 'PyCQA/bandit' .pre-commit-config.yaml | grep 'rev:' | awk '{print $2}')

# Verify match (handle both == and >= formats)
check_version() {
    local tool=$1
    local pyproject_version=$2
    local precommit_version=$3
    
    # Strip >= or == prefix
    pyproject_version=$(echo "$pyproject_version" | sed 's/[><=]*//g')
    precommit_version=$(echo "$precommit_version" | sed 's/[><=]*//g')
    
    if [[ "$pyproject_version" != "$precommit_version" ]]; then
        echo "ERROR: $tool version mismatch (pyproject: $pyproject_version, pre-commit: $precommit_version)"
        return 1
    fi
    echo "✓ $tool: $pyproject_version"
}

check_version "ruff" "$RUFF_VERSION" "$PRE_COMMIT_RUFF" || exit 1
check_version "mypy" "$MYPY_VERSION" "$PRE_COMMIT_MYPY" || exit 1
check_version "bandit" "$BANDIT_VERSION" "$PRE_COMMIT_BANDIT" || exit 1

echo "✓ All tooling versions match"
```

## Makefile Targets
Single source of truth for all build commands. MUST complete in < 30 minutes (CodeBuild timeout).

```makefile
# Makefile
.PHONY: setup lint type-check security-check test test-unit test-integration smoke-test docker-up docker-down clean help

help:
	@echo "RideStream v2 Makefile"
	@echo "====================="
	@echo "setup              - Initialize venv, install deps, setup pre-commit"
	@echo "lint               - Run ruff linter and formatter"
	@echo "type-check         - Run mypy static type checker"
	@echo "security-check     - Run bandit security audit"
	@echo "test-unit          - Run unit tests (fast, no Docker)"
	@echo "test-integration   - Run integration tests (requires Docker)"
	@echo "test               - Run lint, type-check, security-check, and unit tests"
	@echo "smoke-test         - Validate Kafka, MinIO, Trino, Hive, Spark, Airflow"
	@echo "docker-up          - Start Docker Compose stack"
	@echo "docker-down        - Stop and clean Docker stack"
	@echo "clean              - Remove caches and build artifacts"

setup:
	@echo "Setting up RideStream v2..."
	python3 -m venv .venv
	./.venv/bin/pip install --upgrade pip uv
	./.venv/bin/uv sync
	./.venv/bin/pre-commit install
	dbt debug --profiles-dir .
	@echo "✓ Setup complete"

lint:
	@echo "Running linter (ruff)..."
	ruff check src/ tests/ --fix
	ruff format src/ tests/
	@echo "✓ Lint complete"

type-check:
	@echo "Running type checker (mypy)..."
	mypy --strict src/
	@echo "✓ Type check complete"

security-check:
	@echo "Running security audit (bandit)..."
	bandit -r src/ -ll
	@echo "✓ Security check complete"

test-unit:
	@echo "Running unit tests..."
	pytest tests/unit/ -v --cov=src.domain --cov=src.adapters --cov=src.application --cov-report=term-missing --cov-fail-under=85
	@echo "✓ Unit tests passed"

test-integration:
	@echo "Running integration tests..."
	docker compose -f docker/docker-compose.yml up -d
	sleep 10
	pytest tests/integration/ -v --timeout=60 -m integration || { docker compose -f docker/docker-compose.yml down -v; exit 1; }
	docker compose -f docker/docker-compose.yml down -v
	@echo "✓ Integration tests passed"

test: lint type-check security-check test-unit
	@echo "✓ All checks passed"

smoke-test:
	@echo "Running smoke tests..."
	bash scripts/smoke-test.sh
	@echo "✓ Smoke tests passed"

docker-up:
	docker compose -f docker/docker-compose.yml up -d
	sleep 10
	@echo "✓ Docker stack ready"

docker-down:
	docker compose -f docker/docker-compose.yml down -v

clean:
	rm -rf .pytest_cache target/ dbt_packages/ .ruff_cache .mypy_cache .venv
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name '*.pyc' -delete
	@echo "✓ Clean complete"
```

## buildspec.yml for AWS CodeBuild
CI/CD pipeline running in CodeBuild (NOT GitHub Actions). Must complete in 30 minutes.

```yaml
# buildspec.yml
version: 0.2

phases:
  pre_build:
    commands:
      - echo "Pre-build phase: install dependencies"
      - python3 -m venv .venv
      - source .venv/bin/activate
      - pip install --upgrade pip uv
      - uv sync
      - bash scripts/verify-versions.sh
      - pre-commit install
      - echo "Dependencies installed"

  build:
    commands:
      - echo "Build phase: lint, type-check, security-check"
      - source .venv/bin/activate
      
      - echo "Step 1: Linting with ruff"
      - ruff check src/ tests/ || exit 1
      - ruff format --check src/ tests/ || exit 1
      
      - echo "Step 2: Type checking with mypy"
      - mypy --strict src/ || exit 1
      
      - echo "Step 3: Security audit with bandit"
      - bandit -r src/ -ll || exit 1
      
      - echo "Step 4: Run unit tests"
      - pytest tests/unit/ -v --cov=src --cov-report=xml --cov-fail-under=85 || exit 1
      
      - echo "Step 5: Run integration tests"
      - docker compose -f docker/docker-compose.yml up -d
      - sleep 15
      - pytest tests/integration/ -v --timeout=60 -m integration || { docker compose -f docker/docker-compose.yml down -v; exit 1; }
      - docker compose -f docker/docker-compose.yml down -v
      
      - echo "Step 6: dbt tests (validates SQL models)"
      - dbt test --profiles-dir . || exit 1
      
      - echo "All stages passed"

  post_build:
    commands:
      - echo "Post-build phase: smoke tests and artifact generation"
      - bash scripts/smoke-test.sh || exit 1
      - echo "Generating test reports..."
      - |
        if [ -f coverage.xml ]; then
          echo "Coverage report generated"
        fi

artifacts:
  files:
    - 'src/**/*'
    - 'dbt/**/*'
    - 'tests/**/*'
    - 'pyproject.toml'
    - '.pre-commit-config.yaml'
    - 'Makefile'
    - 'buildspec.yml'
  name: ridestream-build-artifacts

cache:
  paths:
    - '.venv/**/*'
    - '.ruff_cache/**/*'
    - '.mypy_cache/**/*'
    - 'dbt_packages/**/*'

reports:
  coverage:
    files:
      - 'coverage.xml'
    file-format: COBERTURAXML
  pytest:
    files:
      - 'pytest-report.json'
    file-format: JSON

env:
  variables:
    RIDESTREAM_PROFILE: "dev"
    PYTHONPATH: "/codebuild/output/src:$PYTHONPATH"
    DBT_PROFILES_DIR: "."
```

## Smoke Test Script
Validates critical external services. MUST complete in < 5 minutes.

```bash
#!/bin/bash
# scripts/smoke-test.sh
# Validate Kafka, MinIO, Trino, Hive, Spark, Airflow connectivity

set -e

TIMEOUT_SEC=30
RETRIES=5

echo "Running smoke tests..."
echo "====================="

# Test 1: Kafka broker
echo "Test 1: Kafka broker..."
kafka_host=${KAFKA_HOST:-localhost}
kafka_port=${KAFKA_PORT:-9092}
for i in $(seq 1 $RETRIES); do
    if timeout $TIMEOUT_SEC python3 -c "
from kafka import KafkaConsumer
consumer = KafkaConsumer(bootstrap_servers=['$kafka_host:$kafka_port'], request_timeout_ms=5000)
consumer.close()
print('Kafka OK')
" 2>/dev/null; then
        echo "✓ Kafka broker OK"
        break
    elif [ $i -eq $RETRIES ]; then
        echo "✗ Kafka broker NOT responsive"
        exit 1
    else
        sleep 2
    fi
done

# Test 2: MinIO S3
echo "Test 2: MinIO S3..."
minio_host=${MINIO_HOST:-localhost}
minio_port=${MINIO_PORT:-9000}
for i in $(seq 1 $RETRIES); do
    if timeout $TIMEOUT_SEC python3 -c "
import boto3
client = boto3.client('s3', endpoint_url='http://$minio_host:$minio_port', aws_access_key_id='minioadmin', aws_secret_access_key='minioadmin')
client.list_buckets()
print('MinIO OK')
" 2>/dev/null; then
        echo "✓ MinIO S3 OK"
        break
    elif [ $i -eq $RETRIES ]; then
        echo "✗ MinIO S3 NOT responsive"
        exit 1
    else
        sleep 2
    fi
done

# Test 3: Trino coordinator
echo "Test 3: Trino coordinator..."
trino_host=${TRINO_HOST:-localhost}
trino_port=${TRINO_PORT:-8080}
for i in $(seq 1 $RETRIES); do
    if timeout $TIMEOUT_SEC curl -s -f "http://$trino_host:$trino_port/v1/info" > /dev/null 2>&1; then
        echo "✓ Trino coordinator OK"
        break
    elif [ $i -eq $RETRIES ]; then
        echo "✗ Trino coordinator NOT responsive"
        exit 1
    else
        sleep 2
    fi
done

# Test 4: Hive metastore
echo "Test 4: Hive metastore..."
hive_host=${HIVE_HOST:-localhost}
hive_port=${HIVE_PORT:-9083}
for i in $(seq 1 $RETRIES); do
    if timeout $TIMEOUT_SEC nc -z "$hive_host" "$hive_port" 2>/dev/null; then
        echo "✓ Hive metastore OK"
        break
    elif [ $i -eq $RETRIES ]; then
        echo "✗ Hive metastore NOT responsive"
        exit 1
    else
        sleep 2
    fi
done

# Test 5: Spark master
echo "Test 5: Spark master..."
spark_host=${SPARK_HOST:-localhost}
spark_port=${SPARK_PORT:-7077}
for i in $(seq 1 $RETRIES); do
    if timeout $TIMEOUT_SEC curl -s -f "http://$spark_host:$spark_port/" > /dev/null 2>&1; then
        echo "✓ Spark master OK"
        break
    elif [ $i -eq $RETRIES ]; then
        echo "✗ Spark master NOT responsive"
        exit 1
    else
        sleep 2
    fi
done

# Test 6: Airflow webserver
echo "Test 6: Airflow webserver..."
airflow_host=${AIRFLOW_HOST:-localhost}
airflow_port=${AIRFLOW_PORT:-8080}
for i in $(seq 1 $RETRIES); do
    if timeout $TIMEOUT_SEC curl -s -f "http://$airflow_host:$airflow_port/health" > /dev/null 2>&1; then
        echo "✓ Airflow webserver OK"
        break
    elif [ $i -eq $RETRIES ]; then
        echo "✗ Airflow webserver NOT responsive"
        exit 1
    else
        sleep 2
    fi
done

echo ""
echo "✓ All smoke tests passed"
```

## Version Conflict Resolution
Coordinate changes via architecture review. Process:

```bash
# 1. Check installed vs pinned versions
uv pip list | grep -E "ruff|mypy|bandit|pytest|pydantic|kafka"

# 2. Identify conflict (example: pytest)
# Current: pytest==8.0.0, want: pytest==8.1.0

# 3. Update pyproject.toml
sed -i 's/pytest==8.0.0/pytest==8.1.0/' pyproject.toml

# 4. Sync dependencies
uv sync

# 5. Verify changes
bash scripts/verify-versions.sh

# 6. Run ALL tests
make test
make test-integration
make smoke-test

# 7. If all pass, commit together:
git add pyproject.toml .pre-commit-config.yaml
git commit -m "chore: upgrade pytest to 8.1.0"

# 8. If any test fails, REVERT immediately:
git revert HEAD
uv sync
```

### Dependency Update Restrictions
- **pydantic, pydantic-settings**: Freeze to major.minor (>=2.0, no patch changes)
- **kafka-python, boto3, minio, dbt-core, dbt-trino, sqlalchemy**: Test before updating
- **Dev tools (ruff, mypy, bandit)**: Update independently, verify pre-commit versions match
- **pytest, pytest-asyncio, pytest-cov, pytest-mock**: Pinned, test all integration tests after change
- **Never update without running: `make test && make test-integration && make smoke-test`**

## Python Version
RideStream v2 REQUIRES Python 3.12 (uses StrEnum, datetime.UTC, etc.).

```bash
# Verify Python version
python3 --version  # Must be >= 3.12

# In buildspec.yml/CI, enforce:
python_version=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
if [ "$python_version" != "3.12" ]; then
    echo "ERROR: Python 3.12 required, found $python_version"
    exit 1
fi
```

## Tool Versions Summary

| Tool | Version | Purpose | Pinned in |
|------|---------|---------|-----------|
| Python | 3.12+ | Runtime | pyproject.toml |
| uv | latest | Package manager | CI/CD setup |
| ruff | 0.8.0 | Linter/formatter | pyproject.toml + .pre-commit-config.yaml |
| mypy | 1.13.0 | Type checker | pyproject.toml + .pre-commit-config.yaml |
| bandit | 1.8.0 | Security audit | pyproject.toml + .pre-commit-config.yaml |
| pytest | 8.0.0 | Test runner | pyproject.toml |
| pytest-asyncio | 0.24.0 | Async test support | pyproject.toml |
| black | 24.10.0 | Code formatter | pyproject.toml |
| pydantic | >=2.0 | Validation | pyproject.toml |
| kafka-python | >=2.0.2 | Kafka client | pyproject.toml |
| boto3 | >=1.28.0 | AWS SDK | pyproject.toml |
| minio | >=7.2.0 | MinIO client | pyproject.toml |
| dbt-core | >=1.7.0 | Data transformations | pyproject.toml |
| dbt-trino | >=1.7.0 | dbt Trino adapter | pyproject.toml |

## Anti-Patterns (Never Do This)
- Hardcoded environment variables in buildspec.yml (use CodeBuild secrets)
- GitHub Actions workflows (use AWS CodeBuild only)
- asyncpg, aioredis, FastAPI, uvicorn (batch architecture, not HTTP server)
- Postgres/Redis services in CI (use local adapters + Docker Compose for integration tests)
- Version mismatches between pyproject.toml and .pre-commit-config.yaml
- Running smoke tests without Docker stack up
- Skipping dbt test stage in buildspec.yml
- Uncommitted pyproject.toml changes (all versions controlled)
