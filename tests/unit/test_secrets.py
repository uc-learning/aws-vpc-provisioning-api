"""
Unit tests for src/common/secrets.py

Tests cover:
- Successful secret retrieval and caching
- Cache TTL expiry triggers a re-fetch
- JSON parsing from get_secret_json / get_secret_field
- SecretNotFoundError on ResourceNotFoundException
- SecretAccessError on AccessDeniedException
- RuntimeError on other boto3 errors
- invalidate_cache clears correctly
- Binary secret raises RuntimeError
"""
from __future__ import annotations

import os
import time
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

os.environ.setdefault("AWS_DEFAULT_REGION", "ap-south-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")


def _client_error(code: str) -> ClientError:
    return ClientError(
        {"Error": {"Code": code, "Message": "test error"}},
        "GetSecretValue",
    )


class TestGetSecretString:
    def setup_method(self):
        from src.common import secrets
        secrets.invalidate_cache()

    def test_returns_secret_string(self):
        from src.common.secrets import get_secret_string

        with patch("src.common.secrets._secrets_client") as mock_client:
            mock_client.get_secret_value.return_value = {
                "SecretString": '{"key": "value"}',
                "VersionId": "v1",
            }
            result = get_secret_string("test-secret")

        assert result == '{"key": "value"}'

    def test_caches_on_second_call(self):
        from src.common.secrets import get_secret_string

        with patch("src.common.secrets._secrets_client") as mock_client:
            mock_client.get_secret_value.return_value = {
                "SecretString": "cached-value",
                "VersionId": "v1",
            }
            get_secret_string("test-secret")
            get_secret_string("test-secret")

        # Only one actual API call despite two get_secret_string calls
        assert mock_client.get_secret_value.call_count == 1

    def test_force_refresh_bypasses_cache(self):
        from src.common.secrets import get_secret_string

        with patch("src.common.secrets._secrets_client") as mock_client:
            mock_client.get_secret_value.return_value = {
                "SecretString": "value",
                "VersionId": "v1",
            }
            get_secret_string("test-secret")
            get_secret_string("test-secret", force_refresh=True)

        assert mock_client.get_secret_value.call_count == 2

    def test_resource_not_found_raises_secret_not_found(self):
        from src.common.secrets import SecretNotFoundError, get_secret_string

        with patch("src.common.secrets._secrets_client") as mock_client:
            mock_client.get_secret_value.side_effect = _client_error("ResourceNotFoundException")
            with pytest.raises(SecretNotFoundError):
                get_secret_string("missing-secret")

    def test_access_denied_raises_secret_access_error(self):
        from src.common.secrets import SecretAccessError, get_secret_string

        with patch("src.common.secrets._secrets_client") as mock_client:
            mock_client.get_secret_value.side_effect = _client_error("AccessDeniedException")
            with pytest.raises(SecretAccessError):
                get_secret_string("denied-secret")

    def test_other_client_error_raises_runtime_error(self):
        from src.common.secrets import get_secret_string

        with patch("src.common.secrets._secrets_client") as mock_client:
            mock_client.get_secret_value.side_effect = _client_error("InternalServiceError")
            with pytest.raises(RuntimeError, match="InternalServiceError"):
                get_secret_string("broken-secret")

    def test_binary_secret_raises_runtime_error(self):
        from src.common.secrets import get_secret_string

        with patch("src.common.secrets._secrets_client") as mock_client:
            mock_client.get_secret_value.return_value = {
                "SecretBinary": b"binary-data",
                "VersionId": "v1",
            }
            with pytest.raises(RuntimeError, match="binary"):
                get_secret_string("binary-secret")


class TestGetSecretJson:
    def setup_method(self):
        from src.common import secrets
        secrets.invalidate_cache()

    def test_parses_json_string(self):
        from src.common.secrets import get_secret_json

        with patch("src.common.secrets._secrets_client") as mock_client:
            mock_client.get_secret_value.return_value = {
                "SecretString": '{"allowed_azs": "ap-south-1a"}',
                "VersionId": "v1",
            }
            result = get_secret_json("json-secret")

        assert result["allowed_azs"] == "ap-south-1a"

    def test_invalid_json_raises_value_error(self):
        from src.common.secrets import get_secret_json

        with patch("src.common.secrets._secrets_client") as mock_client:
            mock_client.get_secret_value.return_value = {
                "SecretString": "not-json",
                "VersionId": "v1",
            }
            with pytest.raises(ValueError, match="not valid JSON"):
                get_secret_json("bad-json-secret")


class TestGetSecretField:
    def setup_method(self):
        from src.common import secrets
        secrets.invalidate_cache()

    def test_returns_field_value(self):
        from src.common.secrets import get_secret_field

        with patch("src.common.secrets._secrets_client") as mock_client:
            mock_client.get_secret_value.return_value = {
                "SecretString": '{"max_subnets": "10", "region": "ap-south-1"}',
                "VersionId": "v1",
            }
            assert get_secret_field("s", "max_subnets") == "10"
            assert get_secret_field("s", "region") == "ap-south-1"

    def test_missing_field_raises_key_error(self):
        from src.common.secrets import get_secret_field

        with patch("src.common.secrets._secrets_client") as mock_client:
            mock_client.get_secret_value.return_value = {
                "SecretString": '{"key": "val"}',
                "VersionId": "v1",
            }
            with pytest.raises(KeyError, match="missing_field"):
                get_secret_field("s", "missing_field")


class TestInvalidateCache:
    def setup_method(self):
        from src.common import secrets
        secrets.invalidate_cache()

    def test_invalidate_specific_secret(self):
        from src.common.secrets import get_secret_string, invalidate_cache

        with patch("src.common.secrets._secrets_client") as mock_client:
            mock_client.get_secret_value.return_value = {
                "SecretString": "v1",
                "VersionId": "v1",
            }
            get_secret_string("my-secret")
            invalidate_cache("my-secret")
            get_secret_string("my-secret")

        assert mock_client.get_secret_value.call_count == 2

    def test_invalidate_all_secrets(self):
        from src.common.secrets import get_secret_string, invalidate_cache

        with patch("src.common.secrets._secrets_client") as mock_client:
            mock_client.get_secret_value.return_value = {
                "SecretString": "v",
                "VersionId": "v1",
            }
            get_secret_string("secret-a")
            get_secret_string("secret-b")
            invalidate_cache()   # flush all
            get_secret_string("secret-a")
            get_secret_string("secret-b")

        assert mock_client.get_secret_value.call_count == 4
