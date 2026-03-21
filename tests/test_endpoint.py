"""
Tests for agent/endpoint.py — the HTTP client used in CD mode.

Uses the `responses` library to mock HTTP calls without any real network.
"""
import pytest
import responses as resp_lib
from agent_eval.agent.endpoint import run_agent_via_endpoint

ENDPOINT = "http://fake-canary-agent.internal"


@resp_lib.activate
def test_successful_response():
    """Happy path: endpoint returns a valid JSON response."""
    resp_lib.add(
        resp_lib.POST,
        f"{ENDPOINT}/predict",
        json={"response": "Routing to billing_agent. I will process your refund."},
        status=200,
    )
    result = run_agent_via_endpoint("I need a refund.", ENDPOINT)
    assert "billing_agent" in result
    assert "Agent Error" not in result


@resp_lib.activate
def test_http_500_returns_error_string():
    """Endpoint returns HTTP 500 — should return error string, not raise."""
    resp_lib.add(
        resp_lib.POST,
        f"{ENDPOINT}/predict",
        json={"detail": "Internal Server Error"},
        status=500,
    )
    result = run_agent_via_endpoint("I need a refund.", ENDPOINT)
    assert result.startswith("Agent Error:")
    assert "500" in result


@resp_lib.activate
def test_timeout_returns_error_string():
    """Endpoint times out — should return error string, not raise."""
    import responses
    from requests.exceptions import ConnectTimeout

    resp_lib.add(
        resp_lib.POST,
        f"{ENDPOINT}/predict",
        body=ConnectTimeout(),
    )
    result = run_agent_via_endpoint("I need a refund.", ENDPOINT, timeout=1)
    assert result.startswith("Agent Error:")
    assert "timeout" in result.lower()


@resp_lib.activate
def test_empty_response_field():
    """Endpoint returns 200 but response field is empty — still non-error."""
    resp_lib.add(
        resp_lib.POST,
        f"{ENDPOINT}/predict",
        json={"response": ""},
        status=200,
    )
    result = run_agent_via_endpoint("I need a refund.", ENDPOINT)
    assert result == ""
    assert "Agent Error" not in result


@resp_lib.activate
def test_non_standard_response_returned_as_string():
    """Endpoint returns unexpected JSON shape — falls back to str(data)."""
    resp_lib.add(
        resp_lib.POST,
        f"{ENDPOINT}/predict",
        json={"message": "ok", "code": 0},   # no 'response' key
        status=200,
    )
    result = run_agent_via_endpoint("I need a refund.", ENDPOINT)
    assert "message" in result or "ok" in result
