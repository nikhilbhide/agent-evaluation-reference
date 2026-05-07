#!/usr/bin/env bash
# =============================================================================
# bootstrap_gcp.sh — One-shot project bootstrap for the reference stack.
#
# Run this AFTER `gcloud auth login` + `gcloud auth application-default login`
# to go from "I have a Google account" to "I have a project ready for
# `make deploy-mcp`".
#
# What it does (idempotent — safe to re-run):
#   1. Creates a new GCP project (or reuses an existing one)
#   2. Links a billing account
#   3. Sets the project as the active gcloud + ADC quota project
#   4. Enables every API the deploy scripts need
#   5. Grants the default Compute SA the roles Cloud Build needs to deploy
#      Cloud Run from source (the greenfield gotcha called out in README §0)
#   6. Creates the Agent Engine staging bucket
#   7. Writes/updates ./.env with GCP_PROJECT, GCP_LOCATION, GCP_STAGING_BUCKET
#
# USAGE:
#   PROJECT_ID=my-eval-ref-123 \
#   BILLING_ACCOUNT=0X0X0X-0X0X0X-0X0X0X \
#     ./scripts/bootstrap_gcp.sh
#
#   # Optional:
#   GCP_LOCATION=us-central1            # default region
#   ALERT_EMAIL=you@example.com         # written to .env for setup_alerting.py
#   PROJECT_NAME="Agent Eval Reference" # human-readable display name
#
# Find your billing account:
#   gcloud billing accounts list
# =============================================================================

set -euo pipefail

# ── Inputs ────────────────────────────────────────────────────────────────────
PROJECT_ID="${PROJECT_ID:-}"
BILLING_ACCOUNT="${BILLING_ACCOUNT:-}"
GCP_LOCATION="${GCP_LOCATION:-us-central1}"
PROJECT_NAME="${PROJECT_NAME:-Agent Evaluation Reference}"
ALERT_EMAIL="${ALERT_EMAIL:-}"

if [[ -z "${PROJECT_ID}" ]]; then
  echo "❌ PROJECT_ID is required."
  echo "   Example: PROJECT_ID=my-eval-ref-$(date +%s) BILLING_ACCOUNT=XXXXXX-XXXXXX-XXXXXX $0"
  exit 1
fi

# ── Pre-flight: gcloud auth ───────────────────────────────────────────────────
ACTIVE_ACCOUNT=$(gcloud auth list --filter=status:ACTIVE --format='value(account)' 2>/dev/null | head -n1)
if [[ -z "${ACTIVE_ACCOUNT}" ]]; then
  echo "❌ No active gcloud account. Run: gcloud auth login"
  exit 1
fi

echo "====================================================="
echo " 🚀 Bootstrapping reference stack"
echo " Account     : ${ACTIVE_ACCOUNT}"
echo " Project     : ${PROJECT_ID}"
echo " Region      : ${GCP_LOCATION}"
echo "====================================================="

# ── 1. Create or reuse the project ────────────────────────────────────────────
if gcloud projects describe "${PROJECT_ID}" >/dev/null 2>&1; then
  echo "ℹ️  Project ${PROJECT_ID} already exists — reusing."
else
  echo "🆕 Creating project ${PROJECT_ID}..."
  if ! gcloud projects create "${PROJECT_ID}" --name="${PROJECT_NAME}" --quiet; then
    cat <<EOF
❌ Project creation failed. Common causes:

  - Project ID is globally taken. Try a more unique ID.
  - You're at the personal-account project quota (typically 12-30).
    Free a slot at https://console.cloud.google.com/cloud-resource-manager
    or request a quota increase.
  - Your account lacks roles/resourcemanager.projectCreator. If your account
    is in a Workspace/Cloud Identity org with a restrictive policy, ask
    an org admin to grant you the role or create the project for you.

Once resolved, re-run this script — it will reuse the project if it now
exists.
EOF
    exit 1
  fi
fi

# ── 2. Link billing ───────────────────────────────────────────────────────────
CURRENT_BILLING=$(gcloud billing projects describe "${PROJECT_ID}" \
  --format='value(billingAccountName)' 2>/dev/null || true)

if [[ -n "${CURRENT_BILLING}" && "${CURRENT_BILLING}" == *"/"* ]]; then
  echo "ℹ️  Project already linked to billing: ${CURRENT_BILLING}"
else
  if [[ -z "${BILLING_ACCOUNT}" ]]; then
    echo ""
    echo "⚠️  Project has no billing account linked, and BILLING_ACCOUNT was not set."
    echo "   List your billing accounts with:"
    echo "     gcloud billing accounts list"
    echo "   Then re-run with BILLING_ACCOUNT=<id>"
    exit 1
  fi
  echo "💳 Linking billing account ${BILLING_ACCOUNT}..."
  gcloud billing projects link "${PROJECT_ID}" \
    --billing-account="${BILLING_ACCOUNT}" --quiet
fi

# ── 3. Set as active project + ADC quota project ──────────────────────────────
gcloud config set project "${PROJECT_ID}" --quiet
gcloud auth application-default set-quota-project "${PROJECT_ID}" --quiet 2>/dev/null \
  || echo "   (skip) ADC not configured — run: gcloud auth application-default login"

# ── 4. Enable APIs ────────────────────────────────────────────────────────────
echo ""
echo "🔌 Enabling APIs (this can take 1-2 min on first run)..."
gcloud services enable \
  aiplatform.googleapis.com \
  run.googleapis.com \
  iam.googleapis.com \
  cloudresourcemanager.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  bigquery.googleapis.com \
  cloudtrace.googleapis.com \
  logging.googleapis.com \
  monitoring.googleapis.com \
  storage.googleapis.com \
  modelarmor.googleapis.com \
  agentregistry.googleapis.com \
  cloudapiregistry.googleapis.com \
  securitycenter.googleapis.com \
  --project="${PROJECT_ID}" --quiet

# ── 5. Grant Compute SA roles for Cloud Build → Cloud Run from source ─────────
PROJECT_NUMBER=$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)')
COMPUTE_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"

echo ""
echo "🛂 Granting build/deploy roles to ${COMPUTE_SA}..."
for ROLE in \
  roles/storage.objectViewer \
  roles/run.builder \
  roles/logging.logWriter \
  roles/artifactregistry.writer; do
  gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${COMPUTE_SA}" \
    --role="${ROLE}" \
    --condition=None --quiet >/dev/null
  echo "   ✅ ${ROLE}"
done

# ── 6. Create the staging bucket ──────────────────────────────────────────────
STAGING_BUCKET_NAME="agent-eval-staging-${PROJECT_ID}"
STAGING_BUCKET_URI="gs://${STAGING_BUCKET_NAME}"

echo ""
if gcloud storage buckets describe "${STAGING_BUCKET_URI}" \
    --project="${PROJECT_ID}" >/dev/null 2>&1; then
  echo "ℹ️  Staging bucket ${STAGING_BUCKET_URI} already exists."
else
  echo "🪣 Creating staging bucket ${STAGING_BUCKET_URI}..."
  gcloud storage buckets create "${STAGING_BUCKET_URI}" \
    --project="${PROJECT_ID}" \
    --location="${GCP_LOCATION}" \
    --uniform-bucket-level-access \
    --quiet
fi

# ── 7. Write .env ─────────────────────────────────────────────────────────────
ENV_FILE="$(cd "$(dirname "$0")/.." && pwd)/.env"
echo ""
echo "📝 Writing ${ENV_FILE}..."
{
  echo "export GCP_PROJECT=${PROJECT_ID}"
  echo "export GOOGLE_CLOUD_PROJECT=${PROJECT_ID}"
  echo "export GCP_LOCATION=${GCP_LOCATION}"
  echo "export GCP_STAGING_BUCKET=${STAGING_BUCKET_URI}"
  if [[ -n "${ALERT_EMAIL}" ]]; then
    echo "export ALERT_EMAILS=${ALERT_EMAIL}"
  fi
} > "${ENV_FILE}"

echo ""
echo "====================================================="
echo " ✅ Bootstrap complete"
echo "====================================================="
echo "Next:"
echo "  source .env"
echo "  make deploy-mcp"
echo "  ./scripts/setup_enterprise_iam.sh \$GCP_PROJECT \$GCP_LOCATION mcp-server"
echo ""
