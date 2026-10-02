"""
DynamoDB repository for VPC provisioning request lifecycle.

Design choices:
- All writes use ConditionExpressions to prevent silent overwrites.
- The create_if_absent method enforces idempotency via the clientRequestId GSI
  and a normalized request hash.
- Status transitions use conditional expressions to prevent backward transitions.
- No business logic lives here; this module is pure data access.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Any

import boto3
from boto3.dynamodb.conditions import Attr, Key
from botocore.exceptions import ClientError

from .models import ProvisioningStatus

# ── Configuration ─────────────────────────────────────────────────────────────

TABLE_NAME = os.environ.get("DYNAMODB_TABLE", "VpcProvisioningRequests")
REGION = os.environ.get("AWS_REGION", "ap-south-1")


# ── Exceptions ────────────────────────────────────────────────────────────────


class DuplicateRequestError(Exception):
    """Raised when a clientRequestId already exists with a *different* payload hash."""

    def __init__(self, existing_request_id: str) -> None:
        super().__init__(f"Duplicate clientRequestId with different payload; existing requestId={existing_request_id}")
        self.existing_request_id = existing_request_id


class RequestNotFoundError(Exception):
    """Raised when a requestId does not exist in the table."""

    def __init__(self, request_id: str) -> None:
        super().__init__(f"Request '{request_id}' not found")
        self.request_id = request_id


# ── Helpers ───────────────────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def compute_request_hash(request_body: dict[str, Any]) -> str:
    """
    Deterministic SHA-256 hash of the normalized request body.
    Used to detect same-payload vs different-payload retries on the same clientRequestId.
    clientRequestId itself is excluded from the hash.
    """
    normalized = {k: v for k, v in sorted(request_body.items()) if k != "clientRequestId"}
    canonical = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _get_table(table_name: str = TABLE_NAME):
    dynamodb = boto3.resource("dynamodb", region_name=REGION)
    return dynamodb.Table(table_name)


# ── Repository functions ──────────────────────────────────────────────────────


def create_if_absent(
    request_id: str,
    client_request_id: str | None,
    request_hash: str,
    requested_by: str,
    request_body: dict[str, Any],
    table_name: str = TABLE_NAME,
) -> dict[str, Any] | None:
    """
    Attempt to create a new provisioning request record.

    Idempotency logic:
    - If clientRequestId is None: always insert a new record (no idempotency key).
    - If clientRequestId already exists with the SAME hash: return the existing item
      (idempotent replay).
    - If clientRequestId already exists with a DIFFERENT hash: raise DuplicateRequestError.
    - Otherwise: insert and return None (caller should proceed).

    Returns the existing DynamoDB item if this is an idempotent replay, else None.
    """
    table = _get_table(table_name)
    now = _now_iso()

    if client_request_id:
        # Query for existing record by clientRequestId using the GSI
        existing = _find_by_client_request_id(client_request_id, table_name)
        if existing:
            if existing.get("requestHash") == request_hash:
                # Idempotent replay — return existing record
                return existing
            else:
                raise DuplicateRequestError(existing["requestId"])

    item: dict[str, Any] = {
        "requestId": request_id,
        "status": ProvisioningStatus.PENDING.value,
        "requestedBy": requested_by,
        "requestHash": request_hash,
        "request": request_body,
        "createdAt": now,
        "updatedAt": now,
    }
    if client_request_id:
        item["clientRequestId"] = client_request_id

    try:
        table.put_item(
            Item=item,
            ConditionExpression=Attr("requestId").not_exists(),
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            # Race condition: another process created the same requestId
            # (UUID collision is astronomically unlikely; treat as internal error)
            raise RuntimeError(f"requestId collision for {request_id}") from exc
        raise

    return None  # New record created; caller should proceed


def attach_execution(
    request_id: str,
    execution_arn: str,
    table_name: str = TABLE_NAME,
) -> None:
    """Attach a Step Functions executionArn to an existing PENDING request record."""
    table = _get_table(table_name)
    table.update_item(
        Key={"requestId": request_id},
        UpdateExpression="SET executionArn = :arn, updatedAt = :now",
        ExpressionAttributeValues={
            ":arn": execution_arn,
            ":now": _now_iso(),
        },
        ConditionExpression=Attr("requestId").exists(),
    )


def update_status(
    request_id: str,
    new_status: ProvisioningStatus,
    expected_status: ProvisioningStatus,
    extra_attrs: dict[str, Any] | None = None,
    table_name: str = TABLE_NAME,
) -> None:
    """
    Transition request status with an optimistic condition check.

    extra_attrs allows the Step Functions task (via Lambda or SDK integration)
    to write vpc/subnets/error data in a single call.
    """
    table = _get_table(table_name)
    now = _now_iso()

    update_expr = "SET #st = :new_status, updatedAt = :now"
    expr_names: dict[str, str] = {"#st": "status"}
    expr_values: dict[str, Any] = {
        ":new_status": new_status.value,
        ":expected_status": expected_status.value,
        ":now": now,
    }

    if extra_attrs:
        for idx, (attr_name, attr_value) in enumerate(extra_attrs.items()):
            placeholder_name = f"#attr{idx}"
            placeholder_value = f":val{idx}"
            update_expr += f", {placeholder_name} = {placeholder_value}"
            expr_names[placeholder_name] = attr_name
            expr_values[placeholder_value] = attr_value

    table.update_item(
        Key={"requestId": request_id},
        UpdateExpression=update_expr,
        ConditionExpression=Attr("status").eq(":expected_status"),
        ExpressionAttributeNames=expr_names,
        ExpressionAttributeValues=expr_values,
    )


def get_request(
    request_id: str,
    table_name: str = TABLE_NAME,
) -> dict[str, Any]:
    """
    Retrieve a request record by requestId (PK).
    Raises RequestNotFoundError if not found.
    """
    table = _get_table(table_name)
    response = table.get_item(
        Key={"requestId": request_id},
        ConsistentRead=True,
    )
    item = response.get("Item")
    if not item:
        raise RequestNotFoundError(request_id)
    return item


def _find_by_client_request_id(
    client_request_id: str,
    table_name: str = TABLE_NAME,
) -> dict[str, Any] | None:
    """
    Query the ClientRequestIdIndex GSI for an existing record.
    Returns the first matching item or None.
    """
    table = _get_table(table_name)
    response = table.query(
        IndexName="ClientRequestIdIndex",
        KeyConditionExpression=Key("clientRequestId").eq(client_request_id),
        Limit=1,
    )
    items = response.get("Items", [])
    return items[0] if items else None
