"""
Unit tests for src/common/models.py

Validates Pydantic model constraints: extra field rejection,
reserved tag enforcement, subnet uniqueness, field limits.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError as PydanticValidationError

from src.common.models import (
    AcceptedResponse,
    CreateVpcRequest,
    ProvisioningStatus,
    SubnetRequest,
    VpcStatusResponse,
    WorkflowInput,
)


# ── SubnetRequest ─────────────────────────────────────────────────────────────

class TestSubnetRequest:
    def test_valid_subnet(self):
        s = SubnetRequest(name="app-a", cidrBlock="10.0.1.0/24", availabilityZone="ap-south-1a")
        assert s.name == "app-a"

    def test_extra_field_rejected(self):
        with pytest.raises(PydanticValidationError, match="Extra inputs"):
            SubnetRequest(
                name="a", cidrBlock="10.0.1.0/24",
                availabilityZone="ap-south-1a", unknown_field="oops"
            )

    def test_aws_prefix_name_rejected(self):
        with pytest.raises(PydanticValidationError, match="aws:"):
            SubnetRequest(name="aws:reserved", cidrBlock="10.0.1.0/24", availabilityZone="ap-south-1a")

    def test_empty_name_rejected(self):
        with pytest.raises(PydanticValidationError):
            SubnetRequest(name="", cidrBlock="10.0.1.0/24", availabilityZone="ap-south-1a")


# ── CreateVpcRequest ──────────────────────────────────────────────────────────

class TestCreateVpcRequest:
    def _base_data(self, **overrides) -> dict:
        data = {
            "name": "test-vpc",
            "cidrBlock": "10.20.0.0/16",
            "subnets": [
                {"name": "app-a", "cidrBlock": "10.20.1.0/24", "availabilityZone": "ap-south-1a"},
            ],
        }
        data.update(overrides)
        return data

    def test_minimal_valid_request(self):
        req = CreateVpcRequest.model_validate(self._base_data())
        assert req.name == "test-vpc"
        assert req.enableDnsSupport is True
        assert req.enableDnsHostnames is True
        assert req.clientRequestId is None
        assert req.tags == {}

    def test_with_client_request_id(self):
        data = self._base_data(clientRequestId="demo-001")
        req = CreateVpcRequest.model_validate(data)
        assert req.clientRequestId == "demo-001"

    def test_extra_field_rejected(self):
        data = self._base_data(unknownField="oops")
        with pytest.raises(PydanticValidationError, match="Extra inputs"):
            CreateVpcRequest.model_validate(data)

    def test_empty_subnet_list_rejected(self):
        data = self._base_data(subnets=[])
        with pytest.raises(PydanticValidationError):
            CreateVpcRequest.model_validate(data)

    def test_duplicate_subnet_names_rejected(self):
        data = self._base_data(subnets=[
            {"name": "app-a", "cidrBlock": "10.20.1.0/24", "availabilityZone": "ap-south-1a"},
            {"name": "app-a", "cidrBlock": "10.20.2.0/24", "availabilityZone": "ap-south-1b"},
        ])
        with pytest.raises(PydanticValidationError, match="unique"):
            CreateVpcRequest.model_validate(data)

    def test_reserved_tag_key_rejected(self):
        data = self._base_data(tags={"RequestId": "hacked"})
        with pytest.raises(PydanticValidationError, match="reserved"):
            CreateVpcRequest.model_validate(data)

    def test_aws_prefix_tag_rejected(self):
        data = self._base_data(tags={"aws:something": "value"})
        with pytest.raises(PydanticValidationError, match="aws:"):
            CreateVpcRequest.model_validate(data)

    def test_too_many_tags_rejected(self):
        data = self._base_data(tags={f"key{i}": "val" for i in range(51)})
        with pytest.raises(PydanticValidationError, match="50 tags"):
            CreateVpcRequest.model_validate(data)

    def test_valid_tags_accepted(self):
        data = self._base_data(tags={"Owner": "team", "CostCenter": "eng"})
        req = CreateVpcRequest.model_validate(data)
        assert req.tags["Owner"] == "team"

    def test_dns_support_defaults_true(self):
        req = CreateVpcRequest.model_validate(self._base_data())
        assert req.enableDnsSupport is True

    def test_dns_support_can_be_disabled(self):
        data = self._base_data(enableDnsSupport=False, enableDnsHostnames=False)
        req = CreateVpcRequest.model_validate(data)
        assert req.enableDnsSupport is False


# ── ProvisioningStatus ────────────────────────────────────────────────────────

class TestProvisioningStatus:
    def test_all_statuses_exist(self):
        statuses = {s.value for s in ProvisioningStatus}
        assert statuses == {"PENDING", "PROVISIONING", "SUCCEEDED", "FAILED", "ROLLED_BACK"}

    def test_status_from_string(self):
        assert ProvisioningStatus("SUCCEEDED") == ProvisioningStatus.SUCCEEDED


# ── AcceptedResponse ──────────────────────────────────────────────────────────

class TestAcceptedResponse:
    def test_default_status_is_pending(self):
        r = AcceptedResponse(requestId="abc-123", statusUrl="/vpcs/abc-123")
        assert r.status == ProvisioningStatus.PENDING

    def test_serialization(self):
        r = AcceptedResponse(requestId="abc-123", statusUrl="/vpcs/abc-123")
        d = r.model_dump()
        assert d["requestId"] == "abc-123"
        assert d["status"] == ProvisioningStatus.PENDING


# ── VpcStatusResponse ─────────────────────────────────────────────────────────

class TestVpcStatusResponse:
    def test_minimal_response(self):
        r = VpcStatusResponse(requestId="abc", status=ProvisioningStatus.PENDING)
        assert r.vpc is None
        assert r.subnets == []
        assert r.error is None

    def test_extra_fields_ignored(self):
        # extra="ignore" on model_config
        r = VpcStatusResponse.model_validate({
            "requestId": "abc",
            "status": "SUCCEEDED",
            "unknownField": "should-be-ignored",
        })
        assert r.requestId == "abc"


# ── WorkflowInput ─────────────────────────────────────────────────────────────

class TestWorkflowInput:
    def test_valid_workflow_input(self):
        wi = WorkflowInput(
            requestId="req-1",
            cidrBlock="10.0.0.0/16",
            enableDnsSupport=True,
            enableDnsHostnames=True,
            vpcName="test",
            subnets=[SubnetRequest(name="a", cidrBlock="10.0.1.0/24", availabilityZone="ap-south-1a")],
            tags={},
            tableName="TestTable",
            region="ap-south-1",
        )
        assert wi.requestId == "req-1"

    def test_extra_field_rejected(self):
        with pytest.raises(PydanticValidationError, match="Extra inputs"):
            WorkflowInput(
                requestId="req-1", cidrBlock="10.0.0.0/16",
                enableDnsSupport=True, enableDnsHostnames=True,
                vpcName="test", subnets=[], tags={},
                tableName="T", region="ap-south-1",
                unknownField="oops",
            )
