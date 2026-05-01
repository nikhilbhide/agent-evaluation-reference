"""Per-principal tool ACL for the MCP server.

Cloud Run is deployed with `--no-allow-unauthenticated`, so the frontend
cryptographically validates the caller's ID token before traffic ever
reaches us. For service-to-service calls, Cloud Run forwards the token
as `Authorization: Bearer <id_token>` — it does NOT inject
`X-Goog-Authenticated-User-Email` (that header is only set for end-user
auth via IAP). So we extract the principal ourselves by decoding the
JWT's `email` claim, then enforce a tool-level ACL.

We don't re-verify the signature: Cloud Run has already done that. We
just decode the payload to read the email/sub claims.
"""

from __future__ import annotations

import base64
import json
import logging
import os
from typing import Iterable

from fastapi import Header, HTTPException

logger = logging.getLogger(__name__)

# Project ID is injected via env var; falls back to "unknown" so the ACL
# fails closed (no principal can match) if the var is missing in prod.
_PROJECT_ID = os.environ.get("GCP_PROJECT", "unknown")


def _gsa(role: str) -> str:
    return f"agent-{role}@{_PROJECT_ID}.iam.gserviceaccount.com"


# Per-principal tool allowlist. Anything not in the list is forbidden.
#
# Per-specialist scoping (billing/account/technical) is the strict pattern
# you'd use if each specialist ran as a separately-deployed Reasoning Engine
# and called MCP with its own SA. We currently use in-process AgentTool
# delegation — the orchestrator's runtime executes specialists locally, so
# every MCP egress is signed by `agent-orchestrator`. The orchestrator entry
# below grants the *union* of specialist tools so the in-process pattern
# works while the per-specialist entries above remain enforced for any
# direct engine-to-engine invocation.
_BILLING_TOOLS = {"lookup_invoice", "issue_refund"}
_ACCOUNT_TOOLS = {"lookup_account", "lookup_transaction"}
_TECHNICAL_TOOLS = {"search_knowledge_base"}

TOOL_ACL: dict[str, set[str]] = {
    _gsa("billing"): _BILLING_TOOLS,
    _gsa("account"): _ACCOUNT_TOOLS,
    _gsa("technical"): _TECHNICAL_TOOLS,
    _gsa("orchestrator"): _BILLING_TOOLS | _ACCOUNT_TOOLS | _TECHNICAL_TOOLS,
}


def _decode_jwt_payload(token: str) -> dict:
    """Decode a JWT payload without signature verification.

    Cloud Run validated the signature before we got the request, so we
    only need to read the claims. Returns {} on any decode error.
    """
    try:
        _, payload_b64, _ = token.split(".", 2)
        # Pad to a multiple of 4 for base64.
        padding = "=" * (-len(payload_b64) % 4)
        payload = base64.urlsafe_b64decode(payload_b64 + padding)
        return json.loads(payload)
    except Exception:
        return {}


def _principal_from_authorization(auth_header: str | None) -> str | None:
    if not auth_header or not auth_header.lower().startswith("bearer "):
        return None
    token = auth_header.split(" ", 1)[1].strip()
    claims = _decode_jwt_payload(token)
    # Service-account tokens carry both `email` and `sub` (numeric user id).
    return claims.get("email") or None


def authorize_tool(
    tool_name: str,
    authorization: str | None = Header(default=None),
) -> str:
    """Verify the caller's principal can invoke `tool_name`.

    Returns the normalized principal email on success.
    Raises HTTPException(401|403) on failure.
    """
    principal = _principal_from_authorization(authorization)
    if not principal:
        logger.warning("MCP call missing/unparseable Authorization bearer token")
        raise HTTPException(status_code=401, detail="Unauthenticated caller")

    allowed = TOOL_ACL.get(principal)
    if allowed is None:
        logger.warning("Unknown principal %s attempted tool %s", principal, tool_name)
        raise HTTPException(
            status_code=403,
            detail=f"Principal {principal} is not registered with the MCP ACL",
        )

    if tool_name not in allowed:
        logger.warning(
            "Principal %s forbidden from tool %s (allowed: %s)",
            principal, tool_name, sorted(allowed),
        )
        raise HTTPException(
            status_code=403,
            detail=f"Principal {principal} cannot invoke tool '{tool_name}'",
        )

    return principal


def expected_principals() -> Iterable[str]:
    """Used by /ready to surface which GSAs the MCP expects callers from."""
    return TOOL_ACL.keys()
