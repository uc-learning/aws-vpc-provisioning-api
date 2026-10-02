# API Examples

Complete request/response examples for every endpoint.

## Prerequisites

```bash
# Set these once in your shell session
export API_ENDPOINT="https://<api-id>.execute-api.ap-south-1.amazonaws.com/demo"
export COGNITO_TOKEN=$(./scripts/get_token.sh \
    --client-id  <cognito-app-client-id> \
    --username   demo@example.com \
    --password   "Demo@Password1!" \
    --region     ap-south-1)
```

---

## GET /health — Liveness probe (no auth required)

```bash
curl -s "${API_ENDPOINT}/health" | python3 -m json.tool
```

**Response 200 OK**
```json
{
  "status": "ok",
  "service": "vpc-provisioning-api",
  "environment": "demo"
}
```

---

## POST /vpcs — Submit a provisioning request

```bash
curl -s -X POST "${API_ENDPOINT}/vpcs" \
  -H "Authorization: Bearer ${COGNITO_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "clientRequestId": "demo-20260928-001",
    "name": "interview-demo-vpc",
    "cidrBlock": "10.20.0.0/16",
    "enableDnsSupport": true,
    "enableDnsHostnames": true,
    "subnets": [
      {
        "name": "app-a",
        "cidrBlock": "10.20.1.0/24",
        "availabilityZone": "ap-south-1a"
      },
      {
        "name": "app-b",
        "cidrBlock": "10.20.2.0/24",
        "availabilityZone": "ap-south-1b"
      }
    ],
    "tags": {
      "Environment": "demo",
      "Owner": "aws-challenge"
    }
  }' | python3 -m json.tool
```

**Response 202 Accepted**
```json
{
  "requestId": "8e0b5a9a-3c1f-4d2b-a7e6-0f123456789a",
  "status": "PENDING",
  "statusUrl": "/vpcs/8e0b5a9a-3c1f-4d2b-a7e6-0f123456789a"
}
```

### Error cases

**400 Bad Request — invalid CIDR**
```bash
curl -s -X POST "${API_ENDPOINT}/vpcs" \
  -H "Authorization: Bearer ${COGNITO_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"name":"bad","cidrBlock":"999.0.0.0/16","subnets":[{"name":"a","cidrBlock":"10.0.1.0/24","availabilityZone":"ap-south-1a"}]}'
```
```json
{
  "error": "BAD_REQUEST",
  "message": "'999.0.0.0/16' is not a valid IPv4 CIDR: ...",
  "field": "cidrBlock"
}
```

**400 Bad Request — overlapping subnets**
```bash
curl -s -X POST "${API_ENDPOINT}/vpcs" \
  -H "Authorization: Bearer ${COGNITO_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "overlap-test",
    "cidrBlock": "10.50.0.0/16",
    "subnets": [
      {"name": "a", "cidrBlock": "10.50.1.0/24",   "availabilityZone": "ap-south-1a"},
      {"name": "b", "cidrBlock": "10.50.1.128/25",  "availabilityZone": "ap-south-1b"}
    ]
  }'
```
```json
{
  "error": "BAD_REQUEST",
  "message": "Subnet 'a' (10.50.1.0/24) overlaps with subnet 'b' (10.50.1.128/25)",
  "field": "subnets"
}
```

**401 Unauthorized — missing token**
```bash
curl -s -X POST "${API_ENDPOINT}/vpcs" \
  -H "Content-Type: application/json" \
  -d '{"name":"test","cidrBlock":"10.0.0.0/16","subnets":[]}'
```
```json
{
  "error": "UNAUTHORIZED",
  "message": "Authentication required"
}
```

**409 Conflict — same clientRequestId, different payload**
```json
{
  "error": "CONFLICT",
  "message": "A request with this clientRequestId already exists with a different payload",
  "existingRequestId": "8e0b5a9a-3c1f-4d2b-a7e6-0f123456789a"
}
```

---

## GET /vpcs/{requestId} — Retrieve provisioning status

```bash
export REQUEST_ID="8e0b5a9a-3c1f-4d2b-a7e6-0f123456789a"

curl -s "${API_ENDPOINT}/vpcs/${REQUEST_ID}" \
  -H "Authorization: Bearer ${COGNITO_TOKEN}" | python3 -m json.tool
```

**Response 200 — PENDING (workflow not yet started)**
```json
{
  "requestId": "8e0b5a9a-3c1f-4d2b-a7e6-0f123456789a",
  "status": "PENDING",
  "requestedBy": "cognito-sub-abc123",
  "createdAt": "2026-09-28T14:10:00Z",
  "updatedAt": "2026-09-28T14:10:00Z"
}
```

**Response 200 — PROVISIONING (workflow running)**
```json
{
  "requestId": "8e0b5a9a-3c1f-4d2b-a7e6-0f123456789a",
  "status": "PROVISIONING",
  "requestedBy": "cognito-sub-abc123",
  "createdAt": "2026-09-28T14:10:00Z",
  "updatedAt": "2026-09-28T14:10:02Z"
}
```

**Response 200 — SUCCEEDED**
```json
{
  "requestId": "8e0b5a9a-3c1f-4d2b-a7e6-0f123456789a",
  "status": "SUCCEEDED",
  "requestedBy": "cognito-sub-abc123",
  "createdAt": "2026-09-28T14:10:00Z",
  "updatedAt": "2026-09-28T14:10:09Z",
  "vpc": {
    "vpcId": "vpc-0123456789abcdef0",
    "cidrBlock": "10.20.0.0/16",
    "state": "available"
  },
  "subnets": [
    {
      "name": "app-a",
      "subnetId": "subnet-0aabbccdd11223344",
      "cidrBlock": "10.20.1.0/24",
      "availabilityZone": "ap-south-1a",
      "state": "available"
    },
    {
      "name": "app-b",
      "subnetId": "subnet-0ffeeddcc99887766",
      "cidrBlock": "10.20.2.0/24",
      "availabilityZone": "ap-south-1b",
      "state": "available"
    }
  ]
}
```

**Response 200 — FAILED**
```json
{
  "requestId": "8e0b5a9a-3c1f-4d2b-a7e6-0f123456789a",
  "status": "FAILED",
  "requestedBy": "cognito-sub-abc123",
  "createdAt": "2026-09-28T14:10:00Z",
  "updatedAt": "2026-09-28T14:11:30Z",
  "error": {
    "code": "PROVISIONING_FAILED",
    "message": "Provisioning encountered an unrecoverable error. Check Step Functions execution history for details."
  }
}
```

**Response 200 — ROLLED_BACK**
```json
{
  "requestId": "8e0b5a9a-3c1f-4d2b-a7e6-0f123456789a",
  "status": "ROLLED_BACK",
  "error": {
    "code": "PROVISIONING_FAILED_COMPENSATED",
    "message": "Provisioning failed; created resources have been removed."
  }
}
```

**Response 404 — Not Found**
```json
{
  "error": "NOT_FOUND",
  "message": "Request '8e0b5a9a-...' not found"
}
```

---

## Idempotency demonstration

```bash
# Send the same request twice — must return the same requestId both times
CLIENT_ID="demo-idempotency-$(date +%s)"
PAYLOAD='{"clientRequestId":"'${CLIENT_ID}'","name":"idem-test","cidrBlock":"10.30.0.0/16","subnets":[{"name":"a","cidrBlock":"10.30.1.0/24","availabilityZone":"ap-south-1a"}]}'

RESP1=$(curl -s -X POST "${API_ENDPOINT}/vpcs" \
  -H "Authorization: Bearer ${COGNITO_TOKEN}" \
  -H "Content-Type: application/json" -d "${PAYLOAD}")

RESP2=$(curl -s -X POST "${API_ENDPOINT}/vpcs" \
  -H "Authorization: Bearer ${COGNITO_TOKEN}" \
  -H "Content-Type: application/json" -d "${PAYLOAD}")

echo "First  requestId: $(echo $RESP1 | python3 -c 'import sys,json; print(json.load(sys.stdin)["requestId"])')"
echo "Second requestId: $(echo $RESP2 | python3 -c 'import sys,json; print(json.load(sys.stdin)["requestId"])')"
# Both lines must print the same UUID.
```

---

## Verify created resources with AWS CLI

```bash
VPC_ID="vpc-0123456789abcdef0"

# Describe VPC
aws ec2 describe-vpcs \
  --region ap-south-1 \
  --vpc-ids "${VPC_ID}" \
  --query "Vpcs[0].{VpcId:VpcId,CidrBlock:CidrBlock,State:State,Tags:Tags}" \
  --output table

# Describe subnets
aws ec2 describe-subnets \
  --region ap-south-1 \
  --filters "Name=vpc-id,Values=${VPC_ID}" \
  --query "Subnets[*].{SubnetId:SubnetId,CidrBlock:CidrBlock,AZ:AvailabilityZone,State:State}" \
  --output table

# Verify tags
aws ec2 describe-vpcs \
  --region ap-south-1 \
  --filters "Name=tag:ManagedBy,Values=vpc-provisioning-api" \
  --query "Vpcs[*].{VpcId:VpcId,Tags:Tags}" \
  --output json
```

---

## View Step Functions execution

```bash
# List recent executions for the state machine
aws stepfunctions list-executions \
  --region ap-south-1 \
  --state-machine-arn "$(aws cloudformation describe-stacks \
      --stack-name aws-vpc-provisioning-api \
      --query 'Stacks[0].Outputs[?OutputKey==`StateMachineArn`].OutputValue' \
      --output text)" \
  --max-results 5 \
  --output table
```

---

## CloudWatch structured log query

```
# Logs Insights query — paste into CloudWatch console
fields @timestamp, level, requestId, message, status
| filter @logStream like /create_vpc/
| sort @timestamp desc
| limit 50
```
