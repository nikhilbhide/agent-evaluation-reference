"""Register the MCP server and the four agents in the Agent Platform Registry.

This populates the Vertex AI "Agents" / "MCP Servers" tabs (the new
``agent-registry`` surface) and the Topology view. It's complementary to
``deploy_mcp_cloud_run.sh`` (which provisions the Cloud Run runtime) and
``register_agents.py`` (which creates the Reasoning Engines) — those still
do the actual deployment; this script tells the platform they exist.

API surface (alpha): ``gcloud alpha agent-registry services create``.
There is no stable Python SDK at the time of writing — we shell out.

Idempotent: re-runs skip services that already exist (gcloud surfaces an
``ALREADY_EXISTS`` error and we treat it as success).

Required env / inputs:
  * ``GCP_PROJECT``   — project to register into
  * ``GCP_LOCATION``  — defaults to ``us-central1``
  * ``MCP_SERVER_URL`` (or ``mcp_server_url.txt``)
  * ``deployed_agent_resource.txt`` — orchestrator Reasoning Engine
  * ``deployed_billing_agent_resource.txt`` etc — specialists

Note: the gcloud command may fail with a permission error if the
``agentregistry.googleapis.com`` API isn't enabled or your project isn't on
the alpha allowlist. Run ``gcloud services enable agentregistry.googleapis.com``
first; if that returns NOT_FOUND, request access through the Agent Platform
console.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# mcp_server uses `from app.xxx import ...` (Cloud Run-style absolute imports
# rooted at the container's WORKDIR), so its package root has to be on
# sys.path *in addition* to the repo root.
sys.path.insert(0, str(ROOT / "mcp_server"))

from agents._shared.config import (  # noqa: E402
    ACCOUNT_DISPLAY_NAME,
    BILLING_DISPLAY_NAME,
    GCP_LOCATION,
    ORCHESTRATOR_DISPLAY_NAME,
    TECHNICAL_DISPLAY_NAME,
    require,
)

# Pulling TOOL_REGISTRY from the local source gives us the same payload the
# deployed service returns on /mcp/tools/list — no need to call the
# deployed endpoint, which would require IAM bootstrapping.
from app.main import TOOL_REGISTRY  # noqa: E402


PROJECT_ID = require("GCP_PROJECT")
LOCATION = os.environ.get("GCP_LOCATION", GCP_LOCATION)


# ── Helpers ───────────────────────────────────────────────────────────────────
def _read_mcp_url() -> str:
    env = os.environ.get("MCP_SERVER_URL")
    if env:
        return env.strip()
    f = ROOT / "mcp_server_url.txt"
    if f.exists():
        return f.read_text().strip()
    raise RuntimeError(
        "MCP_SERVER_URL not set and mcp_server_url.txt missing. "
        "Run scripts/deploy_mcp_cloud_run.sh first."
    )


def _read_engine_resource(filename: str) -> str | None:
    f = ROOT / filename
    if not f.exists():
        return None
    val = f.read_text().strip()
    return val or None


def _engine_endpoint_url(resource_name: str) -> str:
    """Public REST endpoint for a Reasoning Engine resource."""
    return f"https://{LOCATION}-aiplatform.googleapis.com/v1/{resource_name}"


def _service_id(display_name: str) -> str:
    """Derive a registry service ID from a display name.

    Service IDs must be lowercase alphanumeric with hyphens; display names
    in this repo already conform, but normalize defensively.
    """
    return display_name.lower().replace("_", "-").replace(" ", "-")


def _tool_spec_payload() -> str:
    """Build the MCP tool-spec JSON in the same shape /mcp/tools/list returns."""
    tools = [
        {
            "name": name,
            "description": meta["description"],
            "parameters": meta["parameters"],
        }
        for name, meta in TOOL_REGISTRY.items()
    ]
    return json.dumps({"tools": tools})


def _gcloud_create(
    service_id: str,
    display_name: str,
    description: str,
    interfaces: list[dict[str, str]],
    *,
    spec_flags: list[str],
) -> None:
    """Run `gcloud alpha agent-registry services create`. Idempotent on ALREADY_EXISTS."""
    cmd = [
        "gcloud", "alpha", "agent-registry", "services", "create", service_id,
        "--project", PROJECT_ID,
        "--location", LOCATION,
        "--display-name", display_name,
        "--description", description,
        "--interfaces", json.dumps(interfaces),
        *spec_flags,
    ]
    print(f"   $ {' '.join(cmd[:7])} ...")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode == 0:
        print(f"   ✅ created {service_id}")
        return
    # gcloud writes the API error to stderr; treat ALREADY_EXISTS as ok.
    err = (proc.stderr or "") + (proc.stdout or "")
    if "ALREADY_EXISTS" in err or "already exists" in err.lower():
        print(f"   ↪︎ {service_id} already registered, skipping")
        return
    print(f"   ❌ failed to register {service_id}:\n{err}", file=sys.stderr)
    raise SystemExit(1)


# ── Registrations ─────────────────────────────────────────────────────────────
def register_mcp_server() -> None:
    print("\n[1/2] Registering MCP server")
    mcp_url = _read_mcp_url()
    _gcloud_create(
        service_id="mcp-tool-server",
        display_name="MCP Tool Server",
        description="Customer-support MCP server (billing, account, KB tools).",
        interfaces=[{"protocolBinding": "https", "url": mcp_url}],
        spec_flags=[
            "--mcp-server-spec-type", "tool-spec",
            "--mcp-server-spec-content", _tool_spec_payload(),
        ],
    )


# (display_name, resource_file, description) for each agent.
AGENT_TARGETS = [
    (
        ORCHESTRATOR_DISPLAY_NAME,
        "deployed_agent_resource.txt",
        "Routing orchestrator that delegates to billing/account/technical specialists.",
    ),
    (
        BILLING_DISPLAY_NAME,
        "deployed_billing_agent_resource.txt",
        "Billing specialist — invoice lookup and refund issuance.",
    ),
    (
        ACCOUNT_DISPLAY_NAME,
        "deployed_account_agent_resource.txt",
        "Account specialist — account and transaction lookup.",
    ),
    (
        TECHNICAL_DISPLAY_NAME,
        "deployed_technical_agent_resource.txt",
        "Technical specialist — knowledge-base search.",
    ),
]


def register_agents() -> None:
    print("\n[2/2] Registering agents")
    for display_name, resource_file, description in AGENT_TARGETS:
        resource = _read_engine_resource(resource_file)
        if not resource:
            print(f"   ⚠️  {resource_file} missing — skipping {display_name}")
            continue
        _gcloud_create(
            service_id=_service_id(display_name),
            display_name=display_name,
            description=description,
            interfaces=[{
                "protocolBinding": "https",
                "url": _engine_endpoint_url(resource),
            }],
            # We don't ship A2A Agent Cards yet; `no-spec` records the
            # service without a card. Add cards by updating the service
            # later when ready.
            spec_flags=["--agent-spec-type", "no-spec"],
        )


def main() -> None:
    print(f"📒 Registering Agent Platform entries in {PROJECT_ID} ({LOCATION})")
    register_mcp_server()
    register_agents()
    print("\n✅ Done. Open the Agent Registry tab in the Vertex AI Console.")
    print("   Agents and MCP servers should now appear, and the Topology view")
    print("   will populate as traces flow through them.")


if __name__ == "__main__":
    main()
