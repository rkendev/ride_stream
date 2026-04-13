# AWS Setup Guide

Step-by-step instructions for deploying RideStream v2 to AWS.

## Prerequisites

- AWS account with billing enabled
- IAM user with `AdministratorAccess` (or equivalent least-privilege set)
- AWS CLI v2+ installed and configured (`aws configure`)
- Bash shell (Linux, macOS, or WSL2)

Verify your access:

```bash
aws sts get-caller-identity
# Should return: {"UserId": "...", "Account": "...", "Arn": "..."}
```

## Deployment Order

CloudFormation stacks have dependencies — deploy in this order:

```
1. networking       (VPC, subnets, security groups)
2. iam              (5 least-privilege roles)
3. storage          (S3 buckets)
4. streaming        (MSK cluster + Firehose) ────── 15-20 min provisioning
5. compute          (EMR Serverless)
6. catalog          (Glue Data Catalog + Athena)
7. orchestration    (Step Functions)
8. monitoring       (CloudWatch + SNS)
9. codepipeline     (CodePipeline + CodeBuild)
10. main            (Root nested stack)
```

## Quick Deploy (Dev Environment)

```bash
# Set environment
export ENVIRONMENT=dev
export AWS_REGION=us-east-1

# Validate all templates first
make cfn-validate

# Deploy via setup script (creates/updates stacks)
bash scripts/setup-pipeline.sh dev
```

The script uses change sets for updates — always review before applying:

```bash
# Review change set
aws cloudformation describe-change-set \
  --stack-name ridestream-pipeline-dev \
  --change-set-name update-<timestamp>

# Apply after review
aws cloudformation execute-change-set \
  --stack-name ridestream-pipeline-dev \
  --change-set-name update-<timestamp>
```

## Staging → Production

```bash
# Deploy to staging
bash scripts/setup-pipeline.sh staging

# Verify staging
export AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
export ENVIRONMENT=staging
pytest tests/e2e/ -v  # runs AWS-only tests

# Deploy to prod (requires manual approval in CodePipeline)
bash scripts/setup-pipeline.sh prod
```

## Manual Validation Checklist

After each deployment, verify:

- [ ] VPC exists with 4 subnets (2 public, 2 private)
- [ ] MSK cluster is in `ACTIVE` state (takes 15-20 min)
- [ ] All 4 S3 buckets exist: `ridestream-{bronze,silver,gold,athena-results}-{env}`
- [ ] Glue database `ridestream_{env}` exists
- [ ] Athena workgroup `ridestream-{env}` exists and is enabled
- [ ] Step Functions state machine visible in console
- [ ] CloudWatch dashboard `ridestream-{env}` visible
- [ ] CodePipeline `ridestream-pipeline-{env}` exists and runs

Automated validation:

```bash
# Runs E2E tests against AWS (skips without AWS_ACCOUNT_ID)
pytest tests/e2e/ -v
```

## Cost Estimation

| Service | Config | Monthly Cost (est.) |
|---------|--------|---------------------|
| MSK Cluster | 3× `kafka.m7g.small` | ~$108 |
| Kinesis Firehose | 5-min buffer | $0.029/GB ingested |
| S3 (standard) | 100 GB | ~$2.30 |
| EMR Serverless | 4 vCPU × 10h/day | ~$50 |
| Glue Catalog | 10K queries | ~$1 |
| Athena | 100 GB scanned | ~$0.50 |
| CloudWatch | Standard | ~$5 |
| Step Functions | Daily runs | ~$1 |
| **Total (dev)** | — | **~$170/month** |

Production with higher volume: $500-2000/month depending on load.

Cost optimization tips:

- Use S3 Intelligent-Tiering on bronze bucket (lifecycle rule configured)
- Partition bronze data by date (reduces Athena scan cost)
- Enable MSK auto-scaling only for production
- Use EMR Serverless `AutoStopConfiguration` (15 min idle timeout already set)
- Delete `athena-results/` objects after 7 days (lifecycle rule configured)

## Secrets Management

All secrets live in AWS Secrets Manager — **never** in env files or git.

```bash
# Create DB secret
aws secretsmanager create-secret \
  --name prod/ridestream/db \
  --secret-string '{
    "username": "ridestream_admin",
    "password": "<strong-random-password>"
  }'

# GitHub OAuth token for CodePipeline
aws secretsmanager create-secret \
  --name prod/ridestream/github-token \
  --secret-string '<github-pat>'
```

CloudFormation references secrets via:

```yaml
MasterUserPassword: !Sub '{{resolve:secretsmanager:prod/ridestream/db:SecretString:password}}'
```

## Rollback Procedure

If a deployment fails:

1. **CloudFormation auto-rollback**: Stacks roll back on create/update failure by default.
2. **Manual rollback**: `aws cloudformation cancel-update-stack --stack-name <name>`
3. **Data rollback**: Bronze bucket is append-only; silver/gold can be recomputed via `dbt run --full-refresh`.
4. **Drift detection**: `aws cloudformation detect-stack-drift --stack-name <name>`

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| MSK stuck in `CREATING` | Provisioning >20 min | Wait; check CloudWatch logs for `/aws/msk/` |
| Firehose not delivering | 5-min buffer | Wait 6 min before checking S3 |
| Athena query fails with permissions | IAM policy | Check `RideStream-EMRJobRole-${Env}` has s3:GetObject |
| Step Functions timing out | EMR cold start | Increase `TimeoutSeconds` to 900 |
| CodeBuild failing on smoke test | Docker not available | Enable privileged mode in CodeBuild project |

## References

- [ARCHITECTURE.md](ARCHITECTURE.md) — System design
- [DOCKER_SERVICES.md](DOCKER_SERVICES.md) — Local dev equivalents
- AWS docs: https://docs.aws.amazon.com/cloudformation/
- ADR-006: CloudFormation over Terraform (see TECHNICAL_PLAN.md)
