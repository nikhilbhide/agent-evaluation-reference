import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import vertexai
from vertexai import agent_engines

from agents.account_agent.app.agent import account_agent
from agents.billing_agent.app.agent import billing_agent
from agents.technical_agent.app.agent import technical_agent

# ── Configuration ─────────────────────────────────────────────────────────────
PROJECT_ID = os.environ.get("GCP_PROJECT", "agent-evaluation-494310")
LOCATION = os.environ.get("GCP_LOCATION", "us-central1")
STAGING_BUCKET = os.environ.get("GCP_STAGING_BUCKET", f"gs://agent-eval-staging-{PROJECT_ID}")
ADK_VERSION = "1.31.1"
AIPLATFORM_VERSION = "1.148.1"
GENAI_VERSION = "1.73.1"

REQUIREMENTS = [
    f"google-adk=={ADK_VERSION}",
    f"google-cloud-aiplatform[agent_engines,adk]=={AIPLATFORM_VERSION}",
    f"google-genai=={GENAI_VERSION}",
    "requests",
]

# Each entry deploys one specialist as its own Reasoning Engine so it shows up
# independently in the Vertex AI Agent Engine Playground.
SPECIALIST_REGISTRY = [
    {
        "display_name": "billing-specialist",
        "agent": billing_agent,
        "resource_file": "deployed_billing_agent_resource.txt",
    },
    {
        "display_name": "account-specialist",
        "agent": account_agent,
        "resource_file": "deployed_account_agent_resource.txt",
    },
    {
        "display_name": "technical-specialist",
        "agent": technical_agent,
        "resource_file": "deployed_technical_agent_resource.txt",
    },
]


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


def _deploy_specialist(entry: dict) -> str | None:
    display_name = entry["display_name"]
    print(f"\n🚀 Deploying {display_name} to Agent Engine...")

    app = agent_engines.AdkApp(agent=entry["agent"], enable_tracing=True)

    try:
        remote_app = agent_engines.create(
            agent_engine=app,
            display_name=display_name,
            requirements=REQUIREMENTS,
            extra_packages=["agents", "src"],
            env_vars={"GOOGLE_GENAI_USE_VERTEXAI": "true"},
        )
    except Exception as exc:
        print(f"❌ {display_name} deployment failed: {exc}")
        return None

    resource_name = remote_app.resource_name
    print(f"✅ {display_name} resource created: {resource_name}")

    # Persist immediately so a partial run still leaves a record on disk.
    with open(entry["resource_file"], "w") as f:
        f.write(resource_name)

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

    manifest: dict[str, str] = {}
    for entry in SPECIALIST_REGISTRY:
        resource = _deploy_specialist(entry)
        if resource:
            manifest[entry["display_name"]] = resource

    with open("deployed_specialist_agents.json", "w") as f:
        json.dump(manifest, f, indent=2)

    print("\n📒 Manifest written to deployed_specialist_agents.json")
    print("👉 All deployed specialists should now appear in the Vertex AI Console Agent Engine list with a working Playground tab.")


if __name__ == "__main__":
    deploy_all_specialists()
