"""
Tear down every Reasoning Engine in the project that matches one of our
agent display names, then redeploy all four agents (orchestrator + 3
specialists) using the GA agent_engines SDK so they show up in the
Vertex AI Console Playground.

Run from Python 3.10+ (use venv-adk):
    source venv-adk/bin/activate
    export GCP_PROJECT=<your-project-id>
    export GCP_LOCATION=us-central1
    python scripts/redeploy_all.py
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import vertexai
from google.cloud import aiplatform_v1beta1
from google.cloud.aiplatform_v1beta1.types import DeleteReasoningEngineRequest

from agents._shared.config import (
    ACCOUNT_DISPLAY_NAME,
    BILLING_DISPLAY_NAME,
    ORCHESTRATOR_DISPLAY_NAME,
    TECHNICAL_DISPLAY_NAME,
    require,
    staging_bucket,
)
from scripts.deploy_agent_engine import deploy as deploy_orchestrator
from scripts.register_agents import deploy_all_specialists

PROJECT_ID = require("GCP_PROJECT")
LOCATION = os.environ.get("GCP_LOCATION", "us-central1")
STAGING_BUCKET = staging_bucket(PROJECT_ID)

# Any Reasoning Engine whose display_name matches one of these is torn down.
# `*-custom` is a legacy display name still in some projects from earlier
# deploy paths — keep matching it so teardown stays idempotent.
TARGET_DISPLAY_NAMES = {
    ORCHESTRATOR_DISPLAY_NAME,
    f"{ORCHESTRATOR_DISPLAY_NAME}-custom",
    BILLING_DISPLAY_NAME,
    ACCOUNT_DISPLAY_NAME,
    TECHNICAL_DISPLAY_NAME,
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
                # force=True cascades through child sessions (Memory Bank-wired
                # engines accumulate sessions per user_id; without force, delete
                # fails with "contains child resources: sessions").
                op = client.delete_reasoning_engine(
                    request=DeleteReasoningEngineRequest(name=re.name, force=True)
                )
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
