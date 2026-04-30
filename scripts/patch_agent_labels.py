"""
Retroactively apply ADK labels to a deployed Reasoning Engine resource so it
appears in the Vertex AI Console → Agent Engine → Playground.

Usage:
    python scripts/patch_agent_labels.py [RESOURCE_NAME]

If RESOURCE_NAME is omitted, reads it from deployed_agent_resource.txt.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents._shared.config import require
from agents._shared.versions import ADK_VERSION

PROJECT_ID = require("GCP_PROJECT")
LOCATION = os.environ.get("GCP_LOCATION", "us-central1")

ADK_LABELS = {
    "goog-vertex-reasoning-engine-adk": "true",
    "goog-adk-version": ADK_VERSION.replace(".", "-"),
    "goog-vertex-reasoning-engine-template": "adk",
}


def patch(resource_name: str) -> None:
    from google.cloud import aiplatform_v1beta1
    from google.protobuf import field_mask_pb2

    print(f"🔍 Fetching resource: {resource_name}")
    client = aiplatform_v1beta1.ReasoningEngineServiceClient(
        client_options={"api_endpoint": f"{LOCATION}-aiplatform.googleapis.com"}
    )

    res = client.get_reasoning_engine(name=resource_name)
    print(f"   Current labels: {dict(res.labels)}")

    res.labels.update(ADK_LABELS)
    mask = field_mask_pb2.FieldMask(paths=["labels"])

    print("⏳ Applying ADK labels...")
    op = client.update_reasoning_engine(reasoning_engine=res, update_mask=mask)
    op.result(timeout=90)

    updated = client.get_reasoning_engine(name=resource_name)
    print(f"✅ Labels applied: {dict(updated.labels)}")
    print("   The agent should now appear in Vertex AI Console → Agent Engine → Playground.")


def main() -> None:
    if len(sys.argv) > 1:
        resource_name = sys.argv[1]
    else:
        try:
            with open("deployed_agent_resource.txt") as f:
                resource_name = f.read().strip()
        except FileNotFoundError:
            print("❌ No resource name provided and deployed_agent_resource.txt not found.")
            print("   Usage: python scripts/patch_agent_labels.py <resource_name>")
            sys.exit(1)

    if not resource_name:
        print("❌ Resource name is empty.")
        sys.exit(1)

    patch(resource_name)


if __name__ == "__main__":
    main()
