#!/bin/bash
# Create CodePipeline stack in AWS for RideStream v2 CI/CD.
# Usage: bash scripts/setup-pipeline.sh [dev|staging|prod]

set -e

ENVIRONMENT="${1:-dev}"
STACK_NAME="ridestream-pipeline-${ENVIRONMENT}"
TEMPLATE="infrastructure/cloudformation/stacks/codepipeline.yaml"

echo "==== Setting up CodePipeline for environment: $ENVIRONMENT ===="

# Verify AWS credentials
if ! aws sts get-caller-identity > /dev/null 2>&1; then
    echo "ERROR: AWS credentials not configured. Run 'aws configure' first."
    exit 1
fi

# Validate template
echo "Validating CloudFormation template..."
aws cloudformation validate-template --template-body file://$TEMPLATE > /dev/null || {
    echo "ERROR: Template validation failed"
    exit 1
}

# Check if stack exists
if aws cloudformation describe-stacks --stack-name "$STACK_NAME" > /dev/null 2>&1; then
    echo "Stack $STACK_NAME exists. Updating via change set..."
    CHANGESET_NAME="update-$(date +%s)"
    aws cloudformation create-change-set \
        --stack-name "$STACK_NAME" \
        --change-set-name "$CHANGESET_NAME" \
        --template-body file://$TEMPLATE \
        --parameters ParameterKey=Environment,ParameterValue="$ENVIRONMENT" \
        --capabilities CAPABILITY_NAMED_IAM
    aws cloudformation wait change-set-create-complete \
        --stack-name "$STACK_NAME" --change-set-name "$CHANGESET_NAME"
    aws cloudformation describe-change-set \
        --stack-name "$STACK_NAME" --change-set-name "$CHANGESET_NAME" \
        --query 'Changes[].ResourceChange.{Action:Action, Resource:LogicalResourceId, Type:ResourceType}' \
        --output table
    echo "To apply: aws cloudformation execute-change-set --stack-name $STACK_NAME --change-set-name $CHANGESET_NAME"
else
    echo "Creating new stack: $STACK_NAME"
    aws cloudformation create-stack \
        --stack-name "$STACK_NAME" \
        --template-body file://$TEMPLATE \
        --parameters ParameterKey=Environment,ParameterValue="$ENVIRONMENT" \
        --capabilities CAPABILITY_NAMED_IAM
    aws cloudformation wait stack-create-complete --stack-name "$STACK_NAME"
    echo "Stack created successfully."
fi

echo ""
echo "==== Pipeline setup complete ===="
aws cloudformation describe-stacks --stack-name "$STACK_NAME" \
    --query 'Stacks[0].Outputs' --output table
