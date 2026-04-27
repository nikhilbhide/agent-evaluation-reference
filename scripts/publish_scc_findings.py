import os
import sys
import json
import uuid
import datetime
from google.cloud import securitycenter
from google.cloud.securitycenter_v1 import Finding, Source

def get_or_create_source(client, project_id: str):
    """Ensures a security source exists for Agent findings."""
    parent = f"projects/{project_id}"
    source_display_name = "AI Agent Security Scanner"
    
    # List sources and look for ours
    sources = client.list_sources(request={"parent": parent})
    for source in sources:
        if source.display_name == source_display_name:
            return source.name
            
    # Create if not found
    source = Source(
        display_name=source_display_name,
        description="Scans and monitors AI agent ABOMs and evaluation results."
    )
    created_source = client.create_source(
        request={"parent": parent, "source": source}
    )
    return created_source.name

def publish_finding(project_id: str, abom_path: str):
    """
    Publishes the Agent Bill of Materials (ABOM) and scan results to Security Command Center.
    """
    print(f"🛡️  Publishing Agent Security Posture to SCC for project: {project_id}")

    # Load ABOM
    try:
        with open(abom_path, "r") as f:
            abom = json.load(f)
    except Exception as e:
        print(f"❌ Could not load ABOM from {abom_path}: {e}")
        return

    client = securitycenter.SecurityCenterClient()

    try:
        source_name = get_or_create_source(client, project_id)
        print(f"✅ Using SCC Source: {source_name}")
    except Exception as e:
        print(f"⚠️ Could not create SCC source: {e}. Check IAM permissions for Security Center Admin.")
        return
    
    finding_id = f"agent-abom-{abom['metadata']['agent_version'][:8]}-{uuid.uuid4().hex[:6]}"
    resource_name = f"//aiplatform.googleapis.com/projects/{project_id}/locations/us-central1/reasoningEngines/enterprise-agent"

    # Define the finding
    finding = Finding(
        state=Finding.State.ACTIVE,
        resource_name=resource_name,
        category="AGENT_GOVERNANCE_ABOM",
        external_id=abom["metadata"]["agent_version"],
        event_time=datetime.datetime.utcnow(),
        severity=Finding.Severity.LOW,  # Informational
        source_properties={
            "agent_name": abom["metadata"]["agent_name"],
            "version": abom["metadata"]["agent_version"],
            "system_instructions_hash": abom["governance"]["system_instructions_hash"],
            "tool_manifest_hash": abom["governance"]["tool_manifest_hash"],
            "model_armor_enabled": abom["governance"]["model_armor_enabled"],
            "identity": abom["metadata"]["gsa_identity"]
        }
    )

    try:
        client.create_finding(
            request={"parent": source_name, "finding_id": finding_id, "finding": finding}
        )
        print(f"✅ SCC Finding created: {finding.category} for {abom['metadata']['agent_name']}")
    except Exception as e:
        print(f"❌ SCC submission failed: {e}")

if __name__ == "__main__":
    project = os.environ.get("GCP_PROJECT")
    # Default to build/abom.json if it exists
    abom_file = "build/abom.json"
    if not os.path.exists(abom_file):
        # Fallback for testing/manual runs
        abom_file = "data/sample_abom.json"
        os.makedirs("data", exist_ok=True)
        with open(abom_file, "w") as f:
            json.dump({
                "metadata": {"agent_name": "test-agent", "agent_version": "v1.0.0", "gsa_identity": "test@gcp.com"},
                "governance": {"system_instructions_hash": "abc", "tool_manifest_hash": "def", "model_armor_enabled": True}
            }, f)

    if not project:
        print("❌ GCP_PROJECT not set")
        sys.exit(1)
    publish_finding(project, abom_file)
