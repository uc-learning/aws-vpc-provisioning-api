"""
GET /vpcs/{requestId}  — Retrieve VPC provisioning status Lambda handler.

Responsibilities:
  1. Load AppConfig (reads from Secrets Manager on cold start, cached thereafter).
  2. Extract requestId from the path parameter.
  3. Perform a single consistent DynamoDB GetItem.
  4. Map the raw DynamoDB item to the public VpcStatusResponse model.
  5. Return 200 OK or 404 Not Found.

This Lambda is read-only: it requires only dynamodb:GetItem permission.
"""
from __future__ import annotations

from typing import Any

from common import logging as log_module
from common import repository, responses
from common.config import get_config
from common.models import ProvisioningStatus, SubnetResult, VpcResult, VpcStatusResponse

# ── Cold-start initialisation ─────────────────────────────────────────────────

_config      = get_config()
_base_logger = log_module.get_logger("get_vpc")


# ── Mapping helpers ───────────────────────────────────────────────────────────


def _map_item_to_response(item: dict[str, Any]) -> dict[str, Any]:
    """
    Map a raw DynamoDB item to the VpcStatusResponse shape.

    DynamoDB stores nested maps for 'vpc' and 'subnets'.
    We run them through Pydantic so callers always receive a well-typed response.
    """
    vpc_data     = item.get("vpc")
    subnets_data = item.get("subnets", [])

    vpc_result: VpcResult | None = None
    if vpc_data and isinstance(vpc_data, dict):
        vpc_result = VpcResult.model_validate(vpc_data)

    subnet_results: list[SubnetResult] = []
    if subnets_data and isinstance(subnets_data, list):
        for s in subnets_data:
            if isinstance(s, dict):
                subnet_results.append(SubnetResult.model_validate(s))

    response = VpcStatusResponse(
        requestId=item["requestId"],
        status=ProvisioningStatus(item["status"]),
        requestedBy=item.get("requestedBy"),
        createdAt=item.get("createdAt"),
        updatedAt=item.get("updatedAt"),
        vpc=vpc_result,
        subnets=subnet_results,
        error=item.get("error"),
    )
    return response.model_dump(exclude_none=True)


# ── Main handler ──────────────────────────────────────────────────────────────


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:  # noqa: ANN401
    """
    Lambda entry point for GET /vpcs/{requestId}.

    Error taxonomy:
      400 — Missing requestId path parameter.
      404 — requestId not found in DynamoDB.
      500 — Unexpected internal error.
    """
    path_params: dict[str, str] = event.get("pathParameters") or {}
    request_id = path_params.get("requestId", "").strip()

    log = log_module.RequestLogger("get_vpc", request_id=request_id or None)

    if not request_id:
        log.warning("Missing requestId path parameter")
        return responses.bad_request("requestId path parameter is required")

    log.info("Received GET /vpcs/{requestId}", environment=_config.environment)

    try:
        item = repository.get_request(request_id, table_name=_config.dynamodb_table)
    except repository.RequestNotFoundError:
        log.warning("Request not found")
        return responses.not_found(f"Request '{request_id}' not found")
    except Exception as exc:  # noqa: BLE001
        log.exception("DynamoDB get_request failed", error=str(exc))
        return responses.internal_error()

    try:
        response_body = _map_item_to_response(item)
    except Exception as exc:  # noqa: BLE001
        log.exception("Failed to map DynamoDB item to response", error=str(exc))
        return responses.internal_error("Failed to format response")

    log.info("Returning provisioning status", status=item.get("status"))
    return responses.ok(response_body)
