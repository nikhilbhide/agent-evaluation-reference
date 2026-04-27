import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import vertexai
from vertexai import agent_engines
from agents.orchestrator.app.agent import orchestrator_agent

# ── Configuration ─────────────────────────────────────────────────────────────
PROJECT_ID = os.environ.get("GCP_PROJECT")
LOCATION = os.environ.get("GCP_LOCATION", "us-central1")
STAGING_BUCKET = os.environ.get("GCP_STAGING_BUCKET")
ADK_VERSION = "1.31.1"
AIPLATFORM_VERSION = "1.148.1"
GENAI_VERSION = "1.73.1"

if not PROJECT_ID:
    print("❌ GCP_PROJECT environment variable must be set.")
    sys.exit(1)

if not STAGING_BUCKET:
    STAGING_BUCKET = f"gs://agent-eval-staging-{PROJECT_ID}"


def deploy():
    if sys.version_info < (3, 10):
        print("❌ Agent Engine ADK deployments must be created from Python 3.10+.")
        sys.exit(1)

    print(f"🚀 Initializing Vertex AI (project={PROJECT_ID}, location={LOCATION})")
    vertexai.init(project=PROJECT_ID, location=LOCATION, staging_bucket=STAGING_BUCKET)

    print("📦 Wrapping Orchestrator in AdkApp (GA agent_engines API)...")
    # The GA `agent_engines` path stamps the runtime metadata that the Vertex AI
    # Console Playground reads to detect ADK apps. The older preview
    # `reasoning_engines.ReasoningEngine.create` path does NOT — agents deployed
    # that way render an empty Playground tab even with ADK labels applied.
    app = agent_engines.AdkApp(
        agent=orchestrator_agent,
        enable_tracing=True,
    )

    print("☁️  Deploying to Vertex AI Agent Engine...")
    remote_app = agent_engines.create(
        agent_engine=app,
        display_name="customer-resolution-orchestrator",
        description="Managed ADK multi-agent orchestrator for TechCorp support.",
        requirements=[
            f"google-adk=={ADK_VERSION}",
            f"google-cloud-aiplatform[agent_engines,adk]=={AIPLATFORM_VERSION}",
            f"google-genai=={GENAI_VERSION}",
            "requests",
        ],
        extra_packages=["agents", "src"],
        env_vars={
            "GOOGLE_GENAI_USE_VERTEXAI": "true",
        },
    )

    print(f"✅ Deployment Successful!")
    print(f"🔗 Resource Name: {remote_app.resource_name}")

    with open("deployed_agent_resource.txt", "w") as f:
        f.write(remote_app.resource_name)

    # ── ADK Labels ────────────────────────────────────────────────────────────
    # Belt-and-suspenders: the GA path already registers the runtime as ADK,
    # but explicit labels keep parity with register_agents.py and surface the
    # agent in label-based dashboards.
    try:
        from google.cloud import aiplatform_v1beta1
        from google.protobuf import field_mask_pb2

        client = aiplatform_v1beta1.ReasoningEngineServiceClient(
            client_options={"api_endpoint": f"{LOCATION}-aiplatform.googleapis.com"}
        )

        labels = {
            "goog-vertex-reasoning-engine-adk": "true",
            "goog-adk-version": ADK_VERSION.replace(".", "-"),
            "goog-vertex-reasoning-engine-template": "adk",
            "enterprise-agent-type": "orchestrator",
        }

        res = client.get_reasoning_engine(name=remote_app.resource_name)
        res.labels.update(labels)
        mask = field_mask_pb2.FieldMask(paths=["labels"])

        print("⏳ Applying ADK labels for playground visibility...")
        operation = client.update_reasoning_engine(reasoning_engine=res, update_mask=mask)
        operation.result(timeout=90)
        print("✅ ADK labels applied. Playground should be active in the Console.")
    except Exception as label_err:
        print(f"⚠️  Warning: Could not apply ADK labels: {label_err}")


if __name__ == "__main__":
    deploy()
