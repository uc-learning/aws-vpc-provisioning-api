"""
Unit tests for src/common/validation.py

Tests run with no AWS credentials or network access.
"""
from __future__ import annotations

import pytest

from src.common.models import CreateVpcRequest, SubnetRequest
from src.common.validation import (
    ValidationError,
    validate_availability_zones,
    validate_create_request,
    validate_ipv4_cidr,
    validate_subnet_contained,
    validate_subnets_no_overlap,
    validate_vpc_cidr,
)


# ── validate_ipv4_cidr ────────────────────────────────────────────────────────

class TestValidateIpv4Cidr:
    def test_valid_cidr(self):
        net = validate_ipv4_cidr("10.0.0.0/16")
        assert str(net) == "10.0.0.0/16"

    def test_valid_cidr_host_bits_ignored(self):
        # strict=False means host bits are zeroed automatically
        net = validate_ipv4_cidr("10.0.1.5/16")
        assert str(net) == "10.0.0.0/16"

    def test_invalid_cidr_raises(self):
        with pytest.raises(ValidationError, match="not a valid IPv4 CIDR"):
            validate_ipv4_cidr("not-a-cidr")

    def test_ipv6_raises(self):
        with pytest.raises(ValidationError):
            validate_ipv4_cidr("2001:db8::/32")

    def test_empty_string_raises(self):
        with pytest.raises(ValidationError):
            validate_ipv4_cidr("")


# ── validate_vpc_cidr ─────────────────────────────────────────────────────────

class TestValidateVpcCidr:
    @pytest.mark.parametrize("cidr", [
        "10.0.0.0/16",
        "10.20.0.0/16",
        "172.16.0.0/16",
        "192.168.0.0/24",
        "10.0.0.0/28",
    ])
    def test_valid_vpc_cidrs(self, cidr):
        net = validate_vpc_cidr(cidr)
        assert net is not None

    def test_prefix_too_small_raises(self):
        with pytest.raises(ValidationError, match="prefix length"):
            validate_vpc_cidr("10.0.0.0/15")  # /15 < /16 minimum

    def test_prefix_too_large_raises(self):
        with pytest.raises(ValidationError, match="prefix length"):
            validate_vpc_cidr("10.0.0.0/29")  # /29 > /28 maximum

    def test_public_cidr_raises(self):
        with pytest.raises(ValidationError, match="RFC 1918"):
            validate_vpc_cidr("8.8.0.0/16")  # public IP range

    def test_boundary_slash16_valid(self):
        validate_vpc_cidr("10.0.0.0/16")

    def test_boundary_slash28_valid(self):
        validate_vpc_cidr("10.0.0.0/28")


# ── validate_subnet_contained ─────────────────────────────────────────────────

class TestValidateSubnetContained:
    def test_subnet_inside_vpc(self):
        import ipaddress
        vpc = ipaddress.IPv4Network("10.20.0.0/16")
        net = validate_subnet_contained("10.20.1.0/24", vpc, "app-a")
        assert str(net) == "10.20.1.0/24"

    def test_subnet_outside_vpc_raises(self):
        import ipaddress
        vpc = ipaddress.IPv4Network("10.20.0.0/16")
        with pytest.raises(ValidationError, match="not contained"):
            validate_subnet_contained("10.30.1.0/24", vpc, "app-a")

    def test_subnet_equal_to_vpc_raises(self):
        # Subnet that exactly equals VPC CIDR is contained but bad practice;
        # AWS will reject it, but our validator allows it (AWS enforces further).
        import ipaddress
        vpc = ipaddress.IPv4Network("10.20.0.0/16")
        # This should pass our validator — AWS enforces further constraints
        validate_subnet_contained("10.20.0.0/16", vpc, "app-a")


# ── validate_subnets_no_overlap ───────────────────────────────────────────────

class TestValidateSubnetsNoOverlap:
    def _make_subnet(self, name: str, cidr: str, az: str = "ap-south-1a") -> SubnetRequest:
        return SubnetRequest(name=name, cidrBlock=cidr, availabilityZone=az)

    def test_non_overlapping_subnets_pass(self):
        import ipaddress
        vpc = ipaddress.IPv4Network("10.20.0.0/16")
        subnets = [
            self._make_subnet("a", "10.20.1.0/24"),
            self._make_subnet("b", "10.20.2.0/24"),
            self._make_subnet("c", "10.20.3.0/24"),
        ]
        validate_subnets_no_overlap(subnets, vpc)  # no exception

    def test_overlapping_subnets_raise(self):
        import ipaddress
        vpc = ipaddress.IPv4Network("10.20.0.0/16")
        subnets = [
            self._make_subnet("a", "10.20.1.0/24"),
            self._make_subnet("b", "10.20.1.128/25"),  # overlaps with a
        ]
        with pytest.raises(ValidationError, match="overlaps"):
            validate_subnets_no_overlap(subnets, vpc)

    def test_subnet_outside_vpc_raises(self):
        import ipaddress
        vpc = ipaddress.IPv4Network("10.20.0.0/16")
        subnets = [self._make_subnet("a", "192.168.1.0/24")]
        with pytest.raises(ValidationError, match="not contained"):
            validate_subnets_no_overlap(subnets, vpc)

    def test_identical_cidrs_raise(self):
        import ipaddress
        vpc = ipaddress.IPv4Network("10.20.0.0/16")
        subnets = [
            self._make_subnet("a", "10.20.1.0/24"),
            self._make_subnet("b", "10.20.1.0/24"),  # identical
        ]
        with pytest.raises(ValidationError, match="overlaps"):
            validate_subnets_no_overlap(subnets, vpc)


# ── validate_availability_zones ───────────────────────────────────────────────

class TestValidateAvailabilityZones:
    def _make_subnet(self, name: str, az: str) -> SubnetRequest:
        return SubnetRequest(name=name, cidrBlock="10.0.1.0/24", availabilityZone=az)

    def test_valid_az_passes(self):
        subnets = [self._make_subnet("a", "ap-south-1a")]
        validate_availability_zones(subnets, "ap-south-1")  # no exception

    def test_wrong_region_raises(self):
        subnets = [self._make_subnet("a", "us-east-1a")]
        with pytest.raises(ValidationError, match="does not belong to region"):
            validate_availability_zones(subnets, "ap-south-1")

    def test_invalid_az_pattern_raises(self):
        subnets = [self._make_subnet("a", "invalid-zone")]
        with pytest.raises(ValidationError, match="does not match"):
            validate_availability_zones(subnets, "ap-south-1")

    def test_multiple_valid_azs(self):
        subnets = [
            self._make_subnet("a", "ap-south-1a"),
            self._make_subnet("b", "ap-south-1b"),
        ]
        validate_availability_zones(subnets, "ap-south-1")


# ── validate_create_request (composite) ──────────────────────────────────────

class TestValidateCreateRequest:
    def _valid_request(self, **overrides) -> CreateVpcRequest:
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
        return CreateVpcRequest.model_validate(data)

    def test_valid_request_passes(self):
        req = self._valid_request()
        validate_create_request(req, region="ap-south-1")  # no exception

    def test_invalid_vpc_cidr_fails(self):
        req = self._valid_request(cidrBlock="10.0.0.0/15")
        with pytest.raises(ValidationError, match="prefix length"):
            validate_create_request(req, region="ap-south-1")

    def test_overlapping_subnets_fail(self):
        req = self._valid_request(subnets=[
            {"name": "a", "cidrBlock": "10.20.1.0/24", "availabilityZone": "ap-south-1a"},
            {"name": "b", "cidrBlock": "10.20.1.128/25", "availabilityZone": "ap-south-1b"},
        ])
        with pytest.raises(ValidationError, match="overlaps"):
            validate_create_request(req, region="ap-south-1")

    def test_subnet_outside_vpc_fails(self):
        req = self._valid_request(subnets=[
            {"name": "a", "cidrBlock": "192.168.1.0/24", "availabilityZone": "ap-south-1a"},
        ])
        with pytest.raises(ValidationError, match="not contained"):
            validate_create_request(req, region="ap-south-1")

    def test_wrong_az_region_fails(self):
        req = self._valid_request(subnets=[
            {"name": "a", "cidrBlock": "10.20.1.0/24", "availabilityZone": "us-east-1a"},
        ])
        with pytest.raises(ValidationError, match="does not belong to region"):
            validate_create_request(req, region="ap-south-1")
