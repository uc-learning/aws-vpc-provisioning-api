#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# smoke_test.sh
#
# Quick end-to-end smoke test against a deployed stack.
# Authenticates, submits one VPC request, polls until SUCCEEDED or FAILED,
# and prints a PASS / FAIL summary.
#
# Usage:
#   ./scripts/smoke_test.sh \
#       --api-endpoint  https://abc123.execute-api.ap-south-1.amazonaws.com/demo \
#       --client-id     <cognito-app-client-id> \
#       --username      demo@example.com \
#       --password      "Demo@Password1!" \
#       --region        ap-south-1
#
# Exit codes:
#   0 — all checks passed
#   1 — one or more checks failed
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

# ── Argument parsing ──────────────────────────────────────────────────────────
API_ENDPOINT=""
CLIENT_ID=""
USERNAME=""
PASSWORD=""
REGION="ap-south-1"
POLL_INTERVAL=5
MAX_POLLS=24   # 2 minutes

while [[ $# -gt 0 ]]; do
  case "$1" in
    --api-endpoint) API_ENDPOINT="$2"; shift 2 ;;
    --client-id)    CLIENT_ID="$2";    shift 2 ;;
    --username)     USERNAME="$2";     shift 2 ;;
    --password)     PASSWORD="$2";     shift 2 ;;
    --region)       REGION="$2";       shift 2 ;;
    *) echo "Unknown argument: $1" >&2; exit 1 ;;
  esac
done

for var in API_ENDPOINT CLIENT_ID USERNAME PASSWORD; do
  if [[ -z "${!var}" ]]; then
    echo "Error: --${var,,} is required." >&2
    exit 1
  fi
done

API_ENDPOINT="${API_ENDPOINT%/}"   # remove trailing slash
PASS=0
FAIL=0

_pass() { echo "  [PASS] $1"; ((PASS++)); }
_fail() { echo "  [FAIL] $1"; ((FAIL++)); }
_info() { echo "  [INFO] $1"; }

echo ""
echo "========================================================"
echo "  VPC Provisioning API — Smoke Test"
echo "  Endpoint : ${API_ENDPOINT}"
echo "  Region   : ${REGION}"
echo "========================================================"

# ── 1. Health check (unauthenticated) ─────────────────────────────────────────
echo ""
echo "1. Health check"
HEALTH_STATUS=$(curl -s -o /dev/null -w "%{http_code}" "${API_ENDPOINT}/health")
if [[ "${HEALTH_STATUS}" == "200" ]]; then
  _pass "GET /health returned 200"
else
  _fail "GET /health returned ${HEALTH_STATUS} (expected 200)"
fi

# ── 2. Unauthenticated POST must return 401 ───────────────────────────────────
echo ""
echo "2. Unauthenticated request rejection"
UNAUTH_STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
  -X POST "${API_ENDPOINT}/vpcs" \
  -H "Content-Type: application/json" \
  -d '{"name":"test","cidrBlock":"10.0.0.0/16","subnets":[]}')
if [[ "${UNAUTH_STATUS}" == "401" ]]; then
  _pass "POST /vpcs without token returned 401"
else
  _fail "POST /vpcs without token returned ${UNAUTH_STATUS} (expected 401)"
fi

# ── 3. Obtain JWT ─────────────────────────────────────────────────────────────
echo ""
echo "3. Obtaining Cognito token"
TOKEN=$(./scripts/get_token.sh \
  --client-id "${CLIENT_ID}" \
  --username  "${USERNAME}" \
  --password  "${PASSWORD}" \
  --region    "${REGION}" 2>/dev/null)

if [[ -n "${TOKEN}" ]]; then
  _pass "Token obtained successfully"
else
  _fail "Failed to obtain Cognito token"
  echo ""
  echo "Smoke test aborted — cannot continue without a token."
  exit 1
fi

AUTH_HEADER="Authorization: Bearer ${TOKEN}"

# ── 4. Invalid CIDR → 400 ─────────────────────────────────────────────────────
echo ""
echo "4. Input validation — invalid CIDR"
BAD_CIDR_STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
  -X POST "${API_ENDPOINT}/vpcs" \
  -H "Content-Type: application/json" \
  -H "${AUTH_HEADER}" \
  -d '{"name":"bad","cidrBlock":"999.999.0.0/16","subnets":[{"name":"a","cidrBlock":"10.0.1.0/24","availabilityZone":"ap-south-1a"}]}')
if [[ "${BAD_CIDR_STATUS}" == "400" ]]; then
  _pass "POST with invalid CIDR returned 400"
else
  _fail "POST with invalid CIDR returned ${BAD_CIDR_STATUS} (expected 400)"
fi

# ── 5. Unknown requestId → 404 ────────────────────────────────────────────────
echo ""
echo "5. Not found"
NOT_FOUND_STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
  -H "${AUTH_HEADER}" \
  "${API_ENDPOINT}/vpcs/00000000-0000-0000-0000-000000000000")
if [[ "${NOT_FOUND_STATUS}" == "404" ]]; then
  _pass "GET unknown requestId returned 404"
else
  _fail "GET unknown requestId returned ${NOT_FOUND_STATUS} (expected 404)"
fi

# ── 6. Happy path — create and poll ───────────────────────────────────────────
echo ""
echo "6. Happy path — create VPC with two subnets"

CLIENT_REQ_ID="smoke-$(date +%s)"
PAYLOAD=$(cat <<EOF
{
  "clientRequestId": "${CLIENT_REQ_ID}",
  "name": "smoke-test-vpc",
  "cidrBlock": "10.96.0.0/16",
  "enableDnsSupport": true,
  "enableDnsHostnames": true,
  "subnets": [
    {"name": "smoke-a", "cidrBlock": "10.96.1.0/24", "availabilityZone": "${REGION}a"},
    {"name": "smoke-b", "cidrBlock": "10.96.2.0/24", "availabilityZone": "${REGION}b"}
  ],
  "tags": {"Purpose": "smoke-test"}
}
EOF
)

POST_RESPONSE=$(curl -s -w "\n%{http_code}" \
  -X POST "${API_ENDPOINT}/vpcs" \
  -H "Content-Type: application/json" \
  -H "${AUTH_HEADER}" \
  -d "${PAYLOAD}")
POST_BODY=$(echo "${POST_RESPONSE}" | head -n -1)
POST_STATUS=$(echo "${POST_RESPONSE}" | tail -n 1)

if [[ "${POST_STATUS}" == "202" ]]; then
  _pass "POST /vpcs returned 202"
else
  _fail "POST /vpcs returned ${POST_STATUS} (expected 202): ${POST_BODY}"
  echo ""
  echo "Smoke test cannot continue without a valid request."
  echo "PASSED: ${PASS}  FAILED: ${FAIL}"
  exit 1
fi

REQUEST_ID=$(echo "${POST_BODY}" | python3 -c "import sys,json; print(json.load(sys.stdin)['requestId'])")
_info "requestId: ${REQUEST_ID}"

# ── 7. Poll until terminal status ─────────────────────────────────────────────
echo ""
echo "7. Polling GET /vpcs/${REQUEST_ID}"
TERMINAL=""
for i in $(seq 1 "${MAX_POLLS}"); do
  GET_RESPONSE=$(curl -s \
    -H "${AUTH_HEADER}" \
    "${API_ENDPOINT}/vpcs/${REQUEST_ID}")
  STATUS=$(echo "${GET_RESPONSE}" | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])" 2>/dev/null || echo "UNKNOWN")
  _info "Attempt ${i}/${MAX_POLLS}: status = ${STATUS}"

  case "${STATUS}" in
    SUCCEEDED|FAILED|ROLLED_BACK) TERMINAL="${STATUS}"; break ;;
  esac
  sleep "${POLL_INTERVAL}"
done

if [[ "${TERMINAL}" == "SUCCEEDED" ]]; then
  _pass "Provisioning reached SUCCEEDED"
  VPC_ID=$(echo "${GET_RESPONSE}" | python3 -c "import sys,json; print(json.load(sys.stdin)['vpc']['vpcId'])" 2>/dev/null || echo "N/A")
  SUBNET_COUNT=$(echo "${GET_RESPONSE}" | python3 -c "import sys,json; print(len(json.load(sys.stdin)['subnets']))" 2>/dev/null || echo "0")
  _info "VPC ID       : ${VPC_ID}"
  _info "Subnet count : ${SUBNET_COUNT}"
elif [[ -n "${TERMINAL}" ]]; then
  _fail "Provisioning reached ${TERMINAL} (expected SUCCEEDED)"
else
  _fail "Provisioning did not reach a terminal status within timeout"
fi

# ── 8. Idempotency — replay same clientRequestId ──────────────────────────────
echo ""
echo "8. Idempotency replay"
REPLAY_RESPONSE=$(curl -s -w "\n%{http_code}" \
  -X POST "${API_ENDPOINT}/vpcs" \
  -H "Content-Type: application/json" \
  -H "${AUTH_HEADER}" \
  -d "${PAYLOAD}")
REPLAY_BODY=$(echo "${REPLAY_RESPONSE}" | head -n -1)
REPLAY_STATUS=$(echo "${REPLAY_RESPONSE}" | tail -n 1)
REPLAY_ID=$(echo "${REPLAY_BODY}" | python3 -c "import sys,json; print(json.load(sys.stdin)['requestId'])" 2>/dev/null || echo "")

if [[ "${REPLAY_STATUS}" == "202" && "${REPLAY_ID}" == "${REQUEST_ID}" ]]; then
  _pass "Idempotent replay returned same requestId"
else
  _fail "Idempotent replay failed: status=${REPLAY_STATUS} requestId=${REPLAY_ID} (expected ${REQUEST_ID})"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "========================================================"
echo "  PASSED: ${PASS}   FAILED: ${FAIL}"
echo "========================================================"
echo ""

if [[ "${FAIL}" -gt 0 ]]; then
  exit 1
fi
