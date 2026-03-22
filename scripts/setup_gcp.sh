#!/usr/bin/env bash
# =============================================================================
# setup_gcp.sh — One-time GCP infrastructure setup for the reference project.
#
# Run this ONCE before your first deployment. It:
#   1. Enables required GCP APIs
#   2. Creates a GKE cluster
#   3. Creates a GCP Service Account for the agent runtime
#   4. Grants Vertex AI permissions to that SA
#   5. Configures Workload Identity (KSA → GSA binding)
#   6. Applies base Kubernetes manifests
#   7. Creates the agent-config Secret
#
# USAGE:
#   ./scripts/setup_gcp.sh <PROJECT_ID> <CLUSTER_NAME> <ZONE>
#
# EXAMPLE:
#   ./scripts/setup_gcp.sh my-gcp-project agent-cluster us-central1-a
# =============================================================================

set -euo pipefail

PROJECT_ID="${1:?Usage: $0 <PROJECT_ID> <CLUSTER_NAME> <ZONE>}"
CLUSTER_NAME="${2:?Usage: $0 <PROJECT_ID> <CLUSTER_NAME> <ZONE>}"
ZONE="${3:?Usage: $0 <PROJECT_ID> <CLUSTER_NAME> <ZONE>}"
REGION="${ZONE%-*}"           # e.g. us-central1-a → us-central1
GSA_NAME="agent-runtime"
GSA_EMAIL="${GSA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
NAMESPACE="agent"
KSA_NAME="agent-workload-sa"

echo "====================================================="
echo " GCP Agent Eval — One-time infrastructure setup"
echo " Project : ${PROJECT_ID}"
echo " Cluster : ${CLUSTER_NAME} (${ZONE})"
echo "====================================================="

# ── Step 1: Enable APIs ───────────────────────────────────────────────────────
echo ""
echo "[1/7] Enabling required APIs..."
gcloud services enable \
  aiplatform.googleapis.com \
  container.googleapis.com \
  containerregistry.googleapis.com \
  cloudbuild.googleapis.com \
  --project="${PROJECT_ID}"
echo "✅ APIs enabled."

# ── Step 2: Create GKE Autopilot cluster ──────────────────────────────────────
# Using Autopilot: no node pool management needed, Workload Identity enabled by default.
echo ""
echo "[2/7] Creating GKE Autopilot cluster (this takes ~5 minutes)..."
gcloud container clusters create-auto "${CLUSTER_NAME}" \
  --region="${REGION}" \
  --project="${PROJECT_ID}" \
  --release-channel=regular || echo "Cluster may already exist, continuing..."
echo "✅ Cluster ready."

# Get credentials
gcloud container clusters get-credentials "${CLUSTER_NAME}" \
  --region="${REGION}" \
  --project="${PROJECT_ID}"

# ── Step 3: Create GCP Service Account ───────────────────────────────────────
echo ""
echo "[3/7] Creating GCP Service Account: ${GSA_NAME}..."
gcloud iam service-accounts create "${GSA_NAME}" \
  --display-name="Agent Runtime Service Account" \
  --project="${PROJECT_ID}" || echo "SA may already exist, continuing..."
echo "✅ Service Account created."

# ── Step 4: Grant Vertex AI permissions ─────────────────────────────────────
echo ""
echo "[4/7] Granting Vertex AI user role to ${GSA_EMAIL}..."
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${GSA_EMAIL}" \
  --role="roles/aiplatform.user"
echo "✅ IAM binding added."

# ── Step 5: Configure Workload Identity ──────────────────────────────────────
echo ""
echo "[5/7] Configuring Workload Identity (KSA → GSA)..."

# Create namespace and KSA first
kubectl apply -f deploy/k8s/namespace.yaml

# Patch workload-identity.yaml with actual project ID
sed "s|YOUR_PROJECT_ID|${PROJECT_ID}|g" \
  deploy/k8s/workload-identity.yaml | kubectl apply -f -

# Allow the KSA to impersonate the GSA
gcloud iam service-accounts add-iam-policy-binding "${GSA_EMAIL}" \
  --role="roles/iam.workloadIdentityUser" \
  --member="serviceAccount:${PROJECT_ID}.svc.id.goog[${NAMESPACE}/${KSA_NAME}]" \
  --project="${PROJECT_ID}"
echo "✅ Workload Identity configured."

# ── Step 6: Apply base Kubernetes manifests ───────────────────────────────────
echo ""
echo "[6/7] Applying base Kubernetes manifests..."
sed "s|YOUR_PROJECT_ID|${PROJECT_ID}|g" \
  deploy/k8s/stable-deployment.yaml | kubectl apply -f -
kubectl apply -f deploy/k8s/service.yaml
kubectl apply -f deploy/k8s/hpa.yaml
echo "✅ Base manifests applied."

# ── Step 7: Create the agent-config Secret ────────────────────────────────────
echo ""
echo "[7/7] Creating agent-config Secret..."
kubectl create secret generic agent-config \
  --namespace="${NAMESPACE}" \
  --from-literal=gcp_project="${PROJECT_ID}" \
  --dry-run=client -o yaml | kubectl apply -f -
echo "✅ Secret created."

echo ""
echo "====================================================="
echo " ✅ Setup complete!"
echo ""
echo " Next steps:"
echo "   1. Add these GitHub Actions secrets:"
echo "      GCP_PROJECT_ID  = ${PROJECT_ID}"
echo "      GKE_CLUSTER_NAME = ${CLUSTER_NAME}"
echo "      GKE_CLUSTER_ZONE = ${ZONE}"
echo "      GCP_SA_KEY       = (JSON key for a CI service account)"
echo ""
echo "   2. Push to main to trigger your first CD deployment:"
echo "      git push origin main"
echo ""
echo "   3. Test locally:"
echo "      pip install -e '.[dev]'"
echo "      agent-eval run-eval --dataset data/golden_dataset.json"
echo "====================================================="
