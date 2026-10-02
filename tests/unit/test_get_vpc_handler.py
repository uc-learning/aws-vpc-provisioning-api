"""
Unit tests for src/get_vpc/app.py Lambda handler.
"""
from __future__ import annotations

import json
import os

import pytest

os.environ.setdefault("DYNAMODB_TABLE", "VpcProvisioningRequests-test")
os.environ.setdefault("AWS_DEFAULT_REGION", "ap-south-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")

from unittest.mock import MagicMock, patch

from src.get_vpc.app import handler

MOCK_CONTEXT = MagicMock()


def _make_event(request_id: str | None = "req-abc-123") -> dict:
    return {
        "httpMethod": "GET",
        "path": f"/vpcs/{request_id}" if request_id else "/vpcs/",
        "pathParameters": {"requestId": request_id} if request_id else None,
        "requestContext": {
            "authorizer": {
                "claims": {"sub": "cognito-sub-123"}
            }
        },
        "body": None,
    }


class TestGetVpcHandler:

    @patch("src.get_vpc.app.repository")
    def test_found_pending_returns_200(self, mock_repo):
        mock_repo.get_request.return_value = {
            "requestId": "req-abc-123",
            "status": "PENDING",
            "requestedBy": "sub-123",
            "createdAt": "2026-09-28T14:10:00Z",
            "updatedAt": "2026-09-28T14:10:00Z",
        }
        response = handler(_make_event("req-abc-123"), MOCK_CONTEXT)
        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert body["requestId"] == "req-abc-123"
        assert body["status"] == "PENDING"

    @patch("src.get_vpc.app.repository")
    def test_found_succeeded_includes_vpc_and_subnets(self, mock_repo):
        mock_repo.get_request.return_value = {
            "requestId": "req-abc-456",
            "status": "SUCCEEDED",
            "requestedBy": "sub-123",
            "createdAt": "2026-09-28T14:10:00Z",
            "updatedAt": "2026-09-28T14:10:09Z",
            "vpc": {
                "vpcId": "vpc-0123456789abcdef0",
                "cidrBlock": "10.20.0.0/16",
                "state": "available",
            },
            "subnets": [
                {
                    "name": "app-a",
                    "subnetId": "subnet-abc",
                    "cidrBlock": "10.20.1.0/24",
                    "availabilityZone": "ap-south-1a",
                    "state": "available",
                }
            ],
        }
        response = handler(_make_event("req-abc-456"), MOCK_CONTEXT)
        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert body["vpc"]["vpcId"] == "vpc-0123456789abcdef0"
        assert len(body["subnets"]) == 1
        assert body["subnets"][0]["subnetId"] == "subnet-abc"

    @patch("src.get_vpc.app.repository")
    def test_not_found_returns_404(self, mock_repo):
        from src.common.repository import RequestNotFoundError
        mock_repo.get_request.side_effect = RequestNotFoundError("req-missing")
        response = handler(_make_event("req-missing"), MOCK_CONTEXT)
        assert response["statusCode"] == 404
        body = json.loads(response["body"])
        assert body["error"] == "NOT_FOUND"

    def test_missing_request_id_returns_400(self):
        event = _make_event(None)
        response = handler(event, MOCK_CONTEXT)
        assert response["statusCode"] == 400

    @patch("src.get_vpc.app.repository")
    def test_dynamodb_error_returns_500(self, mock_repo):
        mock_repo.get_request.side_effect = Exception("DynamoDB unavailable")
        response = handler(_make_event("req-err"), MOCK_CONTEXT)
        assert response["statusCode"] == 500

    @patch("src.get_vpc.app.repository")
    def test_failed_status_includes_error_field(self, mock_repo):
        mock_repo.get_request.return_value = {
            "requestId": "req-failed",
            "status": "FAILED",
            "requestedBy": "sub-xyz",
            "createdAt": "2026-09-28T14:10:00Z",
            "updatedAt": "2026-09-28T14:11:00Z",
            "error": {
                "code": "PROVISIONING_FAILED",
                "message": "Something went wrong",
            },
        }
        response = handler(_make_event("req-failed"), MOCK_CONTEXT)
        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert body["status"] == "FAILED"
        assert body["error"]["code"] == "PROVISIONING_FAILED"

    @patch("src.get_vpc.app.repository")
    def test_rolled_back_status(self, mock_repo):
        mock_repo.get_request.return_value = {
            "requestId": "req-rb",
            "status": "ROLLED_BACK",
            "requestedBy": "sub-xyz",
            "createdAt": "2026-09-28T14:10:00Z",
            "updatedAt": "2026-09-28T14:12:00Z",
            "error": {
                "code": "PROVISIONING_FAILED_COMPENSATED",
                "message": "Resources cleaned up.",
            },
        }
        response = handler(_make_event("req-rb"), MOCK_CONTEXT)
        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert body["status"] == "ROLLED_BACK"
