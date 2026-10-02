"""
Unit tests for src/common/responses.py
"""
from __future__ import annotations

import json

import pytest

from src.common.responses import (
    accepted,
    bad_request,
    conflict,
    internal_error,
    not_found,
    ok,
    unauthorized,
)


class TestResponses:

    def test_accepted_has_202(self):
        r = accepted({"requestId": "abc", "status": "PENDING", "statusUrl": "/vpcs/abc"})
        assert r["statusCode"] == 202
        body = json.loads(r["body"])
        assert body["requestId"] == "abc"

    def test_ok_has_200(self):
        r = ok({"requestId": "abc", "status": "SUCCEEDED"})
        assert r["statusCode"] == 200

    def test_bad_request_has_400(self):
        r = bad_request("Invalid CIDR", field="cidrBlock")
        assert r["statusCode"] == 400
        body = json.loads(r["body"])
        assert body["error"] == "BAD_REQUEST"
        assert body["field"] == "cidrBlock"
        assert "Invalid CIDR" in body["message"]

    def test_bad_request_without_field(self):
        r = bad_request("Something is wrong")
        assert r["statusCode"] == 400
        body = json.loads(r["body"])
        assert "field" not in body

    def test_unauthorized_has_401(self):
        r = unauthorized()
        assert r["statusCode"] == 401
        body = json.loads(r["body"])
        assert body["error"] == "UNAUTHORIZED"

    def test_not_found_has_404(self):
        r = not_found("Request 'abc' not found")
        assert r["statusCode"] == 404
        body = json.loads(r["body"])
        assert body["error"] == "NOT_FOUND"

    def test_conflict_has_409_with_existing_id(self):
        r = conflict("Duplicate payload", existing_request_id="req-old")
        assert r["statusCode"] == 409
        body = json.loads(r["body"])
        assert body["error"] == "CONFLICT"
        assert body["existingRequestId"] == "req-old"

    def test_internal_error_has_500(self):
        r = internal_error()
        assert r["statusCode"] == 500
        body = json.loads(r["body"])
        assert body["error"] == "INTERNAL_ERROR"

    def test_content_type_header_present(self):
        for fn in [ok, bad_request, not_found, internal_error]:
            if fn == bad_request:
                r = fn("msg")
            elif fn == not_found:
                r = fn()
            elif fn == internal_error:
                r = fn()
            else:
                r = fn({"key": "val"})
            assert r["headers"]["Content-Type"] == "application/json"

    def test_body_is_valid_json_string(self):
        r = accepted({"requestId": "x", "status": "PENDING", "statusUrl": "/vpcs/x"})
        # Must be a string, not a dict
        assert isinstance(r["body"], str)
        json.loads(r["body"])  # must not raise
