#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# create_demo_user.sh
#
# Creates a Cognito user and sets a permanent password so you can obtain
# a JWT for demo and testing without going through the hosted UI.
#
# Usage:
#   ./scripts/create_demo_user.sh \
#       --user-pool-id  us-east-1_XXXXXXXXX \
#       --username      demo@example.com \
#       --password      "Demo@Password1!" \
#       --region        ap-south-1
#
# Prerequisites:
#   - AWS CLI v2 configured with credentials that have
#     cognito-idp:AdminCreateUser and cognito-idp:AdminSetUserPassword
#   - The Cognito User Pool must already exist (deployed via SAM)
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

# ── Parse arguments ───────────────────────────────────────────────────────────
USER_POOL_ID=""
USERNAME=""
PASSWORD=""
REGION="ap-south-1"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --user-pool-id)  USER_POOL_ID="$2";  shift 2 ;;
    --username)      USERNAME="$2";      shift 2 ;;
    --password)      PASSWORD="$2";      shift 2 ;;
    --region)        REGION="$2";        shift 2 ;;
    *) echo "Unknown argument: $1"; exit 1 ;;
  esac
done

# ── Validate ──────────────────────────────────────────────────────────────────
if [[ -z "$USER_POOL_ID" || -z "$USERNAME" || -z "$PASSWORD" ]]; then
  echo "Error: --user-pool-id, --username, and --password are required."
  exit 1
fi

echo "Creating Cognito user: ${USERNAME} in pool: ${USER_POOL_ID} (region: ${REGION})"

# ── Create user (suppress temporary password prompt) ─────────────────────────
aws cognito-idp admin-create-user \
  --region          "${REGION}" \
  --user-pool-id    "${USER_POOL_ID}" \
  --username        "${USERNAME}" \
  --user-attributes Name=email,Value="${USERNAME}" Name=email_verified,Value=true \
  --message-action  SUPPRESS \
  --output table 2>&1 | grep -v "TemporaryPassword" || true

# ── Set permanent password (skips FORCE_CHANGE_PASSWORD) ─────────────────────
aws cognito-idp admin-set-user-password \
  --region       "${REGION}" \
  --user-pool-id "${USER_POOL_ID}" \
  --username     "${USERNAME}" \
  --password     "${PASSWORD}" \
  --permanent

echo ""
echo "✓ User '${USERNAME}' created and password set."
echo "  Run ./scripts/get_token.sh to obtain a JWT."
