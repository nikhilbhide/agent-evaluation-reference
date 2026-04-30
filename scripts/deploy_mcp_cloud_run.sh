#!/usr/bin/env bash
# =============================================================================
# deploy_mcp_cloud_run.sh — Deploy the MCP tool server with auth enforced.
#
# The MCP server is the tool boundary for every specialist agent. It is
# deployed with --no-allow-unauthenticated so Cloud Run cryptographically
# validates the caller's ID token before traffic reaches the container.
# Per-tool authorization happens inside the server via TOOL_ACL.
#
# Run scripts/setup_enterprise_iam.sh AFTER this to grant per-agent invoker.
# =============================================================================

set -euo pipefail

PROJECT_ID="${1:?Usage: $0 <PROJECT_ID> [REGION]}"
REGION="${2:-us-central1}"
SERVICE_NAME="mcp-server"

echo "🚀 Deploying ${SERVICE_NAME} to Cloud Run in ${PROJECT_ID} (${REGION})..."

# 1. Enable required APIs
gcloud services enable run.googleapis.com artifactregistry.googleapis.com \
    --project="${PROJECT_ID}"

# 2. Create Artifact Registry repo if missing
gcloud artifacts repositories create cloud-run-source-deploy \
    --repository-format=docker \
    --location="${REGION}" \
    --project="${PROJECT_ID}" 2>/dev/null || true

# 3. Build and deploy from source.
#    --no-allow-unauthenticated: Cloud Run frontend rejects callers without
#      a valid Google ID token whose audience matches this service URL.
#    GCP_PROJECT env var: read by mcp_server/app/auth.py to construct the
#      expected agent GSA emails for the per-tool ACL.
gcloud run deploy "${SERVICE_NAME}" \
    --source=./mcp_server \
    --region="${REGION}" \
    --project="${PROJECT_ID}" \
    --no-allow-unauthenticated \
    --set-env-vars="GCP_PROJECT=${PROJECT_ID}" \
    --platform=managed

# 4. Capture URL
MCP_URL=$(gcloud run services describe "${SERVICE_NAME}" \
    --platform=managed --region="${REGION}" --project="${PROJECT_ID}" \
    --format='value(status.url)')

echo "✅ MCP Server deployed at: ${MCP_URL}"
echo "${MCP_URL}" > mcp_server_url.txt

cat <<EOF

Next:
  1. Grant per-agent invoker + per-tool ACL:
       ./scripts/setup_enterprise_iam.sh ${PROJECT_ID} ${REGION} ${SERVICE_NAME}

  2. Sanity-check auth from your laptop:
       TOKEN=\$(gcloud auth print-identity-token --audiences=${MCP_URL})
       curl -H "Authorization: Bearer \$TOKEN" ${MCP_URL}/ready

  3. Re-deploy agents so they pick up the new authenticated MCP client:
       python scripts/redeploy_all.py
EOF
