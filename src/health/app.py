"""
GET /health — unauthenticated liveness/readiness probe.

Returns a minimal 200 OK with service metadata. Intentionally unauthenticated
so uptime monitors and CI smoke tests can check reachability without tokens.

IMPORTANT: never expose secrets, ARNs, account IDs, internal IPs, or any
configurable values that could aid an attacker. The response only contains
safe, public-facing metadata.
"""
from __future__ import annotations

import json
from typing import Any

from common import logging as log_module
from common.config import get_config

_config      = get_config()
_base_logger = log_module.get_logger("health")

_RESPONSE_BODY = json.dumps({
    "status":      "ok",
    "service":     "vpc-provisioning-api",
    "environment": _config.environment,
    # Do NOT include: ARNs, table names, secret ARNs, account IDs, IP ranges
})


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:  # noqa: ANN401
    _base_logger.info("Health check requested")
    return {
        "statusCode": 200,
        "headers": {
            "Content-Type":              "application/json",
            "Cache-Control":             "no-store",
            "X-Content-Type-Options":    "nosniff",
            "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
        },
        "body": _RESPONSE_BODY,
    }
