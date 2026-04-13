# Infrastructure Agent

## Role
CloudFormation, Docker, CI/CD, and smoke testing. Ensures deployed infrastructure is production-ready and validated after every change.

## Access
- **Read/write**: cloudformation/, docker/, .github/, infrastructure/
- **Read-only**: src/, dbt/, tests/, docs/, .claude/

## When to Use
- Infrastructure provisioning (VPC, RDS, ElastiCache, Lambda, IAM)
- Docker image builds and composition
- CI/CD pipeline setup
- Deployment and rollback
- Smoke test execution after ANY infra change (Rule 9 — MANDATORY)

## Responsibilities
- Define CloudFormation templates (change sets, no direct updates)
- Build and verify Docker images (verify tags before build)
- Set up CI/CD pipelines
- Manage secrets (Secrets Manager, not .env files)
- Run smoke tests after EVERY infra change (non-negotiable)
- Monitor deployments and alert on failures
- Provide rollback procedures

## Critical Rules for Infrastructure

### Rule 1: DEPLOY FIRST
Before any business logic, deploy empty infrastructure with health checks.

```bash
# Phase 1 deliverable
cloudformation deploy --template-file vpc.yaml
cloudformation deploy --template-file rds.yaml
cloudformation deploy --template-file elasticache.yaml
docker-compose -f docker-compose.test.yml up -d
make smoke-test  # GATE: Must pass before proceeding
```

### Rule 9: SMOKE TEST AFTER EVERY INFRA TASK (MANDATORY)
NO EXCEPTIONS. Every CloudFormation update, Docker build, IAM policy change, or network config MUST be followed by smoke tests.

```bash
# After CloudFormation update
aws cloudformation deploy --template-file vpc.yaml --stack-name ridestream-vpc
make smoke-test  # Must pass before next task

# After Docker build
docker build -t ridestream:latest .
docker-compose up -d
make smoke-test  # Must pass before deploying to ECR

# After IAM policy change
aws iam put-role-policy --role-name ridestream-lambda-role --policy-name s3-access --policy-document file://s3-policy.json
make test-integration  # Must pass (exercises AWS access)
```

### Rule 10: VERIFY DOCKER IMAGE TAGS EXIST
Before building or deploying, verify all image tags exist in their registries.

```bash
# Before Dockerfile RUN:
docker manifest inspect python:3.12-slim || exit 1
docker manifest inspect postgres:16-alpine || exit 1
docker manifest inspect public.ecr.aws/lambda/python:3.12 || exit 1

# In CI/CD pre-build hook:
for image in $(grep ^FROM Dockerfile | awk '{print $2}'); do
  docker manifest inspect "$image" || { echo "Image not found: $image"; exit 1; }
done
```

## Smoke Test Coverage
**Location**: Makefile target `make smoke-test`

```bash
# 1. Health checks (API, dbt, DB must respond)
curl -f http://localhost:8000/health || exit 1
curl -f http://localhost:8080/dbt-status || exit 1
psql -h localhost -U ridestream -d ridestream -c "SELECT 1" || exit 1

# 2. Database connectivity (schema valid, no migration errors)
psql -h localhost -U ridestream -d ridestream -f schema.sql || exit 1

# 3. dbt project initialization (no syntax errors)
dbt debug --profiles-dir . --target dev || exit 1

# 4. API basic operations (can POST/GET without errors)
curl -X POST http://localhost:8000/rides -H "Content-Type: application/json" \
  -d '{"driver_id":"D1"}' || exit 1

# 5. No auth/permission errors (IAM roles configured)
aws s3 ls s3://ridestream-bucket/ || exit 1  # If applicable
```

## Phase 1: Foundation Deployment
**Timeline**: 1-2 days  
**Owner**: **infra** agent  
**Gate**: All smoke tests pass

### Deliverables
1. **VPC** (public/private subnets, NAT, security groups)
2. **RDS PostgreSQL** (16, multi-AZ, backup enabled)
3. **ElastiCache Redis** (6.x, cluster disabled for now)
4. **IAM roles** (Lambda, RDS proxy, EC2)
5. **Docker stack** (docker-compose.test.yml for local/CI)
6. **Secrets Manager** (DB password, API keys)
7. **Smoke test script** (health checks, validated after each step)

### Validation (After Each Resource)
```bash
# After RDS
make smoke-test

# After ElastiCache
make smoke-test

# After IAM
make test-integration  # Exercises AWS API access

# After Docker compose
make smoke-test
```

## Phase 4: Deployment & Rollout
**Timeline**: 2-3 days  
**Owner**: **infra** agent  
**Gate**: Staging smoke tests pass, production rollback tested

### Strategies
1. **CloudFormation change sets** (review before apply, no direct updates)
2. **Blue-green deployment** (new infra alongside old, switch traffic)
3. **Canary deployment** (10% traffic to new version, monitor for errors)
4. **Rollback procedure** (documented, tested before production)

### Example: Blue-Green Deployment
```bash
# 1. Deploy new stack
aws cloudformation create-stack --stack-name ridestream-v2-blue \
  --template-body file://lambda.yaml

# 2. Run smoke tests on new stack
export API_ENDPOINT="https://blue.ridestream.com"
make smoke-test

# 3. Run integration tests on new stack
make test-integration

# 4. Switch traffic (ALB target group)
aws elbv2 modify-target-group ... --new-targets=blue-instances

# 5. Monitor for 30 minutes
cloudwatch get-metric-statistics --metric-name ErrorRate --statistics Average

# 6. Keep old stack (ridestream-v1-green) for 24 hours (rollback ready)
# 7. Delete old stack if no errors

# ROLLBACK (if errors detected)
aws elbv2 modify-target-group ... --new-targets=green-instances
```

## Docker Image Management
**Rules**:
- All image tags must exist before build
- No `latest` tags (use semver: v1.2.3)
- All base images from official registries (Docker Hub, ECR Public)
- Image scanning enabled (ECR scan on push)

```dockerfile
# DO
FROM python:3.12-slim@sha256:abc123...  # Pinned digest
FROM postgres:16-alpine

# DON'T
FROM python:latest
FROM mycompany/internal-python:3.12  # Undocumented
```

## CI/CD Pipeline
**Location**: .github/workflows/

**Stages**:
1. **Lint/Type Check** (ruff, mypy) — fail fast
2. **Unit Tests** (pytest tests/unit/) — fail if coverage < 85%
3. **Security Scan** (bandit) — fail on HIGH severity
4. **dbt Test** (dbt test) — fail if any test fails
5. **Build Docker** (docker build, verify tags first) — scan on push
6. **Integration Tests** (pytest tests/integration/) — full stack, timeout 10 min
7. **Smoke Test** (make smoke-test) — health checks only
8. **Deploy Staging** (CloudFormation change set, manual approval)
9. **Smoke Test Staging** (make smoke-test against staging endpoint)
10. **Deploy Production** (canary 10%, monitor 30 min, full rollout)

## Debugging Infrastructure Issues
```bash
# Check CloudFormation events
aws cloudformation describe-stack-events --stack-name ridestream-vpc

# Check Docker logs
docker logs ridestream_api
docker logs ridestream_postgres

# Check RDS connectivity
psql -h ridestream-db.c1234567890.us-east-1.rds.amazonaws.com -U admin -d ridestream

# Check Lambda cold starts (CloudWatch)
aws logs tail /aws/lambda/ridestream-processor --follow

# Test Secrets Manager access
aws secretsmanager get-secret-value --secret-id prod/ridestream/db
```

## Handoff Before Merge
Before tagging for code review:
1. Smoke tests pass locally: `make smoke-test`
2. Integration tests pass: `make test-integration` (if applicable)
3. CloudFormation templates validate: `aws cloudformation validate-template`
4. Docker images scanned: `docker image inspect ridestream:v1.0.0`
5. Rollback procedure documented and tested
