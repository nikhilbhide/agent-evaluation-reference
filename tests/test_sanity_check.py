"""
Tests for scripts/sanity_check.py
Tests the core run_sanity_check() logic with mocked HTTP calls.
"""
import pytest
import responses as resp_lib
import sys
import os

# Make the scripts directory importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from sanity_check import run_sanity_check

ENDPOINT = "http://fake-canary-agent.internal"
EXPECTED_VERSION = "abc1234"


def _register_healthy(version=EXPECTED_VERSION, latency_ms=100, response_text="Routing to billing_agent."):
    """Helper: register all healthy endpoints."""
    resp_lib.add(resp_lib.GET,  f"{ENDPOINT}/health", json={"status": "alive", "version": version}, status=200)
    resp_lib.add(resp_lib.GET,  f"{ENDPOINT}/ready",  json={"status": "ready", "version": version}, status=200)
    resp_lib.add(resp_lib.GET,  f"{ENDPOINT}/version", json={"version": version}, status=200)
    resp_lib.add(resp_lib.POST, f"{ENDPOINT}/predict",
                 json={"response": response_text, "version": version, "latency_ms": latency_ms},
                 status=200)


@resp_lib.activate
def test_all_healthy():
    _register_healthy()
    passed = run_sanity_check(ENDPOINT, EXPECTED_VERSION, latency_threshold_ms=2000)
    assert passed is True


@resp_lib.activate
def test_health_endpoint_down():
    """If /health returns 500, sanity check must fail immediately."""
    resp_lib.add(resp_lib.GET, f"{ENDPOINT}/health", status=500)
    passed = run_sanity_check(ENDPOINT, EXPECTED_VERSION, latency_threshold_ms=2000)
    assert passed is False


@resp_lib.activate
def test_version_mismatch_fails():
    """If /version returns a different SHA, sanity check must fail."""
    _register_healthy(version="wrong_sha_999")
    passed = run_sanity_check(ENDPOINT, EXPECTED_VERSION, latency_threshold_ms=2000)
    assert passed is False


@resp_lib.activate
def test_error_keyword_in_response_fails():
    """If response contains an error marker, sanity check must fail."""
    _register_healthy(response_text="Agent Error: 404 Publisher Model not found")
    passed = run_sanity_check(ENDPOINT, EXPECTED_VERSION, latency_threshold_ms=2000)
    assert passed is False


@resp_lib.activate
def test_no_version_check_when_not_provided():
    """If expected_version is None, version check is skipped."""
    resp_lib.add(resp_lib.GET,  f"{ENDPOINT}/health", json={"status": "alive"}, status=200)
    resp_lib.add(resp_lib.GET,  f"{ENDPOINT}/ready",  json={"status": "ready"}, status=200)
    resp_lib.add(resp_lib.POST, f"{ENDPOINT}/predict",
                 json={"response": "Routing to technical_agent.", "latency_ms": 200},
                 status=200)
    passed = run_sanity_check(ENDPOINT, expected_version=None, latency_threshold_ms=2000)
    assert passed is True
