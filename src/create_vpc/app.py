"""
POST /vpcs  — Create VPC Lambda handler.

Responsibilities (thin handler, all logic delegated to common modules):
  1. Extract and validate the authenticated Cognito identity from the JWT claims
     injected by API Gateway's Cognito authorizer.
  2. Load AppConfig (reads limits/allow-lists from Secrets Manager on cold start).
  3. Parse and validate the request body (Pydantic + config-driven CIDR/AZ rules).
  4. Enforce idempotency via clientRequestId / request hash.
  5. Persist a PENDING record in DynamoDB.
  6. Start the Step Functions provisioning workflow.
  7. Attach the executionArn to the DynamoDB record.
  8. Return 202 Accepted with requestId and a statusUrl.

This Lambda has NO EC2 permissions; all VPC/subnet creation happens in
the Step Functions state machine role.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

import boto3
from pydantic import ValidationError as PydanticValidationError

from common import logging as log_module
from common import repository, responses
from common.config import get_config
from common.models import CreateVpcRequest, ProvisioningStatus, WorkflowInput
from common.validation import ValidationError, validate_create_request

# ── Cold-start initialisation ─────────────────────────────────────────────────
# AppConfig is built once per cold start. It reads from Secrets Manager and
# caches the result. All warm invocations reuse the cached config.

_config = get_config()

# Module-level Step Functions client reused across warm invocations
_sfn_client = boto3.client("stepfunctions", region_name=_config.aws_region)

_base_logger = log_module.get_logger("create_vpc")


# ── Identity extraction ───────────────────────────────────────────────────────


def _get_authenticated_subject(event: dict[str, Any]) -> str:
    """
    Extract the Cognito 'sub' claim injected by API Gateway's Cognito authorizer.
    Raises ValueError if the claim is absent (defensive; authorizer should catch this).
    """
    try:
        claims: dict[str, str] = event["requestContext"]["authorizer"]["claims"]
        sub = claims["sub"]
        if not sub:
            raise ValueError("sub claim is empty")
        return sub
    except (KeyError, TypeError) as exc:
        raise ValueError(f"Unable to extract Cognito sub from request context: {exc}") from exc


# ── Request parsing ───────────────────────────────────────────────────────────


def _parse_body(raw_body: str | None) -> CreateVpcRequest:
    """
    Deserialise and Pydantic-validate the JSON request body.
    Raises ValueError for missing/non-JSON body.
    Raises PydanticValidationError for schema violations.
    """
    if not raw_body:
        raise ValueError("Request body is required")
    data = json.loads(raw_body)
    if not isinstance(data, dict):
        raise ValueError("Request body must be a JSON object")
    return CreateVpcRequest.model_validate(data)


# ── Workflow helpers ──────────────────────────────────────────────────────────


def _build_workflow_input(request_id: str, request: CreateVpcRequest) -> dict[str, Any]:
    """Build the Step Functions execution input from the validated request."""
    workflow = WorkflowInput(
        requestId=request_id,
        cidrBlock=request.cidrBlock,
        enableDnsSupport=request.enableDnsSupport,
        enableDnsHostnames=request.enableDnsHostnames,
        vpcName=request.name,
        subnets=request.subnets,
        tags=request.tags,
        tableName=_config.dynamodb_table,
        region=_config.aws_region,
    )
    return json.loads(workflow.model_dump_json())


def _start_workflow(request_id: str, workflow_input: dict[str, Any]) -> str:
    """Start a Step Functions Standard execution and return the executionArn."""
    response = _sfn_client.start_execution(
        stateMachineArn=_config.state_machine_arn,
        name=f"vpc-{request_id}",
        input=json.dumps(workflow_input),
    )
    return response["executionArn"]


# ── Main handler ──────────────────────────────────────────────────────────────


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:  # noqa: ANN401
    """
    Lambda entry point for POST /vpcs.

    Error taxonomy:
      400 — Invalid request body (schema, CIDR, overlap, AZ, limits from config).
      401 — Missing/invalid identity (API Gateway catches this first normally).
      409 — Duplicate clientRequestId with different payload.
      500 — Unexpected internal error (details in CloudWatch, not in response).
    """
    log = log_module.RequestLogger("create_vpc")
    log.info("Received POST /vpcs", environment=_config.environment)

    # ── 1. Extract authenticated identity ─────────────────────────────────────
    try:
        subject = _get_authenticated_subject(event)
    except ValueError as exc:
        log.error("Failed to extract Cognito identity", error=str(exc))
        return responses.unauthorized("Authentication context is missing or invalid")

    log = log_module.RequestLogger("create_vpc", requested_by=subject)

    # ── 2. Parse and validate request body (Pydantic) ─────────────────────────
    try:
        request = _parse_body(event.get("body"))
    except (ValueError, json.JSONDecodeError) as exc:
        log.warning("Request body parse error", error=str(exc))
        return responses.bad_request(str(exc))
    except PydanticValidationError as exc:
        errors = exc.errors()
        first = errors[0]
        field = ".".join(str(loc) for loc in first["loc"])
        message = f"Validation error on field '{field}': {first['msg']}"
        log.warning("Pydantic validation error", error_count=len(errors))
        return responses.bad_request(message, field=field)

    log = log_module.RequestLogger(
        "create_vpc",
        requested_by=subject,
        client_request_id=request.clientRequestId,
    )

    # ── 3. Config-driven cross-field validation (CIDR, overlap, AZ, limits) ───
    #   max_subnets_per_request is enforced in the Pydantic model but the
    #   model uses a module constant; re-check here against the live config value.
    if len(request.subnets) > _config.max_subnets_per_request:
        return responses.bad_request(
            f"At most {_config.max_subnets_per_request} subnets are allowed "
            f"in this environment ({_config.environment})",
            field="subnets",
        )

    try:
        validate_create_request(request, region=_config.aws_region, config=_config)
    except ValidationError as exc:
        log.warning("Domain validation failed", error=exc.message, field=exc.field)
        return responses.bad_request(exc.message, field=exc.field)

    # ── 4. Generate canonical requestId and payload hash ──────────────────────
    request_id   = str(uuid.uuid4())
    request_hash = repository.compute_request_hash(json.loads(request.model_dump_json()))

    log = log_module.RequestLogger(
        "create_vpc",
        request_id=request_id,
        client_request_id=request.clientRequestId,
        requested_by=subject,
    )

    # ── 5. Idempotency check + DynamoDB write ──────────────────────────────────
    try:
        existing = repository.create_if_absent(
            request_id=request_id,
            client_request_id=request.clientRequestId,
            request_hash=request_hash,
            requested_by=subject,
            request_body=json.loads(request.model_dump_json()),
            table_name=_config.dynamodb_table,
        )
    except repository.DuplicateRequestError as exc:
        log.warning(
            "Duplicate clientRequestId with different payload",
            existing_request_id=exc.existing_request_id,
        )
        return responses.conflict(
            "A request with this clientRequestId already exists with a different payload",
            existing_request_id=exc.existing_request_id,
        )
    except Exception as exc:  # noqa: BLE001
        log.exception("DynamoDB create_if_absent failed", error=str(exc))
        return responses.internal_error()

    if existing:
        # Idempotent replay — return the original requestId and its current status
        existing_request_id = existing["requestId"]
        existing_status     = existing.get("status", ProvisioningStatus.PENDING.value)
        log.info(
            "Idempotent replay — returning existing request",
            existing_request_id=existing_request_id,
            existing_status=existing_status,
        )
        return responses.accepted({
            "requestId": existing_request_id,
            "status":    existing_status,
            "statusUrl": f"/vpcs/{existing_request_id}",
        })

    # ── 6. Start Step Functions execution ──────────────────────────────────────
    try:
        workflow_input = _build_workflow_input(request_id, request)
        execution_arn  = _start_workflow(request_id, workflow_input)
    except Exception as exc:  # noqa: BLE001
        log.exception("Failed to start Step Functions execution", error=str(exc))
        # Mark the PENDING record as FAILED so it doesn't linger
        try:
            repository.update_status(
                request_id,
                new_status=ProvisioningStatus.FAILED,
                expected_status=ProvisioningStatus.PENDING,
                extra_attrs={
                    "error": {
                        "code":    "WORKFLOW_START_FAILED",
                        "message": "Failed to start provisioning workflow",
                    }
                },
                table_name=_config.dynamodb_table,
            )
        except Exception:  # noqa: BLE001
            pass
        return responses.internal_error("Failed to start provisioning workflow")

    # ── 7. Attach executionArn to the DynamoDB record (best-effort) ───────────
    try:
        repository.attach_execution(
            request_id, execution_arn, table_name=_config.dynamodb_table
        )
    except Exception as exc:  # noqa: BLE001
        # Non-fatal: workflow is running; log and continue
        log.warning("Failed to attach executionArn to DynamoDB record", error=str(exc))

    log.info(
        "Provisioning request accepted",
        execution_arn=execution_arn,
        status=ProvisioningStatus.PENDING.value,
    )

    return responses.accepted({
        "requestId": request_id,
        "status":    ProvisioningStatus.PENDING.value,
        "statusUrl": f"/vpcs/{request_id}",
    })
