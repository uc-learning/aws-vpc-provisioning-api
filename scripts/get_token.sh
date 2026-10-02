#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# get_token.sh
#
# Authenticates a Cognito user and prints the IdToken (JWT) to stdout.
# The token can be used as the Authorization header value for API calls.
#
# Usage:
#   TOKEN=$(./scripts/get_token.sh \
#       --client-id    <app-client-id> \
#       --username     demo@example.com \
#       --password     "Demo@Password1!" \
#       --region       ap-south-1)
#
# Or source the token into an environment variable:
#   export COGNITO_TOKEN=$(./scripts/get_token.sh --client-id ... --username ... --password ...)
#
# Prerequisites:
#   - AWS CLI v2 (no IAM credentials needed; Cognito USER_PASSWORD_AUTH is public)
#   - The Cognito app client must have ALLOW_USER_PASSWORD_AUTH enabled
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

CLIENT_ID=""
USERNAME=""
PASSWORD=""
REGION="ap-south-1"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --client-id) CLIENT_ID="$2"; shift 2 ;;
    --username)  USERNAME="$2";  shift 2 ;;
    --password)  PASSWORD="$2";  shift 2 ;;
    --region)    REGION="$2";    shift 2 ;;
    *) echo "Unknown argument: $1" >&2; exit 1 ;;
  esac
done

if [[ -z "$CLIENT_ID" || -z "$USERNAME" || -z "$PASSWORD" ]]; then
  echo "Error: --client-id, --username, and --password are required." >&2
  exit 1
fi

RESULT=$(aws cognito-idp initiate-auth \
  --region          "${REGION}" \
  --auth-flow       USER_PASSWORD_AUTH \
  --client-id       "${CLIENT_ID}" \
  --auth-parameters USERNAME="${USERNAME}",PASSWORD="${PASSWORD}" \
  --output          json)

ID_TOKEN=$(echo "${RESULT}" | python3 -c "import sys,json; print(json.load(sys.stdin)['AuthenticationResult']['IdToken'])")

# Print token to stdout — caller can capture or export
echo "${ID_TOKEN}"
