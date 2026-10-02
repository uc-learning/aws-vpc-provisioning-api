"""
AWS Secrets Manager client with in-process caching and safe redaction.

Design goals:
- A single boto3 SecretsManager client is reused across Lambda warm invocations
  (module-level singleton).
- Secrets are cached in memory with a configurable TTL so that repeated calls
  within the same invocation — or across warm invocations — do not incur an
  extra API call every time.
- Secret values are NEVER written to logs or included in exception messages
  that might reach CloudWatch unredacted.
- The cache is intentionally simple (dict + timestamp). A Lambda function
  typically lives for minutes to hours; a 5-minute TTL is appropriate.
- Boto3 retries (botocore default: 3 standard retries with exponential backoff)
  cover transient Secrets Manager throttling.

Thread safety note: Lambda functions are single-threaded by default.
If you ever run multiple threads inside one Lambda invocation, replace
the dict cache with a threading.Lock-protected structure.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from .logging import get_logger

_logger = get_logger("secrets")

# ── Configuration ─────────────────────────────────────────────────────────────

_REGION = os.environ.get("AWS_REGION", "ap-south-1")
_CACHE_TTL_SECONDS = int(os.environ.get("SECRETS_CACHE_TTL_SECONDS", "300"))  # 5 minutes

# ── Module-level singleton client ─────────────────────────────────────────────

_secrets_client = boto3.client(
    "secretsmanager",
    region_name=_REGION,
    config=Config(
        retries={"max_attempts": 5, "mode": "adaptive"},
        connect_timeout=3,
        read_timeout=5,
    ),
)

# ── In-memory cache: secret_id → (fetched_at_epoch, parsed_value) ─────────────

_cache: dict[str, tuple[float, Any]] = {}


# ── Exceptions ────────────────────────────────────────────────────────────────


class SecretNotFoundError(Exception):
    """Raised when a secret ARN/name does not exist in Secrets Manager."""

    def __init__(self, secret_id: str) -> None:
        # Never include the secret_id value in prod, but it is a name/ARN (not a value)
        super().__init__(f"Secret not found: {secret_id}")
        self.secret_id = secret_id


class SecretAccessError(Exception):
    """Raised when the Lambda role lacks permission to retrieve the secret."""

    def __init__(self, secret_id: str) -> None:
        super().__init__(f"Access denied for secret: {secret_id}")
        self.secret_id = secret_id


# ── Public API ────────────────────────────────────────────────────────────────


def get_secret_string(secret_id: str, *, force_refresh: bool = False) -> str:
    """
    Retrieve a plaintext secret string from Secrets Manager.

    Returns the raw secret string (not parsed).
    Use get_secret_json() when the secret is a JSON object.

    Raises:
        SecretNotFoundError  — secret does not exist
        SecretAccessError    — IAM permission denied
        RuntimeError         — any other Secrets Manager error
    """
    cached = _get_cached(secret_id)
    if cached is not None and not force_refresh:
        return cached  # type: ignore[return-value]

    value = _fetch_raw(secret_id)
    _set_cache(secret_id, value)
    return value


def get_secret_json(secret_id: str, *, force_refresh: bool = False) -> dict[str, Any]:
    """
    Retrieve and JSON-parse a secret from Secrets Manager.

    Raises ValueError if the secret string is not valid JSON.
    """
    raw = get_secret_string(secret_id, force_refresh=force_refresh)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Secret '{secret_id}' is not valid JSON: {exc}") from exc


def get_secret_field(secret_id: str, field: str, *, force_refresh: bool = False) -> str:
    """
    Retrieve a single field from a JSON secret object.

    Raises KeyError if the field is absent from the secret.
    """
    data = get_secret_json(secret_id, force_refresh=force_refresh)
    if field not in data:
        raise KeyError(f"Field '{field}' not found in secret '{secret_id}'")
    return str(data[field])


def invalidate_cache(secret_id: str | None = None) -> None:
    """
    Invalidate the in-memory cache.
    Pass a specific secret_id to invalidate only that entry,
    or None to flush the entire cache.
    """
    if secret_id is None:
        _cache.clear()
        _logger.debug("Secrets cache fully cleared")
    else:
        _cache.pop(secret_id, None)
        _logger.debug("Secrets cache invalidated", secret_id=secret_id)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _get_cached(secret_id: str) -> Any | None:
    entry = _cache.get(secret_id)
    if entry is None:
        return None
    fetched_at, value = entry
    if time.monotonic() - fetched_at > _CACHE_TTL_SECONDS:
        _cache.pop(secret_id, None)
        return None
    return value


def _set_cache(secret_id: str, value: Any) -> None:
    _cache[secret_id] = (time.monotonic(), value)


def _fetch_raw(secret_id: str) -> str:
    """
    Fetch from Secrets Manager.
    Deliberately avoids logging the returned value.
    """
    try:
        response = _secrets_client.get_secret_value(SecretId=secret_id)
    except ClientError as exc:
        code = exc.response["Error"]["Code"]
        if code in ("ResourceNotFoundException", "InvalidParameterException"):
            _logger.error("Secret not found", secret_id=secret_id, error_code=code)
            raise SecretNotFoundError(secret_id) from exc
        if code in ("AccessDeniedException", "UnauthorizedClientException"):
            _logger.error("Secret access denied", secret_id=secret_id, error_code=code)
            raise SecretAccessError(secret_id) from exc
        # Throttling, transient errors — boto3 retried; still failing
        _logger.error("Secrets Manager error", secret_id=secret_id, error_code=code)
        raise RuntimeError(f"Secrets Manager error [{code}] for secret: {secret_id}") from exc

    # A secret can be stored as a string or as binary; we only use string secrets.
    secret_value = response.get("SecretString")
    if secret_value is None:
        raise RuntimeError(
            f"Secret '{secret_id}' has a binary value; only string secrets are supported"
        )
    # Log that a fetch occurred — never log the value itself
    _logger.info(
        "Secret fetched from Secrets Manager",
        secret_id=secret_id,
        version_id=response.get("VersionId", "unknown"),
    )
    return secret_value
