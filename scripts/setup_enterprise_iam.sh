#!/usr/bin/env bash
# =============================================================================
# setup_enterprise_iam.sh — Per-agent identity AND tool-boundary access control.
#
# WHAT THIS DOES:
#   1. Creates one GSA per agent role.
#   2. Grants each GSA only `roles/aiplatform.user` (call Gemini, run on Engine).
#   3. Grants the orchestrator `iam.serviceAccountUser` on each specialist
#      so it can delegate.
#   4. Grants each specialist `roles/run.invoker` on the MCP Cloud Run service
#      — this gives them service-level access. Per-tool scoping is then
#      enforced inside the MCP server (mcp_server/app/auth.py:TOOL_ACL).
#
# Per-tool authorization:
#   Cloud Run only supports service-level invoker IAM. To get per-tool
#   scoping (e.g. billing GSA cannot call search_knowledge_base) we use
#   the canonical pattern: Cloud Run validates the bearer token, forwards
#   the principal as X-Goog-Authenticated-User-Email, and the MCP server
#   checks the principal against an allowlist before dispatching the tool.
#
# USAGE:
#   ./scripts/setup_enterprise_iam.sh <PROJECT_ID> [MCP_REGION] [MCP_SERVICE]
# =============================================================================

set -euo pipefail

PROJECT_ID="${1:?Usage: $0 <PROJECT_ID> [MCP_REGION] [MCP_SERVICE]}"
MCP_REGION="${2:-us-central1}"
MCP_SERVICE="${3:-mcp-server}"

echo "====================================================="
echo " 🛡️  Configuring Enterprise Agent Identity"
echo " Project     : ${PROJECT_ID}"
echo " MCP service : ${MCP_SERVICE} (${MCP_REGION})"
echo "====================================================="

# ── 1. Create one GSA per agent role ──────────────────────────────────────────
AGENT_ROLES=("orchestrator" "billing" "technical" "account")

for ROLE in "${AGENT_ROLES[@]}"; do
  SA_NAME="agent-${ROLE}"
  SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

  echo "👤 Creating identity for ${ROLE}..."
  gcloud iam service-accounts create "${SA_NAME}" \
    --display-name="Agent Identity: ${ROLE}" \
    --project="${PROJECT_ID}" 2>/dev/null || echo "   already exists."

  # roles/aiplatform.user — call Gemini, run inside Agent Engine.
  # Intentionally NOT roles/aiplatform.admin: runtime SAs should not be able
  # to create/delete Reasoning Engines.
  gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="roles/aiplatform.user" \
    --condition=None \
    --quiet >/dev/null
done

echo "⏳ Waiting for IAM propagation (5s)..."
sleep 5

# ── 2. Orchestrator → specialist delegation ───────────────────────────────────
echo "🔗 Granting orchestrator delegation rights on specialists..."
for ROLE in "billing" "technical" "account"; do
    SA_EMAIL="agent-${ROLE}@${PROJECT_ID}.iam.gserviceaccount.com"
    gcloud iam service-accounts add-iam-policy-binding "${SA_EMAIL}" \
        --member="serviceAccount:agent-orchestrator@${PROJECT_ID}.iam.gserviceaccount.com" \
        --role="roles/iam.serviceAccountUser" \
        --project="${PROJECT_ID}" --quiet >/dev/null
done
echo "✅ Orchestrator can impersonate specialists."

# ── 3. Tool-boundary IAM: per-agent run.invoker on the MCP service ────────────
# Service-level grant. The MCP server then enforces per-tool ACL using the
# X-Goog-Authenticated-User-Email header that Cloud Run injects after token
# validation.
echo ""
echo "🔐 Granting per-agent run.invoker on Cloud Run service ${MCP_SERVICE}..."

if ! gcloud run services describe "${MCP_SERVICE}" \
    --region="${MCP_REGION}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
  echo "⚠️  Cloud Run service ${MCP_SERVICE} not found in ${MCP_REGION}."
  echo "    Deploy it first via scripts/deploy_mcp_cloud_run.sh, then re-run this script."
  exit 1
fi

# Specialist agents (NOT orchestrator) get invoker. Orchestrator does not call
# tools directly — it delegates to specialists.
for ROLE in "billing" "technical" "account"; do
    SA_EMAIL="agent-${ROLE}@${PROJECT_ID}.iam.gserviceaccount.com"
    gcloud run services add-iam-policy-binding "${MCP_SERVICE}" \
        --region="${MCP_REGION}" \
        --project="${PROJECT_ID}" \
        --member="serviceAccount:${SA_EMAIL}" \
        --role="roles/run.invoker" \
        --quiet >/dev/null
    echo "   ✅ ${SA_EMAIL} → run.invoker on ${MCP_SERVICE}"
done

# Lock down the service: drop allUsers if present.
echo "🚫 Removing public access (allUsers) from ${MCP_SERVICE} if present..."
gcloud run services remove-iam-policy-binding "${MCP_SERVICE}" \
    --region="${MCP_REGION}" \
    --project="${PROJECT_ID}" \
    --member="allUsers" \
    --role="roles/run.invoker" \
    --quiet 2>/dev/null || echo "   (allUsers was not bound — good.)"

# ── 4. Model Armor template — real provisioning via REST API ──────────────────
echo ""
echo "====================================================="
echo " 🛡️  Model Armor template"
echo "====================================================="
gcloud services enable modelarmor.googleapis.com --project="${PROJECT_ID}" --quiet
GCP_PROJECT="${PROJECT_ID}" GCP_LOCATION="${MCP_REGION}" \
    python3 "$(dirname "$0")/setup_model_armor.py"

echo ""
echo "Verify the per-agent ACL inside the MCP server:"
echo "  TOKEN=\$(gcloud auth print-identity-token --audiences=\$(cat mcp_server_url.txt))"
echo "  curl -H \"Authorization: Bearer \$TOKEN\" \$(cat mcp_server_url.txt)/ready"

echo ""
echo "====================================================="
echo " ✅ Enterprise IAM & Tool-Boundary Setup Complete"
echo "====================================================="
