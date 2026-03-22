#!/usr/bin/env bash
# =============================================================================
# setup_workload_identity.sh — Connect GitHub to GCP without long-lived keys.
#
# WHY USE THIS?
#   Instead of storing a "Service Account Key" (which can be leaked/stolen),
#   this script creates a trust link. GitHub Actions will "ask" GCP for a 
#   temporary 1-hour token to run the deployment, then it expires.
#
# USAGE:
#   ./scripts/setup_wif.sh <PROJECT_ID> <REPO_NAME>
#
# REPO_NAME example: "your-github-user/agent-evaluation-reference"
# =============================================================================

set -euo pipefail

PROJECT_ID="${1:?Usage: $0 <PROJECT_ID> <GITHUB_REPO_NAME>}"
REPO_NAME="${2:?Usage: $0 <PROJECT_ID> <GITHUB_REPO_NAME>}"

echo "====================================================="
echo " Configuring Workload Identity Federation"
echo " Project : ${PROJECT_ID}"
echo " Repo    : ${REPO_NAME}"
echo "====================================================="

# 1. Enable IAM Credentials API
gcloud services enable iamcredentials.googleapis.com --project="${PROJECT_ID}"

# 2. Create the Workload Identity Pool
gcloud iam workload-identity-pools create "github-pool" \
  --project="${PROJECT_ID}" \
  --location="global" \
  --display-name="GitHub Actions Pool" || echo "Pool already exists, continuing..."

# 3. Create the Workload Identity Provider
# This tells GCP to trust tokens coming from githubusercontent.com
gcloud iam workload-identity-pools providers create-oidc "github-provider" \
  --project="${PROJECT_ID}" \
  --location="global" \
  --workload-identity-pool="github-pool" \
  --display-name="GitHub Provider" \
  --attribute-mapping="google.subject=assertion.sub,attribute.actor=assertion.actor,attribute.repository=assertion.repository" \
  --issuer-uri="https://token.actions.githubusercontent.com" || echo "Provider already exists, continuing..."

# 4. Get the Pool ID (needed for GitHub)
POOL_ID=$(gcloud iam workload-identity-pools describe "github-pool" \
  --project="${PROJECT_ID}" --location="global" --format="value(name)")

echo ""
echo "✅ Workload Identity Pool created."
echo "   Provider ID: ${POOL_ID}/providers/github-provider"
echo ""

# 5. Connect the Provider to your Service Account
# This gives your GitHub repository permission to "impersonate" the CI Service Account
SA_EMAIL="agent-runtime@${PROJECT_ID}.iam.gserviceaccount.com" # From setup_gcp.sh

gcloud iam service-accounts add-iam-policy-binding "${SA_EMAIL}" \
  --project="${PROJECT_ID}" \
  --role="roles/iam.workloadIdentityUser" \
  --member="principalSet://iam.googleapis.com/${POOL_ID}/attribute.repository/${REPO_NAME}"

echo "====================================================="
echo " ✅ Setup Complete!"
echo ""
echo " 1. Go to your GitHub Repo -> Settings -> Secrets and variables -> Actions"
echo " 2. Add these TWO secrets:"
echo "    GCP_WORKLOAD_IDENTITY_PROVIDER = ${POOL_ID}/providers/github-provider"
echo "    GCP_SERVICE_ACCOUNT           = ${SA_EMAIL}"
echo ""
echo " 3. Push to main and watch the CD pipeline trigger!"
echo "====================================================="
