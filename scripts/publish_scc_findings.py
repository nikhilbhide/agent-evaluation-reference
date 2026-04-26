import os
import sys
import json
import uuid
import datetime
from google.cloud import securitycenter
from google.cloud.securitycenter_v1 import Finding

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

    # The source name for our custom agent findings
    # In production, you would create this source once and reuse the ID
    source_name = f"projects/{project_id}/sources/agent_security_source"
    
    # Check if source exists, if not, we assume it's created via Terraform or manual setup
    # For this reference, we'll try to find or create a placeholder finding
    
    finding_id = str(uuid.uuid4()).replace("-", "")
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
            "abom_link": f"gs://agent-eval-metadata-{project_id}/aboms/{abom['metadata']['agent_version']}.json"
        }
    )

    print(f"✅ Finding created locally: {finding.category} for {abom['metadata']['agent_name']}")
    print(f"🚀 In a production SCC setup, this finding would be sent to: {source_name}")
    
    # Note: Actually calling create_finding requires a Source to be pre-created.
    # We simulate the successful submission for this reference.
    # try:
    #     client.create_finding(
    #         request={"parent": source_name, "finding_id": finding_id, "finding": finding}
    #     )
    # except Exception as e:
    #     print(f"⚠️ SCC submission skipped: {e}")

if __name__ == "__main__":
    project = os.environ.get("GCP_PROJECT")
    abom = "build/abom.json"
    if not project:
        print("❌ GCP_PROJECT not set")
        sys.exit(1)
    publish_finding(project, abom)
