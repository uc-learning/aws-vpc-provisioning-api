"""
Unit tests for src/create_vpc/app.py Lambda handler.

DynamoDB and Step Functions are mocked via pytest-mock / moto.
No real AWS calls are made.
"""
from __future__ import annotations

import json
import os

import pytest

os.environ.setdefault("DYNAMODB_TABLE", "VpcProvisioningRequests-test")
os.environ.setdefault("STATE_MACHINE_ARN", "arn:aws:states:ap-south-1:123456789012:stateMachine:vpc-provisioning-test")
os.environ.setdefault("AWS_REGION", "ap-south-1")
os.environ.setdefault("AWS_DEFAULT_REGION", "ap-south-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("AWS_SECURITY_TOKEN", "testing")
os.environ.setdefault("AWS_SESSION_TOKEN", "testing")

from unittest.mock import MagicMock, patch

from src.create_vpc.app import handler


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_event(body: dict | None = None, sub: str = "cognito-sub-123") -> dict:
    """Build a minimal API Gateway proxy event with Cognito claims."""
    return {
        "httpMethod": "POST",
        "path": "/vpcs",
        "pathParameters": None,
        "requestContext": {
            "authorizer": {
                "claims": {
                    "sub": sub,
                    "email": "test@example.com",
                }
            }
        },
        "body": json.dumps(body) if body is not None else None,
        "headers": {"Content-Type": "application/json"},
    }


def _valid_body(**overrides) -> dict:
    data = {
        "name": "test-vpc",
        "cidrBlock": "10.20.0.0/16",
        "subnets": [
            {"name": "app-a", "cidrBlock": "10.20.1.0/24", "availabilityZone": "ap-south-1a"},
            {"name": "app-b", "cidrBlock": "10.20.2.0/24", "availabilityZone": "ap-south-1b"},
        ],
        "tags": {"Owner": "test"},
    }
    data.update(overrides)
    return data


MOCK_CONTEXT = MagicMock()


# ── Test cases ────────────────────────────────────────────────────────────────

class TestCreateVpcHandler:

    @patch("src.create_vpc.app._sfn_client")
    @patch("src.create_vpc.app.repository")
    def test_valid_request_returns_202(self, mock_repo, mock_sfn):
        mock_repo.create_if_absent.return_value = None  # new record
        mock_repo.compute_request_hash.return_value = "hash-abc"
        mock_sfn.start_execution.return_value = {
            "executionArn": "arn:aws:states:ap-south-1:123:execution:sfn:exec-1"
        }

        event = _make_event(_valid_body())
        response = handler(event, MOCK_CONTEXT)

        assert response["statusCode"] == 202
        body = json.loads(response["body"])
        assert body["status"] == "PENDING"
        assert "requestId" in body
        assert body["statusUrl"].startswith("/vpcs/")

    @patch("src.create_vpc.app._sfn_client")
    @patch("src.create_vpc.app.repository")
    def test_missing_body_returns_400(self, mock_repo, mock_sfn):
        event = _make_event(body=None)
        response = handler(event, MOCK_CONTEXT)
        assert response["statusCode"] == 400

    @patch("src.create_vpc.app._sfn_client")
    @patch("src.create_vpc.app.repository")
    def test_invalid_cidr_returns_400(self, mock_repo, mock_sfn):
        body = _valid_body(cidrBlock="not-a-cidr")
        event = _make_event(body)
        response = handler(event, MOCK_CONTEXT)
        assert response["statusCode"] == 400

    @patch("src.create_vpc.app._sfn_client")
    @patch("src.create_vpc.app.repository")
    def test_overlapping_subnets_returns_400(self, mock_repo, mock_sfn):
        body = _valid_body(subnets=[
            {"name": "a", "cidrBlock": "10.20.1.0/24", "availabilityZone": "ap-south-1a"},
            {"name": "b", "cidrBlock": "10.20.1.128/25", "availabilityZone": "ap-south-1b"},
        ])
        event = _make_event(body)
        response = handler(event, MOCK_CONTEXT)
        assert response["statusCode"] == 400
        resp_body = json.loads(response["body"])
        assert "overlap" in resp_body["message"].lower()

    @patch("src.create_vpc.app._sfn_client")
    @patch("src.create_vpc.app.repository")
    def test_reserved_tag_returns_400(self, mock_repo, mock_sfn):
        body = _valid_body(tags={"RequestId": "hacked"})
        event = _make_event(body)
        response = handler(event, MOCK_CONTEXT)
        assert response["statusCode"] == 400

    @patch("src.create_vpc.app._sfn_client")
    @patch("src.create_vpc.app.repository")
    def test_missing_cognito_claims_returns_401(self, mock_repo, mock_sfn):
        event = _make_event(_valid_body())
        event["requestContext"] = {}  # no authorizer claims
        response = handler(event, MOCK_CONTEXT)
        assert response["statusCode"] == 401

    @patch("src.create_vpc.app._sfn_client")
    @patch("src.create_vpc.app.repository")
    def test_idempotent_replay_returns_202_with_existing_id(self, mock_repo, mock_sfn):
        mock_repo.compute_request_hash.return_value = "hash-abc"
        mock_repo.create_if_absent.return_value = {
            "requestId": "existing-req-id",
            "status": "SUCCEEDED",
        }

        event = _make_event(_valid_body(clientRequestId="demo-001"))
        response = handler(event, MOCK_CONTEXT)

        assert response["statusCode"] == 202
        body = json.loads(response["body"])
        assert body["requestId"] == "existing-req-id"
        assert body["status"] == "SUCCEEDED"
        # Step Functions must NOT be called for an idempotent replay
        mock_sfn.start_execution.assert_not_called()

    @patch("src.create_vpc.app._sfn_client")
    @patch("src.create_vpc.app.repository")
    def test_duplicate_client_request_id_returns_409(self, mock_repo, mock_sfn):
        from src.common.repository import DuplicateRequestError
        mock_repo.compute_request_hash.return_value = "hash-abc"
        mock_repo.create_if_absent.side_effect = DuplicateRequestError("existing-req-id")

        event = _make_event(_valid_body(clientRequestId="demo-dup"))
        response = handler(event, MOCK_CONTEXT)

        assert response["statusCode"] == 409
        body = json.loads(response["body"])
        assert body["error"] == "CONFLICT"

    @patch("src.create_vpc.app._sfn_client")
    @patch("src.create_vpc.app.repository")
    def test_step_functions_failure_returns_500(self, mock_repo, mock_sfn):
        mock_repo.create_if_absent.return_value = None
        mock_repo.compute_request_hash.return_value = "hash"
        mock_sfn.start_execution.side_effect = Exception("SFN unavailable")

        event = _make_event(_valid_body())
        response = handler(event, MOCK_CONTEXT)

        assert response["statusCode"] == 500

    @patch("src.create_vpc.app._sfn_client")
    @patch("src.create_vpc.app.repository")
    def test_extra_field_in_body_returns_400(self, mock_repo, mock_sfn):
        body = _valid_body()
        body["unknownField"] = "should-be-rejected"
        event = _make_event(body)
        response = handler(event, MOCK_CONTEXT)
        assert response["statusCode"] == 400

    @patch("src.create_vpc.app._sfn_client")
    @patch("src.create_vpc.app.repository")
    def test_subnet_in_wrong_az_region_returns_400(self, mock_repo, mock_sfn):
        body = _valid_body(subnets=[
            {"name": "a", "cidrBlock": "10.20.1.0/24", "availabilityZone": "us-east-1a"},
        ])
        event = _make_event(body)
        response = handler(event, MOCK_CONTEXT)
        assert response["statusCode"] == 400
