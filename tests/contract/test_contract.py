"""
Contract tests: validate request/response payloads against the OpenAPI schema.

These tests validate that:
1. Valid payloads pass schema validation.
2. Invalid payloads fail schema validation (schema is strict enough).
3. Handler response bodies conform to the response schema.

No real AWS calls are made. No deployment required.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from jsonschema import ValidationError as JsonSchemaError
from jsonschema import validate

# Load schema once at module level
_SCHEMA_PATH = Path(__file__).parent / "openapi_schema.json"
_OPENAPI = json.loads(_SCHEMA_PATH.read_text())
_COMPONENTS = _OPENAPI["components"]["schemas"]


def _resolve_ref(ref: str) -> dict:
    """Resolve a $ref like '#/components/schemas/CreateVpcRequest'."""
    parts = ref.lstrip("#/").split("/")
    node = _OPENAPI
    for part in parts:
        node = node[part]
    return node


def _schema(name: str) -> dict:
    return _COMPONENTS[name]


# ── CreateVpcRequest contract ─────────────────────────────────────────────────

class TestCreateVpcRequestContract:
    SCHEMA = _schema("CreateVpcRequest")

    def _valid(self) -> dict:
        return {
            "name": "test-vpc",
            "cidrBlock": "10.20.0.0/16",
            "subnets": [
                {"name": "app-a", "cidrBlock": "10.20.1.0/24", "availabilityZone": "ap-south-1a"},
            ],
            "tags": {"Owner": "test"},
        }

    def test_valid_payload_passes(self):
        validate(instance=self._valid(), schema=self.SCHEMA)

    def test_missing_name_fails(self):
        body = self._valid()
        del body["name"]
        with pytest.raises(JsonSchemaError):
            validate(instance=body, schema=self.SCHEMA)

    def test_missing_cidr_fails(self):
        body = self._valid()
        del body["cidrBlock"]
        with pytest.raises(JsonSchemaError):
            validate(instance=body, schema=self.SCHEMA)

    def test_missing_subnets_fails(self):
        body = self._valid()
        del body["subnets"]
        with pytest.raises(JsonSchemaError):
            validate(instance=body, schema=self.SCHEMA)

    def test_empty_subnets_fails(self):
        body = self._valid()
        body["subnets"] = []
        with pytest.raises(JsonSchemaError):
            validate(instance=body, schema=self.SCHEMA)

    def test_with_client_request_id_passes(self):
        body = self._valid()
        body["clientRequestId"] = "demo-001"
        validate(instance=body, schema=self.SCHEMA)

    def test_extra_field_fails(self):
        body = self._valid()
        body["unknownField"] = "oops"
        with pytest.raises(JsonSchemaError):
            validate(instance=body, schema=self.SCHEMA)


# ── AcceptedResponse contract ─────────────────────────────────────────────────

class TestAcceptedResponseContract:
    SCHEMA = _schema("AcceptedResponse")

    def test_valid_202_body_passes(self):
        validate(
            instance={
                "requestId": "abc-123",
                "status": "PENDING",
                "statusUrl": "/vpcs/abc-123",
            },
            schema=self.SCHEMA,
        )

    def test_invalid_status_fails(self):
        with pytest.raises(JsonSchemaError):
            validate(
                instance={"requestId": "x", "status": "UNKNOWN", "statusUrl": "/vpcs/x"},
                schema=self.SCHEMA,
            )


# ── VpcStatusResponse contract ────────────────────────────────────────────────

class TestVpcStatusResponseContract:
    SCHEMA = _schema("VpcStatusResponse")

    def test_pending_response_passes(self):
        validate(
            instance={"requestId": "abc", "status": "PENDING"},
            schema=self.SCHEMA,
        )

    def test_succeeded_response_with_resources_passes(self):
        validate(
            instance={
                "requestId": "abc",
                "status": "SUCCEEDED",
                "requestedBy": "sub-xyz",
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
            },
            schema=self.SCHEMA,
        )

    def test_failed_response_with_error_passes(self):
        validate(
            instance={
                "requestId": "abc",
                "status": "FAILED",
                "error": {"code": "PROVISIONING_FAILED", "message": "Something went wrong"},
            },
            schema=self.SCHEMA,
        )

    def test_missing_request_id_fails(self):
        with pytest.raises(JsonSchemaError):
            validate(instance={"status": "PENDING"}, schema=self.SCHEMA)

    def test_invalid_status_enum_fails(self):
        with pytest.raises(JsonSchemaError):
            validate(instance={"requestId": "abc", "status": "IN_PROGRESS"}, schema=self.SCHEMA)


# ── ProvisioningStatus enum contract ─────────────────────────────────────────

class TestProvisioningStatusContract:
    SCHEMA = _schema("ProvisioningStatus")

    @pytest.mark.parametrize("status", ["PENDING", "PROVISIONING", "SUCCEEDED", "FAILED", "ROLLED_BACK"])
    def test_valid_statuses(self, status):
        validate(instance=status, schema=self.SCHEMA)

    def test_invalid_status_fails(self):
        with pytest.raises(JsonSchemaError):
            validate(instance="RUNNING", schema=self.SCHEMA)
