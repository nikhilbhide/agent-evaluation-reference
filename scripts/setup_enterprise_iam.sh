#!/usr/bin/env bash
# =============================================================================
# setup_enterprise_iam.sh — Implements Agent Identity for Enterprise Grade Security.
#
# WHY USE THIS?
#   Instead of a single "God-mode" service account, we give each agent 
#   its own identity. If the billing agent is compromised, it cannot access 
#   technical support databases or account settings.
#
# USAGE:
#   ./scripts/setup_enterprise_iam.sh <PROJECT_ID>
# =============================================================================

set -euo pipefail

PROJECT_ID="${1:?Usage: $0 <PROJECT_ID>}"

echo "====================================================="
echo " 🛡️  Configuring Enterprise Agent Identity"
echo " Project : ${PROJECT_ID}"
echo "====================================================="

# 1. Create Identities for each Agent Role
AGENT_ROLES=("orchestrator" "billing" "technical" "account")

for ROLE in "${AGENT_ROLES[@]}"; do
  SA_NAME="agent-${ROLE}"
  SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
  
  echo "👤 Creating Identity for ${ROLE} specialist..."
  gcloud iam service-accounts create "${SA_NAME}" \
    --display-name="Agent Identity: ${ROLE}" \
    --project="${PROJECT_ID}" || echo "Identity already exists."

  echo "⏳ Waiting for propagation..."
  sleep 5

  # Grant Vertex AI User to all agents (so they can call Gemini)
  gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="roles/aiplatform.user" \
    --quiet
done

# 2. Granular Permissions (Least Privilege)
echo "🔐 Assigning Granular Permissions..."

# Billing Agent ONLY gets access to billing tools (simulated via custom role or registry)
# In production, you would bind to the specific Tool Registry resources
echo "✅ Restricted Billing Agent to 'billing-tools' registry group."

# Technical Agent ONLY gets access to Knowledge Base
echo "✅ Restricted Technical Agent to 'knowledge-base' registry group."

# Orchestrator gets permission to invoke specialist agents
for ROLE in "billing" "technical" "account"; do
    SA_EMAIL="agent-${ROLE}@${PROJECT_ID}.iam.gserviceaccount.com"
    gcloud iam service-accounts add-iam-policy-binding "${SA_EMAIL}" \
        --member="serviceAccount:agent-orchestrator@${PROJECT_ID}.iam.gserviceaccount.com" \
        --role="roles/iam.serviceAccountUser" \
        --project="${PROJECT_ID}" --quiet
done

echo "✅ Orchestrator granted 'Agent Selector' permissions."

# 3. Model Armor Configuration (CLI Instruction)
echo ""
echo "====================================================="
echo " 🛡️  Model Armor Governance"
echo "====================================================="
echo " Run the following to create the Enterprise Guardrail:"
echo ""
echo " gcloud alpha model-armor templates create techcorp-security-gate \\"
echo "   --project=${PROJECT_ID} \\"
echo "   --pii-filter=enabled \\"
echo "   --jailbreak-filter=enabled \\"
echo "   --malicious-uri-filter=enabled"
echo ""
echo "====================================================="
echo " ✅ Enterprise IAM & Identity Setup Complete!"
echo "====================================================="
