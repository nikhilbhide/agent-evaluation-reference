#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="${1:?Usage: $0 <PROJECT_ID>}"
REGION="us-central1"
SERVICE_NAME="mcp-server"

echo "🚀 Deploying MCP Server to Cloud Run in ${PROJECT_ID}..."

# 1. Enable Cloud Run and Artifact Registry
gcloud services enable run.googleapis.com artifactregistry.googleapis.com --project="${PROJECT_ID}"

# 2. Create Artifact Registry if it doesn't exist
gcloud artifacts repositories create cloud-run-source-deploy \
    --repository-format=docker \
    --location="${REGION}" \
    --project="${PROJECT_ID}" || true

# 3. Build and Deploy directly from source
# This uses Google Cloud Build under the hood
gcloud run deploy "${SERVICE_NAME}" \
    --source=./mcp_server \
    --region="${REGION}" \
    --project="${PROJECT_ID}" \
    --allow-unauthenticated \
    --platform=managed

# 4. Get the URL
MCP_URL=$(gcloud run services describe "${SERVICE_NAME}" --platform=managed --region="${REGION}" --project="${PROJECT_ID}" --format='value(status.url)')

echo "✅ MCP Server deployed at: ${MCP_URL}"
echo "${MCP_URL}" > mcp_server_url.txt
