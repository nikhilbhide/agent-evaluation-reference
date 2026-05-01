import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import vertexai
from vertexai import agent_engines

from agents._shared.config import (
    ACCOUNT_DISPLAY_NAME,
    BILLING_DISPLAY_NAME,
    TECHNICAL_DISPLAY_NAME,
    require,
    staging_bucket,
)
from agents._shared.versions import (
    ADK_VERSION,
    EXTRA_PACKAGES,
    REQUIREMENTS,
)
from agents.account_agent.app.agent import account_agent
from agents.billing_agent.app.agent import billing_agent
from agents.technical_agent.app.agent import technical_agent

# ── Configuration ─────────────────────────────────────────────────────────────
PROJECT_ID = require("GCP_PROJECT")
LOCATION = os.environ.get("GCP_LOCATION", "us-central1")
STAGING_BUCKET = staging_bucket(PROJECT_ID)

ROOT = Path(__file__).resolve().parents[1]
ORCHESTRATOR_RESOURCE_FILE = ROOT / "deployed_agent_resource.txt"
MCP_URL_FILE = ROOT / "mcp_server_url.txt"


def _read_mcp_url() -> str | None:
    env = os.environ.get("MCP_SERVER_URL")
    if env:
        return env.strip()
    if MCP_URL_FILE.exists():
        return MCP_URL_FILE.read_text().strip() or None
    return None


_MCP_URL = _read_mcp_url()
MA_FILE = ROOT / "model_armor_template.txt"


def _read_model_armor() -> str | None:
    env = os.environ.get("MODEL_ARMOR_TEMPLATE")
    if env:
        return env.strip()
    if MA_FILE.exists():
        return MA_FILE.read_text().strip() or None
    return None


_MODEL_ARMOR = _read_model_armor()

ENV_VARS = {"GOOGLE_GENAI_USE_VERTEXAI": "true"}
if _MCP_URL:
    ENV_VARS["MCP_SERVER_URL"] = _MCP_URL
else:
    print("⚠️  MCP_SERVER_URL not set and mcp_server_url.txt missing — "
          "specialists will surface TOOL_ERROR on every tool call.")
if _MODEL_ARMOR:
    ENV_VARS["MODEL_ARMOR_TEMPLATE"] = _MODEL_ARMOR
else:
    print("⚠️  MODEL_ARMOR_TEMPLATE not set — specialists will deploy without armor.")

# Each entry deploys one specialist as its own Reasoning Engine so it shows up
# independently in the Vertex AI Agent Engine Playground.
SPECIALIST_REGISTRY = [
    {
        "display_name": BILLING_DISPLAY_NAME,
        "agent": billing_agent,
        "resource_file": "deployed_billing_agent_resource.txt",
        "service_account": f"agent-billing@{PROJECT_ID}.iam.gserviceaccount.com",
        "gcs_dir_name": "agent_engine_billing",
    },
    {
        "display_name": ACCOUNT_DISPLAY_NAME,
        "agent": account_agent,
        "resource_file": "deployed_account_agent_resource.txt",
        "service_account": f"agent-account@{PROJECT_ID}.iam.gserviceaccount.com",
        "gcs_dir_name": "agent_engine_account",
    },
    {
        "display_name": TECHNICAL_DISPLAY_NAME,
        "agent": technical_agent,
        "resource_file": "deployed_technical_agent_resource.txt",
        "service_account": f"agent-technical@{PROJECT_ID}.iam.gserviceaccount.com",
        "gcs_dir_name": "agent_engine_technical",
    },
]


def _make_memory_builder(agent_engine_id: str):
    """All specialists share the orchestrator's Memory Bank — pass orchestrator's engine_id here."""
    def builder():
        from google.adk.memory import VertexAiMemoryBankService
        return VertexAiMemoryBankService(
            project=PROJECT_ID,
            location=LOCATION,
            agent_engine_id=agent_engine_id,
        )
    return builder


def _make_session_builder(agent_engine_id: str):
    """Each specialist's session service points at its own engine_id."""
    def builder():
        from google.adk.sessions import VertexAiSessionService
        return VertexAiSessionService(
            project=PROJECT_ID,
            location=LOCATION,
            agent_engine_id=agent_engine_id,
        )
    return builder


def _read_orchestrator_engine_id() -> str:
    if not ORCHESTRATOR_RESOURCE_FILE.exists():
        raise FileNotFoundError(
            f"{ORCHESTRATOR_RESOURCE_FILE} not found — deploy the orchestrator first."
        )
    resource_name = ORCHESTRATOR_RESOURCE_FILE.read_text().strip()
    if not resource_name:
        raise ValueError(f"{ORCHESTRATOR_RESOURCE_FILE} is empty.")
    return resource_name.split("/")[-1]


def _apply_adk_labels(resource_name: str) -> None:
    """Stamp the playground-visibility labels on a deployed Reasoning Engine."""
    from google.cloud import aiplatform_v1beta1
    from google.protobuf import field_mask_pb2

    client = aiplatform_v1beta1.ReasoningEngineServiceClient(
        client_options={"api_endpoint": f"{LOCATION}-aiplatform.googleapis.com"}
    )

    # Label values must NOT contain dots — replace with hyphens.
    labels = {
        "goog-vertex-reasoning-engine-adk": "true",
        "goog-adk-version": ADK_VERSION.replace(".", "-"),
        "goog-vertex-reasoning-engine-template": "adk",
        "enterprise-agent-type": "specialist",
    }

    res = client.get_reasoning_engine(name=resource_name)
    res.labels.update(labels)
    mask = field_mask_pb2.FieldMask(paths=["labels"])

    print(f"⏳ Applying ADK labels to {resource_name}...")
    operation = client.update_reasoning_engine(reasoning_engine=res, update_mask=mask)
    operation.result(timeout=90)
    print("✅ ADK labels applied.")


def _deploy_specialist(entry: dict, orchestrator_engine_id: str) -> str | None:
    display_name = entry["display_name"]
    agent = entry["agent"]
    service_account = entry["service_account"]
    gcs_dir_name = entry["gcs_dir_name"]
    print(f"\n🚀 Deploying {display_name} to Agent Engine (pass 1: create) as {service_account}...")

    # ── Pass 1: create without session wiring ──
    # Memory points at the orchestrator's engine_id (already known).
    # Session is self-referential, so we wire it after we know our own ID.
    app = agent_engines.AdkApp(
        agent=agent,
        enable_tracing=True,
        memory_service_builder=_make_memory_builder(orchestrator_engine_id),
    )

    try:
        remote_app = agent_engines.create(
            agent_engine=app,
            display_name=display_name,
            requirements=REQUIREMENTS,
            extra_packages=EXTRA_PACKAGES,
            env_vars=ENV_VARS,
            service_account=service_account,
            gcs_dir_name=gcs_dir_name,
        )
    except Exception as exc:
        print(f"❌ {display_name} deployment failed: {exc}")
        return None

    resource_name = remote_app.resource_name
    engine_id = resource_name.split("/")[-1]
    print(f"✅ {display_name} created: {resource_name}")

    # Persist immediately so a partial run still leaves a record on disk.
    with open(entry["resource_file"], "w") as f:
        f.write(resource_name)

    # ── Pass 2: re-bind with session_service_builder pointing at self ──
    print(f"🔁 {display_name} pass 2: binding session_service_builder to self engine_id...")
    wired_app = agent_engines.AdkApp(
        agent=agent,
        enable_tracing=True,
        memory_service_builder=_make_memory_builder(orchestrator_engine_id),
        session_service_builder=_make_session_builder(engine_id),
    )

    try:
        agent_engines.update(
            resource_name,
            agent_engine=wired_app,
            requirements=REQUIREMENTS,
            extra_packages=EXTRA_PACKAGES,
            env_vars=ENV_VARS,
            service_account=service_account,
            gcs_dir_name=gcs_dir_name,
        )
        print(f"✅ {display_name} updated with self-bound session service.")
    except Exception as exc:
        print(f"⚠️  {display_name} update failed: {exc}")

    try:
        _apply_adk_labels(resource_name)
    except Exception as label_err:
        print(f"⚠️ Warning: Could not apply ADK labels for {display_name}: {label_err}")

    return resource_name


def deploy_all_specialists() -> None:
    if sys.version_info < (3, 10):
        print("❌ Agent Engine ADK deployments must be created from Python 3.10+.")
        print("   The deployed runtime records the local Python version; Python 3.9 resources are not Playground-compatible.")
        sys.exit(1)

    vertexai.init(project=PROJECT_ID, location=LOCATION, staging_bucket=STAGING_BUCKET)

    orchestrator_engine_id = _read_orchestrator_engine_id()
    print(f"📌 Specialists will share orchestrator Memory Bank: agent_engine_id={orchestrator_engine_id}")

    manifest: dict[str, str] = {}
    failed: list[str] = []
    for entry in SPECIALIST_REGISTRY:
        resource = _deploy_specialist(entry, orchestrator_engine_id)
        if resource:
            manifest[entry["display_name"]] = resource
        else:
            failed.append(entry["display_name"])

    with open("deployed_specialist_agents.json", "w") as f:
        json.dump(manifest, f, indent=2)

    print("\n📒 Manifest written to deployed_specialist_agents.json")
    if failed:
        # Loud failure: a partial specialist deploy means some
        # deployed_*_resource.txt files are missing, which means downstream
        # registration / online-monitor / smoke steps will silently skip
        # the unhealthy specialist. Surface this immediately so the
        # operator decides whether to retry or rollback.
        print(
            f"\n❌ {len(failed)} specialist deploy(s) FAILED: {', '.join(failed)}",
            file=sys.stderr,
        )
        print(
            "   Re-run after fixing the underlying error. Existing successful "
            "specialists are recorded and will be reused.",
            file=sys.stderr,
        )
        sys.exit(1)
    print("👉 All deployed specialists should now appear in the Vertex AI Console Agent Engine list with a working Playground tab.")


if __name__ == "__main__":
    deploy_all_specialists()
