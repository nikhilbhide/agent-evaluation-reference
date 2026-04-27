"""
Tear down every Reasoning Engine in the project that matches one of our
agent display names, then redeploy all four agents (orchestrator + 3
specialists) using the GA agent_engines SDK so they show up in the
Vertex AI Console Playground.

Run from Python 3.10+ (use venv-adk):
    source venv-adk/bin/activate
    export GCP_PROJECT=agent-evaluation-494310
    export GCP_LOCATION=us-central1
    export GCP_STAGING_BUCKET=gs://agent-eval-staging-agent-evaluation-494310
    python scripts/redeploy_all.py
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import vertexai
from google.cloud import aiplatform_v1beta1

from scripts.deploy_agent_engine import deploy as deploy_orchestrator
from scripts.register_agents import deploy_all_specialists

PROJECT_ID = os.environ.get("GCP_PROJECT", "agent-evaluation-494310")
LOCATION = os.environ.get("GCP_LOCATION", "us-central1")
STAGING_BUCKET = os.environ.get("GCP_STAGING_BUCKET", f"gs://agent-eval-staging-{PROJECT_ID}")

# Any Reasoning Engine whose display_name matches one of these is torn down.
TARGET_DISPLAY_NAMES = {
    "customer-resolution-orchestrator",
    "customer-resolution-orchestrator-custom",
    "billing-specialist",
    "account-specialist",
    "technical-specialist",
}

# Stale resource-name files to clear so a partial old run doesn't mislead.
STALE_RESOURCE_FILES = [
    "deployed_agent_resource.txt",
    "deployed_billing_agent_resource.txt",
    "deployed_account_agent_resource.txt",
    "deployed_technical_agent_resource.txt",
    "deployed_specialist_agents.json",
]


def teardown_existing() -> None:
    client = aiplatform_v1beta1.ReasoningEngineServiceClient(
        client_options={"api_endpoint": f"{LOCATION}-aiplatform.googleapis.com"}
    )
    parent = f"projects/{PROJECT_ID}/locations/{LOCATION}"

    print(f"🔍 Listing Reasoning Engines under {parent}...")
    matches = [
        re for re in client.list_reasoning_engines(parent=parent)
        if re.display_name in TARGET_DISPLAY_NAMES
    ]

    if not matches:
        print("ℹ️  No matching agents found — nothing to delete.")
    else:
        print(f"🗑  Deleting {len(matches)} existing agent(s):")
        for re in matches:
            print(f"   - {re.display_name} ({re.name})")
        for re in matches:
            try:
                op = client.delete_reasoning_engine(name=re.name)
                op.result(timeout=300)
                print(f"   ✅ deleted {re.display_name}")
            except Exception as exc:
                print(f"   ❌ failed to delete {re.display_name}: {exc}")

    for stale in STALE_RESOURCE_FILES:
        path = Path(stale)
        if path.exists():
            path.unlink()
            print(f"🧹 removed stale {stale}")


def main() -> None:
    if sys.version_info < (3, 10):
        print("❌ Run from Python 3.10+ (activate venv-adk).")
        sys.exit(1)

    vertexai.init(project=PROJECT_ID, location=LOCATION, staging_bucket=STAGING_BUCKET)

    teardown_existing()

    print("\n=== Redeploying orchestrator ===")
    deploy_orchestrator()

    print("\n=== Redeploying specialists ===")
    deploy_all_specialists()

    print("\n🎉 Done. All four agents should now be Playground-visible in the Vertex AI Console.")


if __name__ == "__main__":
    main()
