#!/usr/bin/env bash
# =============================================================================
# setup_github_secrets.sh — Automate GitHub Actions secret configuration.
#
# This script uses the GitHub CLI (gh) to set all required repository secrets
# for the CI/CD pipeline. It auto-detects values from your GCP project and
# the output of setup_wif.sh.
#
# PREREQUISITES:
#   1. GitHub CLI (gh) installed and authenticated: gh auth login
#   2. GCP CLI (gcloud) authenticated with your project
#   3. setup_wif.sh has been run (to create the WIF pool/provider)
#
# USAGE:
#   ./scripts/setup_github_secrets.sh <PROJECT_ID> <GITHUB_REPO>
#
# EXAMPLE:
#   ./scripts/setup_github_secrets.sh my-project octocat/agent-evaluation-reference
# =============================================================================

set -euo pipefail

PROJECT_ID="${1:?Usage: $0 <PROJECT_ID> <GITHUB_REPO>}"
REPO="${2:?Usage: $0 <PROJECT_ID> <GITHUB_REPO>}"

echo "====================================================="
echo " Setting up GitHub Actions Secrets"
echo " Project : ${PROJECT_ID}"
echo " Repo    : ${REPO}"
echo "====================================================="

# ── Verify prerequisites ─────────────────────────────────────────────────────
echo ""
echo "Checking prerequisites..."

if ! command -v gh &> /dev/null; then
  echo "❌ GitHub CLI (gh) not found. Install: https://cli.github.com/"
  exit 1
fi

if ! gh auth status &> /dev/null; then
  echo "❌ Not logged in to GitHub CLI. Run: gh auth login"
  exit 1
fi

if ! command -v gcloud &> /dev/null; then
  echo "❌ Google Cloud CLI (gcloud) not found."
  exit 1
fi

echo "✅ Prerequisites verified."

# ── 1. GCP_PROJECT_ID ─────────────────────────────────────────────────────────
echo ""
echo "[1/5] Setting GCP_PROJECT_ID..."
echo "${PROJECT_ID}" | gh secret set GCP_PROJECT_ID --repo="${REPO}"
echo "✅ GCP_PROJECT_ID = ${PROJECT_ID}"

# ── 2. GCP_WORKLOAD_IDENTITY_PROVIDER ─────────────────────────────────────────
echo ""
echo "[2/5] Detecting WIF provider..."
POOL_ID=$(gcloud iam workload-identity-pools describe "github-pool" \
  --project="${PROJECT_ID}" --location="global" --format="value(name)" 2>/dev/null) || {
  echo "❌ WIF pool 'github-pool' not found. Run setup_wif.sh first."
  exit 1
}
WIF_PROVIDER="${POOL_ID}/providers/github-provider"
echo "${WIF_PROVIDER}" | gh secret set GCP_WORKLOAD_IDENTITY_PROVIDER --repo="${REPO}"
echo "✅ GCP_WORKLOAD_IDENTITY_PROVIDER = ${WIF_PROVIDER}"

# ── 3. GCP_SERVICE_ACCOUNT ────────────────────────────────────────────────────
echo ""
echo "[3/5] Setting GCP_SERVICE_ACCOUNT..."
SA_EMAIL="agent-runtime@${PROJECT_ID}.iam.gserviceaccount.com"
echo "${SA_EMAIL}" | gh secret set GCP_SERVICE_ACCOUNT --repo="${REPO}"
echo "✅ GCP_SERVICE_ACCOUNT = ${SA_EMAIL}"

# ── 4. GKE_CLUSTER_NAME & GKE_CLUSTER_ZONE ───────────────────────────────────
echo ""
echo "[4/5] Detecting GKE cluster..."
CLUSTER_INFO=$(gcloud container clusters list \
  --project="${PROJECT_ID}" \
  --format="value(name,location)" \
  --limit=1 2>/dev/null) || {
  echo "⚠️  No GKE cluster found. Skipping GKE secrets."
  echo "   You can set these manually later if you deploy to GKE."
  CLUSTER_INFO=""
}

if [ -n "${CLUSTER_INFO}" ]; then
  CLUSTER_NAME=$(echo "${CLUSTER_INFO}" | awk '{print $1}')
  CLUSTER_ZONE=$(echo "${CLUSTER_INFO}" | awk '{print $2}')

  echo "${CLUSTER_NAME}" | gh secret set GKE_CLUSTER_NAME --repo="${REPO}"
  echo "${CLUSTER_ZONE}" | gh secret set GKE_CLUSTER_ZONE --repo="${REPO}"
  echo "✅ GKE_CLUSTER_NAME = ${CLUSTER_NAME}"
  echo "✅ GKE_CLUSTER_ZONE = ${CLUSTER_ZONE}"
fi

# ── 5. Verify all secrets ────────────────────────────────────────────────────
echo ""
echo "[5/5] Verifying secrets..."
echo ""
echo "  Secrets set for ${REPO}:"
gh secret list --repo="${REPO}"

echo ""
echo "====================================================="
echo " ✅ GitHub Secrets Setup Complete!"
echo ""
echo " All required secrets have been configured."
echo " Push to main to trigger the CD pipeline:"
echo "   git push origin main"
echo ""
echo " NOTE: The CD pipeline uses GITHUB_TOKEN (automatic)"
echo "       for GitOps commits. No PAT needed!"
echo "====================================================="
