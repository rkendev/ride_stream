.PHONY: setup all lint format typecheck type-check security-check test test-unit test-integration smoke-test docker-up docker-down clean cfn-validate verify-versions

# ──────────────────────────────────────────────────────────
# RideStream v2 — All operations via make targets
# ──────────────────────────────────────────────────────────

VENV := .venv
PYTHON := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
ACTIVATE := . $(VENV)/bin/activate &&

setup:
	@echo "Setting up RideStream v2..."
	python3.12 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -e ".[dev]"
	$(ACTIVATE) pre-commit install || true
	@echo "Setup complete"

all: lint typecheck security-check test
	@echo "All checks passed"

# ── Linting & Formatting ──────────────────────────────────

lint:
	@echo "Running linter..."
	$(ACTIVATE) ruff check src/ tests/
	$(ACTIVATE) ruff format --check src/ tests/

format:
	@echo "Formatting code..."
	$(ACTIVATE) ruff format src/ tests/
	$(ACTIVATE) ruff check --fix src/ tests/

# ── Type Checking ─────────────────────────────────────────

typecheck type-check:
	@echo "Running type checker..."
	$(ACTIVATE) mypy src/ridestream/

# ── Security ──────────────────────────────────────────────

security-check:
	@echo "Running security audit..."
	$(ACTIVATE) bandit -r src/ -ll -q

# ── Tests ─────────────────────────────────────────────────

test: lint type-check security-check test-unit
	@echo "All checks and tests passed"

test-unit:
	@echo "Running unit tests..."
	$(ACTIVATE) pytest tests/unit/ -v --cov=src/ridestream --cov-report=term-missing

test-integration:
	@echo "Running integration tests (Docker required)..."
	$(ACTIVATE) pytest tests/integration/ -v --timeout=120

smoke-test:
	@echo "==== RideStream v2 Smoke Test ===="
	@echo "  Checking Kafka topics..."
	@docker exec rs2-kafka kafka-topics --bootstrap-server localhost:9092 --list 2>/dev/null | grep -q "ride.events.raw" || (echo "  FAIL: Kafka topic ride.events.raw missing"; exit 1)
	@echo "  Kafka topics: PASS"
	@echo "  Checking MinIO buckets..."
	@docker run --rm --network docker_default --entrypoint sh minio/mc:latest -c "mc alias set minio http://minio:9000 minioadmin minioadmin > /dev/null 2>&1 && mc ls minio/bronze > /dev/null && mc ls minio/silver > /dev/null && mc ls minio/gold > /dev/null" || (echo "  FAIL: MinIO buckets missing"; exit 1)
	@echo "  MinIO buckets: PASS"
	@echo "  Checking Trino connectivity..."
	@docker exec rs2-trino trino --execute "SELECT 1" > /dev/null 2>&1 || (echo "  FAIL: Trino not responding"; exit 1)
	@echo "  Trino: PASS"
	@echo "  Checking Hive Metastore port..."
	@docker inspect rs2-hive-metastore --format '{{.State.Health.Status}}' 2>/dev/null | grep -q "healthy" || (echo "  FAIL: Hive Metastore not healthy"; exit 1)
	@echo "  Hive Metastore: PASS"
	@echo "  Checking Spark master..."
	@curl -sf http://localhost:8090/ > /dev/null 2>&1 || (echo "  FAIL: Spark master UI not responding"; exit 1)
	@echo "  Spark master: PASS"
	@echo "  Checking Airflow..."
	@curl -sf http://localhost:8085/health > /dev/null 2>&1 || (echo "  FAIL: Airflow webserver not responding"; exit 1)
	@echo "  Airflow: PASS"
	@echo "==== ALL SMOKE TESTS PASSED ===="

# ── Docker ────────────────────────────────────────────────

docker-up:
	@echo "Starting Docker stack..."
	docker compose -f docker/docker-compose.yml up -d --build
	@echo "Waiting for services to be healthy (up to 120s)..."
	@for i in $$(seq 1 24); do \
		HEALTHY=$$(docker compose -f docker/docker-compose.yml ps --format json 2>/dev/null | grep -c '"healthy"' || echo 0); \
		TOTAL=$$(docker compose -f docker/docker-compose.yml ps --services 2>/dev/null | wc -l); \
		echo "  [$$(( i * 5 ))s] $$HEALTHY services healthy"; \
		sleep 5; \
	done
	@echo "Docker stack ready — run 'make smoke-test' to verify"

docker-down:
	@echo "Tearing down Docker stack..."
	docker compose -f docker/docker-compose.yml down -v --remove-orphans
	@echo "Docker stack stopped"

# ── CloudFormation ────────────────────────────────────────

cfn-validate:
	@echo "Validating CloudFormation templates..."
	@for f in infrastructure/cloudformation/stacks/*.yaml; do \
		echo "Validating $$f..."; \
		aws cloudformation validate-template --template-body file://$$f || exit 1; \
	done
	@echo "All CloudFormation templates valid"

# ── Version Parity ────────────────────────────────────────

verify-versions:
	$(ACTIVATE) bash scripts/verify-versions.sh

# ── Cleanup ───────────────────────────────────────────────

clean:
	rm -rf .pytest_cache .ruff_cache .mypy_cache build dist *.egg-info
	rm -rf dbt/target dbt/dbt_packages dbt/logs
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name '*.pyc' -delete 2>/dev/null || true
	@echo "Cleaned"
