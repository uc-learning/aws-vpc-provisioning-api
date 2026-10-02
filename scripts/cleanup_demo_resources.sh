#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# cleanup_demo_resources.sh
#
# Deletes VPCs and subnets that were created by this service in a given region,
# identified by the ManagedBy=vpc-provisioning-api tag.
#
# CAUTION: This script permanently deletes real AWS resources.
#          Always run with --dry-run first to review what will be deleted.
#
# Usage:
#   # Dry run (no deletions):
#   ./scripts/cleanup_demo_resources.sh --region ap-south-1 --dry-run
#
#   # Live run:
#   ./scripts/cleanup_demo_resources.sh --region ap-south-1
#
#   # Delete only resources for a specific requestId:
#   ./scripts/cleanup_demo_resources.sh --region ap-south-1 --request-id <uuid>
#
# Prerequisites:
#   - AWS CLI v2 with credentials that have ec2:DescribeVpcs,
#     ec2:DescribeSubnets, ec2:DeleteSubnet, ec2:DeleteVpc
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

REGION="ap-south-1"
DRY_RUN=false
REQUEST_ID_FILTER=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --region)     REGION="$2";           shift 2 ;;
    --request-id) REQUEST_ID_FILTER="$2"; shift 2 ;;
    --dry-run)    DRY_RUN=true;           shift ;;
    *) echo "Unknown argument: $1" >&2; exit 1 ;;
  esac
done

if [[ "${DRY_RUN}" == "true" ]]; then
  echo "===== DRY RUN — no resources will be deleted ====="
fi

echo ""
echo "Scanning for VPCs tagged ManagedBy=vpc-provisioning-api in region: ${REGION}"

# Build filter list
FILTERS="Name=tag:ManagedBy,Values=vpc-provisioning-api"
if [[ -n "${REQUEST_ID_FILTER}" ]]; then
  FILTERS="${FILTERS} Name=tag:RequestId,Values=${REQUEST_ID_FILTER}"
fi

# ── Discover VPCs ─────────────────────────────────────────────────────────────
VPC_IDS=$(aws ec2 describe-vpcs \
  --region "${REGION}" \
  --filters ${FILTERS} \
  --query "Vpcs[*].VpcId" \
  --output text)

if [[ -z "${VPC_IDS}" ]]; then
  echo "No matching VPCs found."
  exit 0
fi

echo ""
echo "Found VPCs: ${VPC_IDS}"

for VPC_ID in ${VPC_IDS}; do
  echo ""
  echo "── VPC: ${VPC_ID} ──"

  # Discover subnets inside this VPC with matching tag
  SUBNET_IDS=$(aws ec2 describe-subnets \
    --region "${REGION}" \
    --filters "Name=vpc-id,Values=${VPC_ID}" "Name=tag:ManagedBy,Values=vpc-provisioning-api" \
    --query "Subnets[*].SubnetId" \
    --output text)

  for SUBNET_ID in ${SUBNET_IDS}; do
    if [[ "${DRY_RUN}" == "true" ]]; then
      echo "  [DRY RUN] Would delete subnet: ${SUBNET_ID}"
    else
      echo "  Deleting subnet: ${SUBNET_ID}"
      aws ec2 delete-subnet --region "${REGION}" --subnet-id "${SUBNET_ID}"
      echo "  Deleted subnet: ${SUBNET_ID}"
    fi
  done

  if [[ "${DRY_RUN}" == "true" ]]; then
    echo "  [DRY RUN] Would delete VPC: ${VPC_ID}"
  else
    echo "  Deleting VPC: ${VPC_ID}"
    aws ec2 delete-vpc --region "${REGION}" --vpc-id "${VPC_ID}"
    echo "  Deleted VPC: ${VPC_ID}"
  fi
done

echo ""
if [[ "${DRY_RUN}" == "true" ]]; then
  echo "Dry run complete. Re-run without --dry-run to perform deletions."
else
  echo "Cleanup complete."
fi
