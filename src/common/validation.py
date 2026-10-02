"""
Pure-Python CIDR / subnet / AZ validation functions.

These functions have no AWS SDK dependency, making them fast and easy
to unit-test without mocking. They are called from the Lambda handler
*before* any DynamoDB or Step Functions call so that invalid payloads
are rejected cheaply with a 400 response.

Limits (prefix length, AZ allow-list, max subnets) are read from
AppConfig so that they can be tuned per-environment via Secrets Manager
without redeploying the Lambda.
"""
from __future__ import annotations

import ipaddress
import os
import re
from typing import TYPE_CHECKING

from .models import CreateVpcRequest, SubnetRequest

if TYPE_CHECKING:
    from .config import AppConfig

# ── Static defaults (used when AppConfig is not available, e.g. unit tests) ──

VPC_MIN_PREFIX = 16
VPC_MAX_PREFIX = 28
AWS_REGION = os.environ.get("AWS_REGION", "ap-south-1")

# Availability Zone pattern: <region-name><letter>  e.g. ap-south-1a
_AZ_RE = re.compile(r"^[a-z]{2}-[a-z]+-\d+[a-z]$")


# ── Exceptions ────────────────────────────────────────────────────────────────


class ValidationError(Exception):
    """Raised when any validation rule fails; carries a human-readable message."""

    def __init__(self, message: str, field: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.field = field


# ── Individual validators ─────────────────────────────────────────────────────


def validate_ipv4_cidr(cidr: str, field: str = "cidrBlock") -> ipaddress.IPv4Network:
    """
    Parse and return an IPv4Network (strict=False so host bits are zeroed).
    Raises ValidationError if the string is not a valid IPv4 CIDR.
    """
    try:
        network = ipaddress.IPv4Network(cidr, strict=False)
    except ValueError as exc:
        raise ValidationError(f"'{cidr}' is not a valid IPv4 CIDR: {exc}", field) from exc
    return network


def validate_vpc_cidr(
    cidr: str,
    min_prefix: int = VPC_MIN_PREFIX,
    max_prefix: int = VPC_MAX_PREFIX,
) -> ipaddress.IPv4Network:
    """
    Validate that a VPC CIDR is:
      - A valid IPv4 CIDR
      - Within the permitted prefix range (default /16–/28, configurable via AppConfig)
      - From RFC 1918 private address space
    """
    network = validate_ipv4_cidr(cidr, "cidrBlock")
    prefix = network.prefixlen
    if prefix < min_prefix or prefix > max_prefix:
        raise ValidationError(
            f"VPC CIDR prefix length /{prefix} is outside the allowed range "
            f"/{min_prefix}–/{max_prefix}",
            "cidrBlock",
        )
    private_ranges = [
        ipaddress.IPv4Network("10.0.0.0/8"),
        ipaddress.IPv4Network("172.16.0.0/12"),
        ipaddress.IPv4Network("192.168.0.0/16"),
    ]
    if not any(network.subnet_of(private) for private in private_ranges):
        raise ValidationError(
            f"VPC CIDR '{cidr}' must be within RFC 1918 private address space",
            "cidrBlock",
        )
    return network


def validate_subnet_contained(
    subnet_cidr: str,
    vpc_network: ipaddress.IPv4Network,
    subnet_name: str,
) -> ipaddress.IPv4Network:
    """Validate that a subnet CIDR is wholly contained within the VPC CIDR."""
    subnet_network = validate_ipv4_cidr(subnet_cidr, f"subnets[{subnet_name}].cidrBlock")
    if not subnet_network.subnet_of(vpc_network):
        raise ValidationError(
            f"Subnet '{subnet_name}' CIDR '{subnet_cidr}' is not contained "
            f"within VPC CIDR '{vpc_network}'",
            f"subnets[{subnet_name}].cidrBlock",
        )
    return subnet_network


def validate_subnets_no_overlap(
    subnets: list[SubnetRequest],
    vpc_network: ipaddress.IPv4Network,
) -> None:
    """
    Validate that:
    1. Every subnet CIDR is contained within the VPC CIDR.
    2. No two subnet CIDRs overlap each other.
    """
    parsed: list[tuple[str, ipaddress.IPv4Network]] = []
    for subnet in subnets:
        net = validate_subnet_contained(subnet.cidrBlock, vpc_network, subnet.name)
        parsed.append((subnet.name, net))

    # O(n²) overlap check — acceptable for ≤20 subnets
    for i in range(len(parsed)):
        for j in range(i + 1, len(parsed)):
            name_a, net_a = parsed[i]
            name_b, net_b = parsed[j]
            if net_a.overlaps(net_b):
                raise ValidationError(
                    f"Subnet '{name_a}' ({net_a}) overlaps with subnet '{name_b}' ({net_b})",
                    "subnets",
                )


def validate_availability_zones(
    subnets: list[SubnetRequest],
    region: str,
    allowed_azs: list[str] | None = None,
) -> None:
    """
    Validate that every subnet AZ:
    1. Matches the AZ naming pattern (<region><letter>).
    2. Belongs to the configured region.
    3. Is in the allow-list if one is configured via Secrets Manager.

    NOTE: This is a static/syntactic check. Dynamic verification against
    ec2:DescribeAvailabilityZones happens inside the Step Functions workflow
    to avoid giving EC2 permissions to the Create Lambda.
    """
    for subnet in subnets:
        az = subnet.availabilityZone
        if not _AZ_RE.match(az):
            raise ValidationError(
                f"Availability Zone '{az}' does not match the expected pattern "
                f"'<region><letter>' (e.g. ap-south-1a)",
                f"subnets[{subnet.name}].availabilityZone",
            )
        if not az.startswith(region):
            raise ValidationError(
                f"Availability Zone '{az}' does not belong to region '{region}'",
                f"subnets[{subnet.name}].availabilityZone",
            )
        if allowed_azs and az not in allowed_azs:
            raise ValidationError(
                f"Availability Zone '{az}' is not in the allowed list for this environment",
                f"subnets[{subnet.name}].availabilityZone",
            )


# ── Composite entry point ─────────────────────────────────────────────────────


def validate_create_request(
    request: CreateVpcRequest,
    region: str = AWS_REGION,
    config: "AppConfig | None" = None,
) -> None:
    """
    Run all cross-field validation rules on a parsed CreateVpcRequest.

    When `config` is provided (production code path), limits and allow-lists
    are read from AppConfig (which sources them from Secrets Manager).
    When `config` is None (unit tests), static module-level defaults are used.

    Raises ValidationError on the first failing rule.
    Pydantic field-level validation has already run before this is called.
    """
    min_prefix  = config.vpc_cidr_min_prefix  if config else VPC_MIN_PREFIX
    max_prefix  = config.vpc_cidr_max_prefix  if config else VPC_MAX_PREFIX
    allowed_azs = config.allowed_azs          if config else None

    vpc_network = validate_vpc_cidr(request.cidrBlock, min_prefix, max_prefix)
    validate_subnets_no_overlap(request.subnets, vpc_network)
    validate_availability_zones(request.subnets, region, allowed_azs or None)
