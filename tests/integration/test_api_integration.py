"""
Integration tests for the deployed VPC Provisioning API.

Prerequisites (set via environment variables before running):
  API_ENDPOINT  — e.g. https://abc123.execute-api.ap-south-1.amazonaws.com/demo
  COGNITO_TOKEN — a valid Bearer token for an authenticated demo user
                  (obtain via scripts/get_token.sh)

These tests are marked with @pytest.mark.integration and are excluded
from the default pytest run. Run explicitly with:
  pytest tests/integration/ -v -m integration

The tests perform real HTTP calls against a deployed stack and verify
end-to-end behavior including DynamoDB persistence and Step Functions execution.
"""
from __future__ import annotations

import os
import time
import uuid

import pytest
import requests

# ── Fixtures ──────────────────────────────────────────────────────────────────

API_ENDPOINT = os.environ.get("API_ENDPOINT", "").rstrip("/")
COGNITO_TOKEN = os.environ.get("COGNITO_TOKEN", "")
POLL_INTERVAL_SECONDS = 5
MAX_POLL_ATTEMPTS = 24  # 2 minutes total


@pytest.fixture(scope="session")
def api_base_url() -> str:
    if not API_ENDPOINT:
        pytest.skip("API_ENDPOINT environment variable not set — skipping integration tests")
    return API_ENDPOINT


@pytest.fixture(scope="session")
def auth_headers() -> dict[str, str]:
    if not COGNITO_TOKEN:
        pytest.skip("COGNITO_TOKEN environment variable not set — skipping integration tests")
    return {
        "Authorization": f"Bearer {COGNITO_TOKEN}",
        "Content-Type": "application/json",
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

def poll_until_terminal(base_url: str, request_id: str, headers: dict) -> dict:
    """Poll GET /vpcs/{requestId} until a terminal status is reached or timeout."""
    terminal_statuses = {"SUCCEEDED", "FAILED", "ROLLED_BACK"}
    for attempt in range(MAX_POLL_ATTEMPTS):
        resp = requests.get(f"{base_url}/vpcs/{request_id}", headers=headers, timeout=10)
        assert resp.status_code == 200, f"GET failed with {resp.status_code}: {resp.text}"
        body = resp.json()
        if body["status"] in terminal_statuses:
            return body
        time.sleep(POLL_INTERVAL_SECONDS)
    pytest.fail(f"Request {request_id} did not reach terminal status within timeout")


# ── Tests ─────────────────────────────────────────────────────────────────────

@pytest.mark.integration
class TestEndToEnd:

    def test_health_endpoint_unauthenticated(self, api_base_url):
        """Health endpoint must return 200 without authentication."""
        resp = requests.get(f"{api_base_url}/health", timeout=10)
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"

    def test_unauthenticated_post_returns_401(self, api_base_url):
        """POST /vpcs without Authorization header must return 401."""
        resp = requests.post(
            f"{api_base_url}/vpcs",
            json={"name": "test", "cidrBlock": "10.0.0.0/16", "subnets": []},
            timeout=10,
        )
        assert resp.status_code == 401

    def test_unauthenticated_get_returns_401(self, api_base_url):
        """GET /vpcs/{id} without Authorization header must return 401."""
        resp = requests.get(f"{api_base_url}/vpcs/some-request-id", timeout=10)
        assert resp.status_code == 401

    def test_create_and_retrieve_vpc(self, api_base_url, auth_headers):
        """
        Full happy-path: POST creates VPC + 2 subnets, polling GET
        eventually returns SUCCEEDED with vpc and subnet IDs.
        """
        client_request_id = f"integration-test-{uuid.uuid4().hex[:8]}"
        payload = {
            "clientRequestId": client_request_id,
            "name": f"integration-test-vpc-{client_request_id}",
            "cidrBlock": "10.99.0.0/16",
            "enableDnsSupport": True,
            "enableDnsHostnames": True,
            "subnets": [
                {
                    "name": "integration-a",
                    "cidrBlock": "10.99.1.0/24",
                    "availabilityZone": "ap-south-1a",
                },
                {
                    "name": "integration-b",
                    "cidrBlock": "10.99.2.0/24",
                    "availabilityZone": "ap-south-1b",
                },
            ],
            "tags": {"Purpose": "integration-test"},
        }

        # POST
        post_resp = requests.post(f"{api_base_url}/vpcs", json=payload, headers=auth_headers, timeout=15)
        assert post_resp.status_code == 202, f"POST failed: {post_resp.text}"
        post_body = post_resp.json()
        assert "requestId" in post_body
        assert post_body["status"] == "PENDING"
        request_id = post_body["requestId"]

        # Poll until SUCCEEDED
        final = poll_until_terminal(api_base_url, request_id, auth_headers)
        assert final["status"] == "SUCCEEDED", f"Unexpected status: {final}"
        assert final["vpc"]["vpcId"].startswith("vpc-")
        assert len(final["subnets"]) == 2
        subnet_ids = [s["subnetId"] for s in final["subnets"]]
        assert all(sid.startswith("subnet-") for sid in subnet_ids)

    def test_invalid_cidr_returns_400(self, api_base_url, auth_headers):
        resp = requests.post(
            f"{api_base_url}/vpcs",
            json={
                "name": "bad-cidr",
                "cidrBlock": "300.0.0.0/16",
                "subnets": [
                    {"name": "a", "cidrBlock": "10.0.1.0/24", "availabilityZone": "ap-south-1a"}
                ],
            },
            headers=auth_headers,
            timeout=10,
        )
        assert resp.status_code == 400

    def test_overlapping_subnets_returns_400(self, api_base_url, auth_headers):
        resp = requests.post(
            f"{api_base_url}/vpcs",
            json={
                "name": "overlap-test",
                "cidrBlock": "10.50.0.0/16",
                "subnets": [
                    {"name": "a", "cidrBlock": "10.50.1.0/24", "availabilityZone": "ap-south-1a"},
                    {"name": "b", "cidrBlock": "10.50.1.128/25", "availabilityZone": "ap-south-1b"},
                ],
            },
            headers=auth_headers,
            timeout=10,
        )
        assert resp.status_code == 400

    def test_get_unknown_request_id_returns_404(self, api_base_url, auth_headers):
        resp = requests.get(
            f"{api_base_url}/vpcs/00000000-0000-0000-0000-000000000000",
            headers=auth_headers,
            timeout=10,
        )
        assert resp.status_code == 404

    def test_idempotency_same_payload_returns_same_request_id(self, api_base_url, auth_headers):
        """Two identical POSTs with the same clientRequestId must return the same requestId."""
        client_request_id = f"idempotency-test-{uuid.uuid4().hex[:8]}"
        payload = {
            "clientRequestId": client_request_id,
            "name": f"idempotency-vpc-{client_request_id}",
            "cidrBlock": "10.98.0.0/16",
            "subnets": [
                {"name": "idem-a", "cidrBlock": "10.98.1.0/24", "availabilityZone": "ap-south-1a"},
            ],
        }

        resp1 = requests.post(f"{api_base_url}/vpcs", json=payload, headers=auth_headers, timeout=15)
        resp2 = requests.post(f"{api_base_url}/vpcs", json=payload, headers=auth_headers, timeout=15)

        assert resp1.status_code == 202
        assert resp2.status_code == 202
        assert resp1.json()["requestId"] == resp2.json()["requestId"]

    def test_idempotency_different_payload_returns_409(self, api_base_url, auth_headers):
        """Same clientRequestId with a different payload must return 409."""
        client_request_id = f"conflict-test-{uuid.uuid4().hex[:8]}"
        payload_a = {
            "clientRequestId": client_request_id,
            "name": "conflict-vpc-a",
            "cidrBlock": "10.97.0.0/16",
            "subnets": [
                {"name": "x", "cidrBlock": "10.97.1.0/24", "availabilityZone": "ap-south-1a"},
            ],
        }
        payload_b = {
            "clientRequestId": client_request_id,
            "name": "conflict-vpc-DIFFERENT",
            "cidrBlock": "10.97.0.0/16",
            "subnets": [
                {"name": "x", "cidrBlock": "10.97.1.0/24", "availabilityZone": "ap-south-1a"},
            ],
        }
        resp1 = requests.post(f"{api_base_url}/vpcs", json=payload_a, headers=auth_headers, timeout=15)
        resp2 = requests.post(f"{api_base_url}/vpcs", json=payload_b, headers=auth_headers, timeout=15)

        assert resp1.status_code == 202
        assert resp2.status_code == 409
