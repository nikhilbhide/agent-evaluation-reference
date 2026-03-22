#!/usr/bin/env bash
# =============================================================================
# setup_gcp.sh — One-time GCP infrastructure setup for the reference project.
#
# Run this ONCE before your first deployment. It:
#   1. Enables required GCP APIs (incl. Artifact Registry)
#   2. Creates a GKE cluster
#   3. Creates a GCP Service Account for the agent runtime
#   4. Grants IAM roles (Vertex AI, Artifact Registry, Storage)
#   5. Creates gcr.io Artifact Registry repository for Docker images
#   6. Configures Workload Identity (KSA → GSA binding)
#   7. Applies base Kubernetes manifests
#   8. Creates the agent-config Secret
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
echo "[1/8] Enabling required APIs..."
gcloud services enable \
  aiplatform.googleapis.com \
  container.googleapis.com \
  containerregistry.googleapis.com \
  artifactregistry.googleapis.com \
  cloudbuild.googleapis.com \
  iamcredentials.googleapis.com \
  --project="${PROJECT_ID}"
echo "✅ APIs enabled."

# ── Step 2: Create GKE Autopilot cluster ──────────────────────────────────────
# Using Autopilot: no node pool management needed, Workload Identity enabled by default.
echo ""
echo "[2/8] Creating GKE Autopilot cluster (this takes ~5 minutes)..."
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
echo "[3/8] Creating GCP Service Account: ${GSA_NAME}..."
gcloud iam service-accounts create "${GSA_NAME}" \
  --display-name="Agent Runtime Service Account" \
  --project="${PROJECT_ID}" || echo "SA may already exist, continuing..."
echo "✅ Service Account created."

# ── Step 4: Grant IAM roles ─────────────────────────────────────────────────
echo ""
echo "[4/8] Granting IAM roles to ${GSA_EMAIL}..."

# Vertex AI — needed for Gemini model calls and evaluation
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${GSA_EMAIL}" \
  --role="roles/aiplatform.user" \
  --quiet

# Artifact Registry Admin — needed to create and push Docker images to gcr.io
# (gcr.io is backed by Artifact Registry in newer GCP projects)
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${GSA_EMAIL}" \
  --role="roles/artifactregistry.admin" \
  --quiet

# Storage Admin — needed for legacy gcr.io bucket operations
gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${GSA_EMAIL}" \
  --role="roles/storage.admin" \
  --quiet

echo "✅ IAM roles granted (aiplatform.user, artifactregistry.admin, storage.admin)."

# ── Step 5: Create gcr.io Artifact Registry repository ──────────────────────
# In newer GCP projects, gcr.io is routed through Artifact Registry.
# The repository MUST exist before the first docker push, unlike legacy GCR.
echo ""
echo "[5/8] Creating gcr.io Artifact Registry repository..."
gcloud artifacts repositories create gcr.io \
  --repository-format=docker \
  --location=us \
  --project="${PROJECT_ID}" || echo "Repository may already exist, continuing..."
echo "✅ gcr.io repository ready."

# ── Step 6: Configure Workload Identity ──────────────────────────────────────
echo ""
echo "[6/8] Configuring Workload Identity (KSA → GSA)..."

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

# ── Step 7: Apply base Kubernetes manifests ───────────────────────────────────
echo ""
echo "[7/8] Applying base Kubernetes manifests..."
sed "s|YOUR_PROJECT_ID|${PROJECT_ID}|g" \
  deploy/k8s/stable-deployment.yaml | kubectl apply -f -
kubectl apply -f deploy/k8s/service.yaml
kubectl apply -f deploy/k8s/hpa.yaml
echo "✅ Base manifests applied."

# ── Step 8: Create the agent-config Secret ────────────────────────────────────
echo ""
echo "[8/8] Creating agent-config Secret..."
kubectl create secret generic agent-config \
  --namespace="${NAMESPACE}" \
  --from-literal=gcp_project="${PROJECT_ID}" \
  --dry-run=client -o yaml | kubectl apply -f -
echo "✅ Secret created."

echo ""
echo "====================================================="
echo " ✅ GCP Infrastructure Setup Complete!"
echo ""
echo " Next steps:"
echo "   1. Run the WIF script to connect GitHub Actions to GCP:"
echo "      ./scripts/setup_wif.sh \${PROJECT_ID} YOUR_GITHUB_USER/YOUR_REPO_NAME"
echo ""
echo "   2. AUTOMATED: Set all GitHub Actions secrets using the new script:"
echo "      # (Requires GitHub CLI 'gh' and being logged in)"
echo "      ./scripts/setup_github_secrets.sh \${PROJECT_ID} YOUR_GITHUB_USER/YOUR_REPO_NAME"
echo ""
echo "   3. (Manual Alternative) Add these GitHub Actions secrets:"
echo "      GCP_WORKLOAD_IDENTITY_PROVIDER = (output from setup_wif.sh)"
echo "      GCP_SERVICE_ACCOUNT            = \${GSA_EMAIL}"
echo "      GCP_PROJECT_ID                 = \${PROJECT_ID}"
echo "      GKE_CLUSTER_NAME               = \${CLUSTER_NAME}"
echo "      GKE_CLUSTER_ZONE               = \${ZONE}"
echo ""
echo "   ⚠️  No GCP_SA_KEY or INFRA_REPO_TOKEN needed — we use WIF and GITHUB_TOKEN!"
echo ""
echo "   4. Push to main to trigger your first CD deployment:"
echo "      git push origin main"
echo ""
echo "   4. Test locally:"
echo "      pip install -e '.[dev]'"
echo "      agent-eval run-eval --dataset data/golden_dataset.json"
echo "====================================================="
