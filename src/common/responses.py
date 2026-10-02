"""
HTTP response helpers for API Gateway Lambda proxy integration.

All functions return a dict shaped as the Lambda proxy integration contract:
{statusCode, headers, body (JSON string)}.
"""
from __future__ import annotations

import json
from typing import Any

_COMMON_HEADERS = {
    "Content-Type": "application/json",
    "X-Content-Type-Options": "nosniff",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
}


def _build(status_code: int, body: Any) -> dict[str, Any]:
    return {
        "statusCode": status_code,
        "headers": _COMMON_HEADERS,
        "body": json.dumps(body, default=str),
    }


# ── Success responses ─────────────────────────────────────────────────────────


def accepted(body: dict[str, Any]) -> dict[str, Any]:
    """202 Accepted — returned after a provisioning request is started."""
    return _build(202, body)


def ok(body: dict[str, Any]) -> dict[str, Any]:
    """200 OK — returned for GET requests."""
    return _build(200, body)


# ── Client error responses ────────────────────────────────────────────────────


def bad_request(message: str, field: str | None = None) -> dict[str, Any]:
    """400 Bad Request."""
    body: dict[str, Any] = {"error": "BAD_REQUEST", "message": message}
    if field:
        body["field"] = field
    return _build(400, body)


def unauthorized(message: str = "Authentication required") -> dict[str, Any]:
    """401 Unauthorized (should normally be handled by API Gateway, not Lambda)."""
    return _build(401, {"error": "UNAUTHORIZED", "message": message})


def not_found(message: str = "Resource not found") -> dict[str, Any]:
    """404 Not Found."""
    return _build(404, {"error": "NOT_FOUND", "message": message})


def conflict(message: str, existing_request_id: str | None = None) -> dict[str, Any]:
    """409 Conflict — duplicate clientRequestId with different payload."""
    body: dict[str, Any] = {"error": "CONFLICT", "message": message}
    if existing_request_id:
        body["existingRequestId"] = existing_request_id
    return _build(409, body)


def too_many_requests(message: str = "Too many requests") -> dict[str, Any]:
    """429 Too Many Requests."""
    return _build(429, {"error": "TOO_MANY_REQUESTS", "message": message})


# ── Server error responses ────────────────────────────────────────────────────


def internal_error(message: str = "Internal server error") -> dict[str, Any]:
    """
    500 Internal Server Error.
    Never expose stack traces or AWS resource details in the message.
    """
    return _build(500, {"error": "INTERNAL_ERROR", "message": message})
