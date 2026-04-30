"""Authenticated MCP client used by every specialist agent.

The client mints a Google ID token whose audience is the MCP Cloud Run URL
and sends it as a Bearer token. Cloud Run's frontend cryptographically
validates the token and forwards the principal's email to the MCP server
as `X-Goog-Authenticated-User-Email`, where a per-tool ACL gates access.

Failure modes are surfaced (not swallowed): unauthenticated, forbidden, or
server errors raise so the orchestrator and Cloud Trace see real signals
instead of a silently-faked tool result.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import google.auth
import google.auth.transport.requests
import google.oauth2.id_token
import requests

logger = logging.getLogger(__name__)

_TOKEN_TTL_BUFFER_SECONDS = 60
_DEFAULT_TIMEOUT = 10

# Agent-side cache: avoid re-minting an ID token on every tool call.
_token_cache: dict[str, tuple[str, float]] = {}


class MCPAuthError(RuntimeError):
    """Raised when MCP rejects the call (401/403) or token mint fails."""


class MCPCallError(RuntimeError):
    """Raised when MCP returns a non-2xx for reasons other than auth."""


def _mcp_audience() -> str:
    url = os.environ.get("MCP_SERVER_URL")
    if not url:
        raise MCPCallError("MCP_SERVER_URL env var is not set on this agent.")
    return url.rstrip("/")


def _fetch_id_token(audience: str) -> str:
    cached = _token_cache.get(audience)
    if cached and cached[1] - _TOKEN_TTL_BUFFER_SECONDS > time.time():
        return cached[0]

    auth_req = google.auth.transport.requests.Request()
    try:
        # Workload identity / metadata server path. Works inside Agent Engine,
        # GKE Workload Identity, Cloud Run, and ADC for local dev.
        token = google.oauth2.id_token.fetch_id_token(auth_req, audience)
    except Exception as exc:
        raise MCPAuthError(f"Failed to mint ID token for {audience}: {exc}") from exc

    # ID tokens default to ~1h; we don't have the exact expiry without
    # decoding, so cache for 50 minutes and let the buffer pad it.
    _token_cache[audience] = (token, time.time() + 50 * 60)
    return token


def call_tool(name: str, arguments: dict[str, Any], *, timeout: float = _DEFAULT_TIMEOUT) -> Any:
    """Invoke a named MCP tool with an authenticated request.

    Returns the tool's `result` payload on success.
    Raises MCPAuthError on 401/403, MCPCallError on other non-2xx responses.
    """
    audience = _mcp_audience()
    token = _fetch_id_token(audience)
    url = f"{audience}/mcp/tools/call"

    try:
        resp = requests.post(
            url,
            json={"name": name, "arguments": arguments},
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise MCPCallError(f"Network error calling MCP {name}: {exc}") from exc

    if resp.status_code in (401, 403):
        raise MCPAuthError(
            f"MCP rejected {name} with HTTP {resp.status_code}: {resp.text[:200]}"
        )
    if resp.status_code >= 400:
        raise MCPCallError(
            f"MCP {name} returned HTTP {resp.status_code}: {resp.text[:200]}"
        )

    body = resp.json()
    if body.get("error"):
        raise MCPCallError(f"MCP tool {name} reported error: {body['error']}")

    return body.get("result")
