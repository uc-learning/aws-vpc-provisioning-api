"""
Unit tests for src/common/config.py

Tests cover:
- Config loads correctly from Secrets Manager when APP_SECRET_ARN is set
- Config falls back to per-environment defaults when secret is unavailable
- Per-environment defaults produce correct values for demo/staging/production
- reset_config() clears the lru_cache allowing a fresh load
- AppConfig helper methods work correctly
"""
from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

import pytest

# Minimal env vars required before importing the module
os.environ.setdefault("DYNAMODB_TABLE", "VpcProvisioningRequests-test")
os.environ.setdefault("AWS_DEFAULT_REGION", "ap-south-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")


# Helper to reset state between tests
def _reset(env: str = "demo", secret_arn: str = "") -> None:
    from src.common.config import reset_config
    os.environ["ENVIRONMENT"]    = env
    os.environ["DYNAMODB_TABLE"] = "VpcProvisioningRequests-test"
    os.environ["APP_SECRET_ARN"] = secret_arn
    os.environ.pop("STATE_MACHINE_ARN", None)
    reset_config()


class TestAppConfigDefaults:
    """Config falls back to hard-coded per-env defaults when no secret is set."""

    def test_demo_defaults(self):
        _reset("demo")
        from src.common.config import get_config
        cfg = get_config()
        assert cfg.environment == "demo"
        assert cfg.max_subnets_per_request == 20
        assert cfg.provisioning_concurrency_limit == 5
        assert cfg.vpc_cidr_min_prefix == 16
        assert cfg.vpc_cidr_max_prefix == 28
        assert cfg.allowed_azs == []

    def test_staging_defaults(self):
        _reset("staging")
        from src.common.config import get_config
        cfg = get_config()
        assert cfg.provisioning_concurrency_limit == 10

    def test_production_defaults(self):
        _reset("production")
        from src.common.config import get_config
        cfg = get_config()
        assert cfg.provisioning_concurrency_limit == 20
        assert cfg.is_production() is True

    def test_demo_is_not_production(self):
        _reset("demo")
        from src.common.config import get_config
        assert get_config().is_production() is False

    def test_unknown_env_falls_back_to_demo(self):
        _reset("unknown-env")
        from src.common.config import get_config
        cfg = get_config()
        # Should not raise; falls back to demo defaults
        assert cfg.max_subnets_per_request == 20


class TestAppConfigFromSecret:
    """Config values are overridden by Secrets Manager when APP_SECRET_ARN is set."""

    def _mock_secret(self, data: dict) -> MagicMock:
        mock = MagicMock()
        mock.return_value = data
        return mock

    def test_secret_overrides_defaults(self):
        _reset("demo", secret_arn="arn:aws:secretsmanager:ap-south-1:123:secret:test")

        secret_payload = {
            "max_subnets_per_request":        "5",
            "provisioning_concurrency_limit": "2",
            "vpc_cidr_min_prefix":            "20",
            "vpc_cidr_max_prefix":            "26",
            "allowed_azs":                    "ap-south-1a,ap-south-1b",
            "alert_email":                    "ops@example.com",
        }

        with patch("src.common.config.get_secret_json", return_value=secret_payload):
            from src.common.config import get_config
            cfg = get_config()

        assert cfg.max_subnets_per_request == 5
        assert cfg.provisioning_concurrency_limit == 2
        assert cfg.vpc_cidr_min_prefix == 20
        assert cfg.vpc_cidr_max_prefix == 26
        assert cfg.allowed_azs == ["ap-south-1a", "ap-south-1b"]
        assert cfg.alert_email == "ops@example.com"

    def test_secret_unavailable_falls_back_gracefully(self):
        from src.common.secrets import SecretNotFoundError

        _reset("demo", secret_arn="arn:aws:secretsmanager:ap-south-1:123:secret:missing")

        with patch("src.common.config.get_secret_json", side_effect=SecretNotFoundError("missing")):
            from src.common.config import get_config
            cfg = get_config()

        # Must not raise; falls back to environment defaults
        assert cfg.max_subnets_per_request == 20

    def test_secret_error_falls_back_gracefully(self):
        _reset("demo", secret_arn="arn:aws:secretsmanager:ap-south-1:123:secret:broken")

        with patch("src.common.config.get_secret_json", side_effect=RuntimeError("network error")):
            from src.common.config import get_config
            cfg = get_config()

        assert cfg.max_subnets_per_request == 20


class TestIsAzAllowed:
    def test_no_allowed_azs_permits_all(self):
        _reset("demo")
        from src.common.config import get_config
        cfg = get_config()
        assert cfg.is_az_allowed("ap-south-1a") is True
        assert cfg.is_az_allowed("us-east-1a") is True

    def test_allowed_az_list_enforced(self):
        _reset("demo", secret_arn="arn:aws:secretsmanager:ap-south-1:123:secret:test")
        secret_payload = {"allowed_azs": "ap-south-1a,ap-south-1b"}

        with patch("src.common.config.get_secret_json", return_value=secret_payload):
            from src.common.config import get_config
            cfg = get_config()

        assert cfg.is_az_allowed("ap-south-1a") is True
        assert cfg.is_az_allowed("ap-south-1c") is False


class TestRequiredEnvVar:
    def test_missing_dynamodb_table_raises(self):
        from src.common.config import reset_config, _require_env
        original = os.environ.pop("DYNAMODB_TABLE", None)
        try:
            with pytest.raises(RuntimeError, match="DYNAMODB_TABLE"):
                _require_env("DYNAMODB_TABLE")
        finally:
            if original:
                os.environ["DYNAMODB_TABLE"] = original
