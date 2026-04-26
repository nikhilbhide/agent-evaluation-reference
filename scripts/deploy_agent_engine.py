import os
import sys
import vertexai
from vertexai.preview import reasoning_engines
from agents.orchestrator.app.agent import orchestrator_agent

# ── Configuration ─────────────────────────────────────────────────────────────
PROJECT_ID = os.environ.get("GCP_PROJECT")
LOCATION = os.environ.get("GCP_LOCATION", "us-central1")
STAGING_BUCKET = os.environ.get("GCP_STAGING_BUCKET")

if not PROJECT_ID:
    print("❌ GCP_PROJECT environment variable must be set.")
    sys.exit(1)

if not STAGING_BUCKET:
    STAGING_BUCKET = f"gs://agent-eval-staging-{PROJECT_ID}"
    print(f"ℹ️  GCP_STAGING_BUCKET not set. Defaulting to {STAGING_BUCKET}")
elif not STAGING_BUCKET.startswith("gs://"):
    STAGING_BUCKET = f"gs://{STAGING_BUCKET}"

def deploy():
    print(f"🚀 Initializing Vertex AI (project={PROJECT_ID}, location={LOCATION})")
    vertexai.init(project=PROJECT_ID, location=LOCATION, staging_bucket=STAGING_BUCKET)

    print("📦 Wrapping Orchestrator in AdkApp...")
    # Wrap the ADK agent for deployment to Reasoning Engine
    # This automatically handles session state and tool execution in the cloud
    app = reasoning_engines.AdkApp(
        agent=orchestrator_agent,
        enable_tracing=True
    )

    print("☁️  Deploying to Vertex AI Agent Engine (Reasoning Engine)...")
    # This command packages the code, uploads to GCS, and creates the managed endpoint
    remote_app = reasoning_engines.ReasoningEngine.create(
        reasoning_engine=app,
        display_name="customer-resolution-orchestrator",
        description="Managed ADK multi-agent orchestrator for TechCorp support.",
        requirements=[
            "google-adk",
            "google-cloud-aiplatform[reasoning_engines]",
            "requests",
        ],
        extra_packages=["agents"], # Include the 'agents' module so sub-agents are packaged
    )

    print(f"✅ Deployment Successful!")
    print(f"🔗 Resource Name: {remote_app.resource_name}")
    print(f"📍 Location: {LOCATION}")
    
    # Save the resource name for the CD pipeline to use
    with open("deployed_agent_resource.txt", "w") as f:
        f.write(remote_app.resource_name)

if __name__ == "__main__":
    deploy()
