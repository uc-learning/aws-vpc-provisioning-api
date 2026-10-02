# AWS VPC Provisioning API

A production-grade serverless REST API that provisions AWS VPCs and subnets asynchronously, protected by Amazon Cognito, orchestrated by AWS Step Functions, and deployed with AWS SAM.

```
POST /vpcs          → validates → DynamoDB (PENDING) → Step Functions → EC2
GET  /vpcs/{id}     → DynamoDB read → current status + resource IDs
GET  /health        → unauthenticated liveness probe
```

---

## Architecture

```
Client ──► Cognito (JWT) ──► API Gateway ──► Create Lambda ──► DynamoDB (PENDING)
                                                            └──► Step Functions
                                                                     ├─ CreateVpc  (EC2)
                                                                     ├─ CreateSubnet × N (EC2)
                                                                     └─ DynamoDB (SUCCEEDED / FAILED)

Client ──► API Gateway ──► Get Lambda ──► DynamoDB (read)
```

Regenerate the full diagrams (requires [Graphviz](https://graphviz.org/download/)):

```bash
dot -Tpng docs/diagrams/architecture.dot -o docs/diagrams/architecture.png
dot -Tpng docs/diagrams/workflow.dot     -o docs/diagrams/workflow.png
```

### Why asynchronous?

Infrastructure provisioning takes variable time, can partially succeed, and needs retries. Coupling that to a synchronous HTTP request forces long timeouts and makes partial failure invisible. Step Functions Standard Workflows make every state, retry, and failure branch durable, auditable, and independently testable.

---

## Technology Stack

| Layer | Technology |
|---|---|
| Language | Python 3.11 |
| API | Amazon API Gateway (REST) + Cognito Authorizer |
| Auth | Amazon Cognito User Pool |
| Compute | AWS Lambda (arm64) |
| Orchestration | AWS Step Functions Standard Workflow |
| Persistence | Amazon DynamoDB (PAY_PER_REQUEST, PITR enabled) |
| Infrastructure | AWS VPC / EC2 APIs |
| IaC | AWS SAM (CloudFormation) |
| Observability | CloudWatch Logs + Metrics + Alarms, X-Ray, CloudTrail |
| Testing | pytest, moto, jsonschema |
| CI/CD | GitHub Actions + OIDC federation |

---

## Repository Structure

```
aws-vpc-provisioning-api/
├── template.yaml                   # SAM / CloudFormation template (all resources)
├── samconfig.toml.example          # SAM deployment configuration template
├── pyproject.toml                  # Python project + dev dependencies
├── Makefile                        # install / lint / test / build / deploy targets
├── src/
│   ├── common/                     # Shared layer: models, validation, repository, responses, logging
│   │   ├── models.py               # Pydantic v2 request/response models
│   │   ├── validation.py           # Pure CIDR / AZ validation (no AWS calls)
│   │   ├── repository.py           # DynamoDB access layer with idempotency
│   │   ├── responses.py            # HTTP response helpers (200/202/400/404/409/500)
│   │   └── logging.py              # Structured JSON logging
│   ├── create_vpc/
│   │   └── app.py                  # POST /vpcs Lambda handler
│   ├── get_vpc/
│   │   └── app.py                  # GET /vpcs/{requestId} Lambda handler
│   └── health/
│       └── app.py                  # GET /health Lambda handler
├── statemachine/
│   └── provision_vpc.asl.json      # Step Functions ASL definition
├── tests/
│   ├── unit/                       # Pure unit tests (no AWS calls)
│   │   ├── test_validation.py
│   │   ├── test_models.py
│   │   ├── test_repository.py      # Uses moto to mock DynamoDB
│   │   ├── test_create_vpc_handler.py
│   │   ├── test_get_vpc_handler.py
│   │   └── test_responses.py
│   ├── integration/
│   │   └── test_api_integration.py # End-to-end tests against deployed stack
│   └── contract/
│       ├── openapi_schema.json     # OpenAPI 3.0 schema
│       └── test_contract.py        # JSON Schema contract validation tests
├── docs/
│   ├── api-examples.md             # Full curl examples for every endpoint
│   └── diagrams/
│       ├── architecture.dot        # Graphviz architecture source
│       └── workflow.dot            # Graphviz Step Functions workflow source
├── scripts/
│   ├── create_demo_user.sh         # Create a Cognito user for demo
│   ├── get_token.sh                # Obtain a Cognito JWT
│   ├── smoke_test.sh               # Full end-to-end smoke test
│   └── cleanup_demo_resources.sh  # Delete tagged VPCs/subnets after demo
└── .github/
    └── workflows/
        └── ci.yml                  # GitHub Actions CI pipeline
```

---

## Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| AWS CLI | v2 | Configured with deployment credentials |
| AWS SAM CLI | ≥ 1.110 | `pip install aws-sam-cli` |
| Python | 3.11 | Runtime and local dev |
| Docker | any | Required for `sam build --use-container` |
| Graphviz | any | Optional, for regenerating diagrams |

### AWS permissions needed for deployment

The deploying IAM principal needs permissions to create/update:
- CloudFormation stacks
- Lambda functions and layers
- API Gateway REST APIs
- Cognito User Pools and clients
- DynamoDB tables
- Step Functions state machines
- IAM roles and policies
- CloudWatch Log Groups and Alarms

For a demo account, `AdministratorAccess` is simplest. For production, scope to the above services.

---

## Deployment

### 1. Clone and install

```bash
git clone https://github.com/<your-org>/aws-vpc-provisioning-api.git
cd aws-vpc-provisioning-api

python -m pip install -e ".[dev]"
```

### 2. Run local checks

```bash
make lint        # ruff check + format
make test-unit   # unit tests (no AWS needed)
```

### 3. Configure SAM

```bash
cp samconfig.toml.example samconfig.toml
# Edit samconfig.toml:
#   - stack_name
#   - s3_bucket (an existing S3 bucket for SAM artifacts)
#   - region
#   - parameter_overrides (Environment, CognitoUserPoolDomain)
```

### 4. Build and deploy

```bash
# First deployment — interactive guided wizard
sam build
sam deploy --guided

# Subsequent deployments
sam build && sam deploy
```

Note the outputs after deployment — you need `CognitoUserPoolId`, `CognitoUserPoolClientId`, and `ApiEndpoint`.

### 5. Capture stack outputs

```bash
STACK_NAME="aws-vpc-provisioning-api"    # matches samconfig.toml
REGION="ap-south-1"

export API_ENDPOINT=$(aws cloudformation describe-stacks \
    --stack-name "${STACK_NAME}" --region "${REGION}" \
    --query 'Stacks[0].Outputs[?OutputKey==`ApiEndpoint`].OutputValue' \
    --output text)

export USER_POOL_ID=$(aws cloudformation describe-stacks \
    --stack-name "${STACK_NAME}" --region "${REGION}" \
    --query 'Stacks[0].Outputs[?OutputKey==`CognitoUserPoolId`].OutputValue' \
    --output text)

export CLIENT_ID=$(aws cloudformation describe-stacks \
    --stack-name "${STACK_NAME}" --region "${REGION}" \
    --query 'Stacks[0].Outputs[?OutputKey==`CognitoUserPoolClientId`].OutputValue' \
    --output text)

echo "API: ${API_ENDPOINT}"
echo "Pool: ${USER_POOL_ID}   Client: ${CLIENT_ID}"
```

---

## Authentication

### Create a demo user

```bash
./scripts/create_demo_user.sh \
    --user-pool-id  "${USER_POOL_ID}" \
    --username      "demo@example.com" \
    --password      "Demo@Password1!" \
    --region        "${REGION}"
```

### Obtain a JWT

```bash
export COGNITO_TOKEN=$(./scripts/get_token.sh \
    --client-id  "${CLIENT_ID}" \
    --username   "demo@example.com" \
    --password   "Demo@Password1!" \
    --region     "${REGION}")

echo "Token obtained (first 40 chars): ${COGNITO_TOKEN:0:40}..."
```

The token is a Cognito IdToken (JWT). Pass it as `Authorization: Bearer <token>` on all business endpoints.

---

## API Usage

Full examples with request/response bodies are in [docs/api-examples.md](docs/api-examples.md).

### POST /vpcs — Create a VPC

```bash
curl -s -X POST "${API_ENDPOINT}/vpcs" \
  -H "Authorization: Bearer ${COGNITO_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "clientRequestId": "demo-20260928-001",
    "name": "interview-demo-vpc",
    "cidrBlock": "10.20.0.0/16",
    "subnets": [
      {"name": "app-a", "cidrBlock": "10.20.1.0/24", "availabilityZone": "ap-south-1a"},
      {"name": "app-b", "cidrBlock": "10.20.2.0/24", "availabilityZone": "ap-south-1b"}
    ],
    "tags": {"Environment": "demo", "Owner": "aws-challenge"}
  }' | python3 -m json.tool
```

Returns `202 Accepted` with a `requestId` immediately. Provisioning continues asynchronously.

### GET /vpcs/{requestId} — Poll for status

```bash
export REQUEST_ID="<requestId from POST response>"

# Poll manually
curl -s "${API_ENDPOINT}/vpcs/${REQUEST_ID}" \
  -H "Authorization: Bearer ${COGNITO_TOKEN}" | python3 -m json.tool

# Or poll in a loop until SUCCEEDED / FAILED
while true; do
  STATUS=$(curl -s "${API_ENDPOINT}/vpcs/${REQUEST_ID}" \
    -H "Authorization: Bearer ${COGNITO_TOKEN}" \
    | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])")
  echo "$(date -u +%T)  status: ${STATUS}"
  case "${STATUS}" in SUCCEEDED|FAILED|ROLLED_BACK) break ;; esac
  sleep 5
done
```

When `SUCCEEDED`, the response includes `vpc.vpcId` and `subnets[].subnetId`.

### GET /health — Liveness probe (no auth)

```bash
curl -s "${API_ENDPOINT}/health"
# {"status":"ok","service":"vpc-provisioning-api","environment":"demo"}
```

---

## Idempotency

Pass `clientRequestId` in every POST request. The service stores a normalized payload hash alongside the key.

- Same `clientRequestId` + same payload → returns the **existing** `requestId` and current status (no second VPC is created).
- Same `clientRequestId` + different payload → `409 Conflict` with `existingRequestId` in the response.
- No `clientRequestId` → every call creates a new request (not idempotent).

---

## Validation Rules

| Rule | Detail |
|---|---|
| VPC CIDR | Valid IPv4 CIDR, RFC 1918, prefix /16–/28 |
| Subnet CIDRs | Each must be contained within the VPC CIDR |
| Subnet overlap | No two subnet CIDRs may overlap |
| Availability Zones | Must match `<region><letter>` pattern and belong to the configured region |
| Subnet count | 1–20 subnets per request |
| Reserved tags | `RequestId`, `ManagedBy`, `Environment` are controlled by the service and rejected if supplied by the caller |
| Extra fields | Unknown request fields are rejected (strict mode) |

---

## Running Tests

```bash
# All unit tests (no AWS required)
make test-unit

# Contract tests (no AWS required)
make test-contract

# Integration tests (requires deployed stack + COGNITO_TOKEN)
export API_ENDPOINT="https://..."
export COGNITO_TOKEN="..."
make test-integration

# Full suite
make test
```

Test coverage report is written to `htmlcov/index.html` after any pytest run.

---

## Observability

### CloudWatch Logs

Each Lambda emits structured JSON logs. Search with CloudWatch Logs Insights:

```
fields @timestamp, level, requestId, executionArn, message
| filter @logStream like /create_vpc/
| sort @timestamp desc
| limit 50
```

### Step Functions execution history

```bash
STATE_MACHINE_ARN=$(aws cloudformation describe-stacks \
    --stack-name "${STACK_NAME}" --region "${REGION}" \
    --query 'Stacks[0].Outputs[?OutputKey==`StateMachineArn`].OutputValue' \
    --output text)

aws stepfunctions list-executions \
    --region "${REGION}" \
    --state-machine-arn "${STATE_MACHINE_ARN}" \
    --max-results 5 \
    --output table
```

### DynamoDB record

```bash
TABLE=$(aws cloudformation describe-stacks \
    --stack-name "${STACK_NAME}" --region "${REGION}" \
    --query 'Stacks[0].Outputs[?OutputKey==`DynamoDBTableName`].OutputValue' \
    --output text)

aws dynamodb get-item \
    --region "${REGION}" \
    --table-name "${TABLE}" \
    --key "{\"requestId\":{\"S\":\"${REQUEST_ID}\"}}" \
    --output json
```

### Alarms

Three CloudWatch Alarms are deployed automatically:
- `vpc-api-create-lambda-errors-<env>` — Create Lambda error rate
- `vpc-provisioning-sfn-failures-<env>` — Step Functions execution failures
- `vpc-api-gateway-5xx-<env>` — API Gateway 5xx rate

---

## Security Design

| Area | Implementation |
|---|---|
| Identity | Cognito User Pool; strong password policy; advanced security mode |
| Transport | TLS only (API Gateway enforces HTTPS) |
| Authorization | Cognito JWT authorizer on all business endpoints; health endpoint is unauthenticated |
| IAM | Separate least-privilege roles for Create Lambda (DynamoDB write + SFN start), Get Lambda (DynamoDB read), Step Functions (EC2 + DynamoDB update) |
| EC2 scope | Region-condition on all EC2 actions; mandatory `RequestId` and `ManagedBy` tags on every resource |
| Data at rest | DynamoDB SSE enabled |
| Secrets | No static AWS credentials anywhere; Lambda uses execution roles; CI uses OIDC federation |
| Logging | JWTs are never logged; `Authorization` header is excluded from access logs |
| Audit | CloudTrail records every `CreateVpc` / `CreateSubnet` call with the service role ARN |

---

## Error Reference

| HTTP | Error code | Cause |
|---|---|---|
| 400 | `BAD_REQUEST` | Invalid CIDR, overlapping subnets, wrong AZ, reserved tag, unknown field |
| 401 | `UNAUTHORIZED` | Missing or invalid JWT |
| 404 | `NOT_FOUND` | `requestId` does not exist in DynamoDB |
| 409 | `CONFLICT` | Same `clientRequestId` submitted with a different payload |
| 429 | `TOO_MANY_REQUESTS` | API Gateway throttle limit exceeded |
| 500 | `INTERNAL_ERROR` | Unexpected error (details in CloudWatch Logs) |

---

## Demo Runbook

Run this sequence during the interview to demonstrate every aspect live.

```bash
# 1. Show structure
tree -L 3 .

# 2. Regenerate architecture diagram
dot -Tpng docs/diagrams/architecture.dot -o docs/diagrams/architecture.png
open docs/diagrams/architecture.png        # macOS; use xdg-open on Linux

# 3. Show the Step Functions workflow diagram
dot -Tpng docs/diagrams/workflow.dot -o docs/diagrams/workflow.png
open docs/diagrams/workflow.png

# 4. Authenticate
export COGNITO_TOKEN=$(./scripts/get_token.sh \
    --client-id "${CLIENT_ID}" --username "demo@example.com" --password "Demo@Password1!" --region "${REGION}")

# 5. Demonstrate 401 (no token)
curl -s -X POST "${API_ENDPOINT}/vpcs" -H "Content-Type: application/json" \
  -d '{"name":"test","cidrBlock":"10.0.0.0/16","subnets":[]}' | python3 -m json.tool

# 6. Submit provisioning request
RESP=$(curl -s -X POST "${API_ENDPOINT}/vpcs" \
  -H "Authorization: Bearer ${COGNITO_TOKEN}" -H "Content-Type: application/json" \
  -d '{"clientRequestId":"demo-live-001","name":"demo-vpc","cidrBlock":"10.20.0.0/16",
       "subnets":[{"name":"app-a","cidrBlock":"10.20.1.0/24","availabilityZone":"'"${REGION}a"'"},
                  {"name":"app-b","cidrBlock":"10.20.2.0/24","availabilityZone":"'"${REGION}b"'"}],
       "tags":{"Owner":"demo"}}')
echo "${RESP}" | python3 -m json.tool
export REQUEST_ID=$(echo "${RESP}" | python3 -c "import sys,json; print(json.load(sys.stdin)['requestId'])")

# 7. Open Step Functions in AWS Console — show execution states live

# 8. Poll status
curl -s "${API_ENDPOINT}/vpcs/${REQUEST_ID}" -H "Authorization: Bearer ${COGNITO_TOKEN}" | python3 -m json.tool

# 9. Verify in EC2
VPC_ID=$(curl -s "${API_ENDPOINT}/vpcs/${REQUEST_ID}" \
  -H "Authorization: Bearer ${COGNITO_TOKEN}" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['vpc']['vpcId'])")
aws ec2 describe-vpcs --region "${REGION}" --vpc-ids "${VPC_ID}" --output table

# 10. Demonstrate idempotency (same clientRequestId returns same requestId)
curl -s -X POST "${API_ENDPOINT}/vpcs" \
  -H "Authorization: Bearer ${COGNITO_TOKEN}" -H "Content-Type: application/json" \
  -d '{"clientRequestId":"demo-live-001","name":"demo-vpc","cidrBlock":"10.20.0.0/16",
       "subnets":[{"name":"app-a","cidrBlock":"10.20.1.0/24","availabilityZone":"'"${REGION}a"'"},
                  {"name":"app-b","cidrBlock":"10.20.2.0/24","availabilityZone":"'"${REGION}b"'"}],
       "tags":{"Owner":"demo"}}' | python3 -m json.tool

# 11. Show DynamoDB record
aws dynamodb get-item --region "${REGION}" --table-name "${TABLE}" \
  --key "{\"requestId\":{\"S\":\"${REQUEST_ID}\"}}" --output json

# 12. Show CloudWatch structured logs

# 13. Run full smoke test
./scripts/smoke_test.sh \
    --api-endpoint "${API_ENDPOINT}" \
    --client-id    "${CLIENT_ID}" \
    --username     "demo@example.com" \
    --password     "Demo@Password1!" \
    --region       "${REGION}"
```

---

## Cleanup

After the demo, remove all VPCs and subnets created by this service:

```bash
# Dry run first — review what will be deleted
./scripts/cleanup_demo_resources.sh --region "${REGION}" --dry-run

# Live cleanup
./scripts/cleanup_demo_resources.sh --region "${REGION}"
```

To tear down the entire stack (does NOT delete the DynamoDB table — DeletionPolicy: Retain):

```bash
sam delete --stack-name "${STACK_NAME}" --region "${REGION}"
```

To also delete the DynamoDB table:

```bash
TABLE=$(aws cloudformation describe-stacks \
    --stack-name "${STACK_NAME}" --region "${REGION}" \
    --query 'Stacks[0].Outputs[?OutputKey==`DynamoDBTableName`].OutputValue' \
    --output text)
aws dynamodb delete-table --table-name "${TABLE}" --region "${REGION}"
```

---

## Interview Change Scenarios

The codebase is deliberately structured to make live changes easy.

| Requested change | What to modify |
|---|---|
| Add a third subnet | No code change — the subnets array is already data-driven |
| Auto-select AZs | Add `DescribeAvailabilityZones` in `validation.py` before AZ check |
| Only creator can read | Compare `sub` claim against `requestedBy` in `get_vpc/app.py`; return 403 |
| Admin-only provisioning | Add Cognito group check in `create_vpc/app.py`; add group to SAM template |
| Delete API | Add `DELETE /vpcs/{id}` Lambda + a deletion state machine in SAM |
| Terraform instead of SAM | Keep all Python handlers unchanged; replace `template.yaml` |
| Multi-account | Assume cross-account role in Step Functions; add `accountId` to model |

---

## Known Limitations and Next Steps

- Subnet deletion in the compensation path uses `DescribeSubnets` via the state machine. For very rapid failures, a small window exists where subnets may not yet be visible to Describe. In practice, EC2 APIs are consistent enough for this to be non-issue.
- The `PersistSucceeded` state serializes the subnet list as a JSON string via `States.JsonToString`. A production system should use a dedicated Lambda for the persist step to store typed DynamoDB Lists.
- No IGW, route tables, or NAT Gateway — intentionally out of scope per the design document.
- Deletion API is not implemented in the core; the cleanup script handles manual post-demo teardown.
- Cognito MFA is configurable in the User Pool but not enforced for the demo environment.
