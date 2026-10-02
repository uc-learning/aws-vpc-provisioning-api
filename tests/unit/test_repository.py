"""
Unit tests for src/common/repository.py

Uses moto to mock DynamoDB entirely — no real AWS calls are made.
"""
from __future__ import annotations

import os

import boto3
import pytest

# Set env before importing the module so it picks up the test table name
os.environ.setdefault("AWS_DEFAULT_REGION", "ap-south-1")
os.environ.setdefault("DYNAMODB_TABLE", "VpcProvisioningRequests-test")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ.setdefault("AWS_SECURITY_TOKEN", "testing")
os.environ.setdefault("AWS_SESSION_TOKEN", "testing")

from moto import mock_aws

from src.common.models import ProvisioningStatus
from src.common.repository import (
    DuplicateRequestError,
    RequestNotFoundError,
    attach_execution,
    compute_request_hash,
    create_if_absent,
    get_request,
    update_status,
)

TEST_TABLE = "VpcProvisioningRequests-test"


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def dynamodb_table():
    """Create a mocked DynamoDB table for each test."""
    with mock_aws():
        client = boto3.client("dynamodb", region_name="ap-south-1")
        client.create_table(
            TableName=TEST_TABLE,
            BillingMode="PAY_PER_REQUEST",
            AttributeDefinitions=[
                {"AttributeName": "requestId", "AttributeType": "S"},
                {"AttributeName": "clientRequestId", "AttributeType": "S"},
            ],
            KeySchema=[{"AttributeName": "requestId", "KeyType": "HASH"}],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "ClientRequestIdIndex",
                    "KeySchema": [{"AttributeName": "clientRequestId", "KeyType": "HASH"}],
                    "Projection": {
                        "ProjectionType": "INCLUDE",
                        "NonKeyAttributes": ["requestId", "requestHash", "status"],
                    },
                }
            ],
        )
        yield


# ── compute_request_hash ──────────────────────────────────────────────────────

class TestComputeRequestHash:
    def test_same_payload_same_hash(self):
        body = {"name": "test", "cidrBlock": "10.0.0.0/16", "subnets": []}
        assert compute_request_hash(body) == compute_request_hash(body)

    def test_different_payload_different_hash(self):
        body_a = {"name": "test-a", "cidrBlock": "10.0.0.0/16"}
        body_b = {"name": "test-b", "cidrBlock": "10.0.0.0/16"}
        assert compute_request_hash(body_a) != compute_request_hash(body_b)

    def test_client_request_id_excluded_from_hash(self):
        body_with = {"clientRequestId": "crid-1", "name": "test", "cidrBlock": "10.0.0.0/16"}
        body_without = {"name": "test", "cidrBlock": "10.0.0.0/16"}
        assert compute_request_hash(body_with) == compute_request_hash(body_without)

    def test_hash_is_deterministic_regardless_of_key_order(self):
        body_a = {"cidrBlock": "10.0.0.0/16", "name": "test"}
        body_b = {"name": "test", "cidrBlock": "10.0.0.0/16"}
        assert compute_request_hash(body_a) == compute_request_hash(body_b)


# ── create_if_absent ──────────────────────────────────────────────────────────

class TestCreateIfAbsent:
    def test_new_request_returns_none(self, dynamodb_table):
        result = create_if_absent(
            request_id="req-001",
            client_request_id=None,
            request_hash="hash-abc",
            requested_by="sub-xyz",
            request_body={"name": "vpc-1"},
            table_name=TEST_TABLE,
        )
        assert result is None

    def test_record_is_persisted(self, dynamodb_table):
        create_if_absent(
            request_id="req-002",
            client_request_id=None,
            request_hash="hash-abc",
            requested_by="sub-xyz",
            request_body={"name": "vpc-1"},
            table_name=TEST_TABLE,
        )
        item = get_request("req-002", table_name=TEST_TABLE)
        assert item["requestId"] == "req-002"
        assert item["status"] == ProvisioningStatus.PENDING.value
        assert item["requestedBy"] == "sub-xyz"

    def test_idempotent_replay_returns_existing(self, dynamodb_table):
        body = {"name": "vpc-1"}
        request_hash = compute_request_hash(body)

        # First call: creates the record
        create_if_absent(
            request_id="req-003",
            client_request_id="crid-abc",
            request_hash=request_hash,
            requested_by="sub-xyz",
            request_body=body,
            table_name=TEST_TABLE,
        )

        # Second call with same clientRequestId + same hash: idempotent replay
        existing = create_if_absent(
            request_id="req-003-new-uuid",
            client_request_id="crid-abc",
            request_hash=request_hash,
            requested_by="sub-xyz",
            request_body=body,
            table_name=TEST_TABLE,
        )
        assert existing is not None
        assert existing["requestId"] == "req-003"

    def test_duplicate_client_request_id_different_payload_raises(self, dynamodb_table):
        body_original = {"name": "vpc-original"}
        body_different = {"name": "vpc-different"}
        hash_original = compute_request_hash(body_original)
        hash_different = compute_request_hash(body_different)

        create_if_absent(
            request_id="req-004",
            client_request_id="crid-dup",
            request_hash=hash_original,
            requested_by="sub-xyz",
            request_body=body_original,
            table_name=TEST_TABLE,
        )

        with pytest.raises(DuplicateRequestError) as exc_info:
            create_if_absent(
                request_id="req-005",
                client_request_id="crid-dup",
                request_hash=hash_different,
                requested_by="sub-xyz",
                request_body=body_different,
                table_name=TEST_TABLE,
            )
        assert exc_info.value.existing_request_id == "req-004"


# ── attach_execution ──────────────────────────────────────────────────────────

class TestAttachExecution:
    def test_attach_execution_arn(self, dynamodb_table):
        create_if_absent(
            request_id="req-010",
            client_request_id=None,
            request_hash="hash",
            requested_by="sub",
            request_body={},
            table_name=TEST_TABLE,
        )
        attach_execution("req-010", "arn:aws:states:ap-south-1:123:execution:sfn:exec-1", table_name=TEST_TABLE)
        item = get_request("req-010", table_name=TEST_TABLE)
        assert item["executionArn"] == "arn:aws:states:ap-south-1:123:execution:sfn:exec-1"


# ── get_request ───────────────────────────────────────────────────────────────

class TestGetRequest:
    def test_not_found_raises(self, dynamodb_table):
        with pytest.raises(RequestNotFoundError) as exc_info:
            get_request("nonexistent-id", table_name=TEST_TABLE)
        assert "nonexistent-id" in str(exc_info.value)

    def test_found_returns_item(self, dynamodb_table):
        create_if_absent(
            request_id="req-020",
            client_request_id=None,
            request_hash="h",
            requested_by="s",
            request_body={"x": 1},
            table_name=TEST_TABLE,
        )
        item = get_request("req-020", table_name=TEST_TABLE)
        assert item["requestId"] == "req-020"


# ── update_status ─────────────────────────────────────────────────────────────

class TestUpdateStatus:
    def test_transition_pending_to_provisioning(self, dynamodb_table):
        create_if_absent(
            request_id="req-030",
            client_request_id=None,
            request_hash="h",
            requested_by="s",
            request_body={},
            table_name=TEST_TABLE,
        )
        update_status(
            "req-030",
            new_status=ProvisioningStatus.PROVISIONING,
            expected_status=ProvisioningStatus.PENDING,
            table_name=TEST_TABLE,
        )
        item = get_request("req-030", table_name=TEST_TABLE)
        assert item["status"] == ProvisioningStatus.PROVISIONING.value

    def test_update_with_extra_attrs(self, dynamodb_table):
        create_if_absent(
            request_id="req-031",
            client_request_id=None,
            request_hash="h",
            requested_by="s",
            request_body={},
            table_name=TEST_TABLE,
        )
        vpc_data = {"vpcId": "vpc-abc123", "cidrBlock": "10.0.0.0/16", "state": "available"}
        update_status(
            "req-031",
            new_status=ProvisioningStatus.SUCCEEDED,
            expected_status=ProvisioningStatus.PENDING,
            extra_attrs={"vpc": vpc_data},
            table_name=TEST_TABLE,
        )
        item = get_request("req-031", table_name=TEST_TABLE)
        assert item["status"] == ProvisioningStatus.SUCCEEDED.value
        assert item["vpc"]["vpcId"] == "vpc-abc123"
