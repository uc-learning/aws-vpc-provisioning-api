"""
Pydantic v2 request and response models for the VPC Provisioning API.

All models are strict where practical so that unknown fields are rejected
and type coercions are explicit, matching the design doc's strict validation mode.
"""
from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ── Constants ─────────────────────────────────────────────────────────────────

MAX_SUBNETS = 20  # configurable application limit
MAX_NAME_LEN = 256
MAX_TAG_KEY_LEN = 128
MAX_TAG_VALUE_LEN = 256
MAX_TAGS = 50
RESERVED_TAG_KEYS = {"RequestId", "ManagedBy", "Environment"}


# ── Enumerations ──────────────────────────────────────────────────────────────


class ProvisioningStatus(str, Enum):
    PENDING = "PENDING"
    PROVISIONING = "PROVISIONING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    ROLLED_BACK = "ROLLED_BACK"


# ── Sub-models ────────────────────────────────────────────────────────────────


class SubnetRequest(BaseModel):
    """A single subnet definition inside a VPC create request."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=MAX_NAME_LEN)
    cidrBlock: str = Field(..., description="IPv4 CIDR for this subnet, e.g. 10.20.1.0/24")
    availabilityZone: str = Field(..., min_length=1, max_length=64)

    @field_validator("name")
    @classmethod
    def name_no_reserved_prefix(cls, v: str) -> str:
        if v.startswith("aws:"):
            raise ValueError("Subnet name must not start with 'aws:'")
        return v


class SubnetResult(BaseModel):
    """Subnet details returned in a GET response after successful provisioning."""

    model_config = ConfigDict(extra="ignore")

    name: str
    subnetId: str
    cidrBlock: str
    availabilityZone: str
    state: str | None = None


class VpcResult(BaseModel):
    """VPC details returned in a GET response after successful provisioning."""

    model_config = ConfigDict(extra="ignore")

    vpcId: str
    cidrBlock: str
    state: str | None = None


# ── Primary request model ─────────────────────────────────────────────────────


class CreateVpcRequest(BaseModel):
    """
    POST /vpcs request body.

    clientRequestId is optional but strongly recommended for idempotency.
    Reserved tags (RequestId, ManagedBy) are injected by the service and
    must not be provided by the caller.
    """

    model_config = ConfigDict(extra="forbid")

    clientRequestId: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        description="Caller-supplied idempotency key",
    )
    name: str = Field(..., min_length=1, max_length=MAX_NAME_LEN)
    cidrBlock: str = Field(..., description="VPC CIDR block, e.g. 10.20.0.0/16")
    enableDnsSupport: bool = Field(default=True)
    enableDnsHostnames: bool = Field(default=True)
    subnets: list[SubnetRequest] = Field(..., min_length=1, max_length=MAX_SUBNETS)
    tags: dict[str, str] = Field(default_factory=dict)

    @field_validator("tags")
    @classmethod
    def tags_valid(cls, v: dict[str, str]) -> dict[str, str]:
        if len(v) > MAX_TAGS:
            raise ValueError(f"At most {MAX_TAGS} tags are allowed")
        for key, value in v.items():
            if not key or len(key) > MAX_TAG_KEY_LEN:
                raise ValueError(
                    f"Tag key '{key}' exceeds {MAX_TAG_KEY_LEN} characters or is empty"
                )
            if len(value) > MAX_TAG_VALUE_LEN:
                raise ValueError(
                    f"Tag value for key '{key}' exceeds {MAX_TAG_VALUE_LEN} characters"
                )
            if key.lower().startswith("aws:"):
                raise ValueError(f"Tag key '{key}' must not start with 'aws:'")
            if key in RESERVED_TAG_KEYS:
                raise ValueError(
                    f"Tag key '{key}' is reserved and controlled by the service"
                )
        return v

    @model_validator(mode="after")
    def subnet_names_unique(self) -> "CreateVpcRequest":
        names = [s.name for s in self.subnets]
        if len(names) != len(set(names)):
            raise ValueError("Subnet names must be unique within a request")
        return self


# ── Response models ───────────────────────────────────────────────────────────


class AcceptedResponse(BaseModel):
    """202 Accepted body returned immediately after POST /vpcs."""

    requestId: str
    status: ProvisioningStatus = ProvisioningStatus.PENDING
    statusUrl: str


class VpcStatusResponse(BaseModel):
    """200 OK body returned by GET /vpcs/{requestId}."""

    model_config = ConfigDict(extra="ignore")

    requestId: str
    status: ProvisioningStatus
    requestedBy: str | None = None
    createdAt: str | None = None
    updatedAt: str | None = None
    vpc: VpcResult | None = None
    subnets: list[SubnetResult] = Field(default_factory=list)
    error: dict[str, Any] | None = None


class HealthResponse(BaseModel):
    status: str = "ok"


# ── Internal workflow input ───────────────────────────────────────────────────


class WorkflowInput(BaseModel):
    """
    Payload passed as input to the Step Functions execution.
    The state machine references these exact field names.
    """

    model_config = ConfigDict(extra="forbid")

    requestId: str
    cidrBlock: str
    enableDnsSupport: bool
    enableDnsHostnames: bool
    vpcName: str
    subnets: list[SubnetRequest]
    tags: dict[str, str]
    tableName: str
    region: str
