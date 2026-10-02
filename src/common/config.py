"""
Centralised application configuration loader.

Resolution order (first defined wins):
  1. AWS Secrets Manager  — for sensitive/environment-specific values
  2. Environment variables — for non-sensitive infrastructure references
     injected by the SAM template (table name, state machine ARN, etc.)
  3. Hard-coded defaults   — for safe fallbacks only (never for secrets)

Secret layout in Secrets Manager:
  The SAM template creates one secret per environment:
    /vpc-provisioning-api/<env>/config

  The secret is a JSON object:
    {
      "allowed_vpc_cidr_ranges": "10.0.0.0/8,172.16.0.0/12,192.168.0.0/16",
      "max_subnets_per_request": "20",
      "provisioning_concurrency_limit": "10",
      "allowed_azs": "ap-south-1a,ap-south-1b,ap-south-1c",
      "alert_email": "ops-team@example.com"
    }

  Any field absent from the secret falls back to the environment variable
  or hard-coded default so that adding new fields to the secret does not
  require a Lambda deployment.

Caching:
  The AppConfig object is built once at module load time (Lambda init phase)
  and cached as a module-level singleton.  Secrets Manager results are
  further cached in secrets.py with a 5-minute TTL.  This means:
    - Cold start: one Secrets Manager call during init.
    - Warm invocations: no Secrets Manager calls (config is already loaded).
    - Secret rotation: cache expires after ≤5 minutes; no Lambda redeploy needed.

Environment-specific tuning is achieved by:
  a) Different secret values per environment (stored in the secret JSON).
  b) Different SAM parameter overrides (injected as Lambda env vars).
  Both are read here and surfaced through a single AppConfig object.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from .logging import get_logger
from .secrets import SecretNotFoundError, get_secret_json

_logger = get_logger("config")

# ── Environment variable names (set by SAM template Globals) ─────────────────

_ENV_DYNAMODB_TABLE      = "DYNAMODB_TABLE"
_ENV_STATE_MACHINE_ARN   = "STATE_MACHINE_ARN"
_ENV_AWS_REGION          = "AWS_REGION"
_ENV_ENVIRONMENT         = "ENVIRONMENT"
_ENV_LOG_LEVEL           = "LOG_LEVEL"
_ENV_APP_SECRET_ARN      = "APP_SECRET_ARN"          # ARN of the Secrets Manager secret

# ── Per-environment defaults (non-sensitive) ──────────────────────────────────

_ENV_DEFAULTS: dict[str, dict[str, Any]] = {
    "demo": {
        "max_subnets_per_request":        20,
        "provisioning_concurrency_limit": 5,
        "vpc_cidr_min_prefix":            16,
        "vpc_cidr_max_prefix":            28,
        "log_retention_days":             30,
        "api_throttle_burst_limit":       50,
        "api_throttle_rate_limit":        100,
    },
    "staging": {
        "max_subnets_per_request":        20,
        "provisioning_concurrency_limit": 10,
        "vpc_cidr_min_prefix":            16,
        "vpc_cidr_max_prefix":            28,
        "log_retention_days":             60,
        "api_throttle_burst_limit":       100,
        "api_throttle_rate_limit":        200,
    },
    "production": {
        "max_subnets_per_request":        20,
        "provisioning_concurrency_limit": 20,
        "vpc_cidr_min_prefix":            16,
        "vpc_cidr_max_prefix":            28,
        "log_retention_days":             90,
        "api_throttle_burst_limit":       500,
        "api_throttle_rate_limit":        1000,
    },
}

_FALLBACK_DEFAULTS = _ENV_DEFAULTS["demo"]


# ── AppConfig dataclass ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class AppConfig:
    """
    Immutable configuration snapshot.

    All Lambda handlers import `get_config()` and read from this object.
    Fields are documented with their source and sensitivity level.
    """

    # ── Infrastructure references (from Lambda env vars, not sensitive) ───────
    dynamodb_table: str           # DynamoDB table name
    state_machine_arn: str        # Step Functions ARN (create Lambda only)
    aws_region: str               # AWS region
    environment: str              # demo | staging | production
    log_level: str                # DEBUG | INFO | WARNING | ERROR
    app_secret_arn: str           # Secrets Manager ARN (the config secret itself)

    # ── Operational limits (from secret or env-specific defaults) ─────────────
    max_subnets_per_request: int        # Hard cap on subnets[] array length
    provisioning_concurrency_limit: int # Soft cap on concurrent Step Functions executions
    vpc_cidr_min_prefix: int            # Minimum VPC prefix length (default 16)
    vpc_cidr_max_prefix: int            # Maximum VPC prefix length (default 28)

    # ── Allowed AZs (from secret; None = accept any syntactically valid AZ) ───
    allowed_azs: list[str] = field(default_factory=list)

    # ── Alert/notification target (from secret; optional) ────────────────────
    alert_email: str = ""

    def is_production(self) -> bool:
        return self.environment == "production"

    def is_az_allowed(self, az: str) -> bool:
        """Return True if the AZ is in the allow-list (or if no list is configured)."""
        if not self.allowed_azs:
            return True
        return az in self.allowed_azs


# ── Loader ────────────────────────────────────────────────────────────────────


@lru_cache(maxsize=1)
def get_config() -> AppConfig:
    """
    Build and return the AppConfig singleton.

    This function is called once per Lambda cold start.
    lru_cache ensures the result is reused for all subsequent calls
    within the same Lambda execution environment (warm invocations).
    """
    env          = os.environ.get(_ENV_ENVIRONMENT, "demo").lower()
    aws_region   = os.environ.get(_ENV_AWS_REGION, "ap-south-1")
    log_level    = os.environ.get(_ENV_LOG_LEVEL, "INFO").upper()

    # Pull non-sensitive infrastructure ARNs from Lambda env vars
    dynamodb_table    = _require_env(_ENV_DYNAMODB_TABLE)
    state_machine_arn = os.environ.get(_ENV_STATE_MACHINE_ARN, "")  # absent in get/health Lambdas
    app_secret_arn    = os.environ.get(_ENV_APP_SECRET_ARN, "")

    # Per-environment defaults
    defaults = _ENV_DEFAULTS.get(env, _FALLBACK_DEFAULTS)

    # Attempt to load additional config from Secrets Manager
    secret_data: dict[str, Any] = {}
    if app_secret_arn:
        try:
            secret_data = get_secret_json(app_secret_arn)
            _logger.info("App config loaded from Secrets Manager", environment=env)
        except SecretNotFoundError:
            _logger.warning(
                "App secret not found — using environment defaults",
                secret_arn=app_secret_arn,
                environment=env,
            )
        except Exception as exc:  # noqa: BLE001
            _logger.warning(
                "Failed to load app secret — using environment defaults",
                error=str(exc),
                environment=env,
            )
    else:
        _logger.info("APP_SECRET_ARN not set — using environment defaults", environment=env)

    # Merge: secret_data overrides defaults
    def _int(key: str) -> int:
        raw = secret_data.get(key)
        if raw is not None:
            return int(raw)
        return int(defaults.get(key, _FALLBACK_DEFAULTS[key]))

    def _str_list(key: str) -> list[str]:
        raw = secret_data.get(key, "")
        if not raw:
            return []
        return [item.strip() for item in raw.split(",") if item.strip()]

    config = AppConfig(
        dynamodb_table=dynamodb_table,
        state_machine_arn=state_machine_arn,
        aws_region=aws_region,
        environment=env,
        log_level=log_level,
        app_secret_arn=app_secret_arn,
        max_subnets_per_request=_int("max_subnets_per_request"),
        provisioning_concurrency_limit=_int("provisioning_concurrency_limit"),
        vpc_cidr_min_prefix=_int("vpc_cidr_min_prefix"),
        vpc_cidr_max_prefix=_int("vpc_cidr_max_prefix"),
        allowed_azs=_str_list("allowed_azs"),
        alert_email=secret_data.get("alert_email", ""),
    )

    _logger.info(
        "AppConfig initialised",
        environment=config.environment,
        max_subnets=config.max_subnets_per_request,
        concurrency_limit=config.provisioning_concurrency_limit,
        allowed_azs_count=len(config.allowed_azs),
    )
    return config


def reset_config() -> None:
    """
    Clear the lru_cache so the next get_config() call re-reads everything.
    Use this in tests or after a secret rotation signal.
    """
    get_config.cache_clear()
    _logger.debug("AppConfig cache cleared")


# ── Helpers ───────────────────────────────────────────────────────────────────


def _require_env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise RuntimeError(
            f"Required environment variable '{name}' is not set. "
            "Check the SAM template Globals/Function/Environment section."
        )
    return value
