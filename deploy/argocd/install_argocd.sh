#!/usr/bin/env bash
# =============================================================================
# install_argocd.sh — Install ArgoCD + KEDA into the GKE cluster.
#
# Run this ONCE after scripts/setup_gcp.sh has created the cluster.
#
# WHAT IS INSTALLED:
#   1. KEDA           — event-driven autoscaler (HTTP request-based scaling)
#   2. ArgoCD         — GitOps controller (watches infra repo, syncs cluster)
#   3. KEDA HTTP add-on — needed for the http trigger type in ScaledObjects
#
# AFTER THIS, ArgoCD will:
#   - Watch YOUR infra repo (or deploy/ folder in this repo)
#   - Automatically apply ANY change you commit to deploy/k8s/
#   - Alert you when cluster state drifts from git state
#
# USAGE:
#   ./deploy/argocd/install_argocd.sh <ARGOCD_HOSTNAME> <GITHUB_REPO_URL>
#
# EXAMPLE:
#   ./deploy/argocd/install_argocd.sh \
#     argocd.my-company.com \
#     https://github.com/my-org/agent-evaluation-reference
# =============================================================================

set -euo pipefail

ARGOCD_HOSTNAME="${1:-argocd.localhost}"
REPO_URL="${2:?Usage: $0 <ARGOCD_HOSTNAME> <GITHUB_REPO_URL>}"

echo "====================================================="
echo "  Installing KEDA + ArgoCD on GKE"
echo "  ArgoCD UI : https://${ARGOCD_HOSTNAME}"
echo "  Watching  : ${REPO_URL}"
echo "====================================================="

# ── Step 1: Install KEDA ──────────────────────────────────────────────────────
echo ""
echo "[1/5] Installing KEDA..."
helm repo add kedacore https://kedacore.github.io/charts --force-update
helm repo update
helm upgrade --install keda kedacore/keda \
  --namespace keda \
  --create-namespace \
  --wait \
  --set prometheus.metricServer.enabled=true

echo "✅ KEDA installed."

# ── Step 2: Install KEDA HTTP Add-on ─────────────────────────────────────────
# This is the plugin that enables the `type: http` trigger in ScaledObjects.
# Without this, only external queue-based triggers work.
echo ""
echo "[2/5] Installing KEDA HTTP add-on..."
helm upgrade --install http-add-on kedacore/keda-add-ons-http \
  --namespace keda \
  --wait

echo "✅ KEDA HTTP add-on installed."

# ── Step 3: Install ArgoCD ────────────────────────────────────────────────────
echo ""
echo "[3/5] Installing ArgoCD..."
kubectl create namespace argocd --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -n argocd \
  -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml

echo "Waiting for ArgoCD pods to be ready (this takes ~2 minutes)..."
kubectl wait --for=condition=available deployment \
  argocd-server argocd-repo-server argocd-application-controller \
  -n argocd --timeout=5m

echo "✅ ArgoCD installed."

# ── Step 4: Register git repo with ArgoCD ────────────────────────────────────
# ArgoCD needs read access to your repo to watch for changes.
# For public repos: no credentials needed.
# For private repos: add a deploy key or use GitHub App authentication.
echo ""
echo "[4/5] Registering repo with ArgoCD..."
kubectl apply -f - <<EOF
apiVersion: v1
kind: Secret
metadata:
  name: agent-eval-repo
  namespace: argocd
  labels:
    argocd.argoproj.io/secret-type: repository
stringData:
  type: git
  url: "${REPO_URL}"
  # For private repos, add:
  # sshPrivateKey: |
  #   -----BEGIN OPENSSH PRIVATE KEY-----
  #   ...your deploy key here...
EOF
echo "✅ Repo registered."

# ── Step 5: Deploy ArgoCD Applications ───────────────────────────────────────
echo ""
echo "[5/5] Deploying ArgoCD Applications for each service..."
# Replace placeholder in application manifests with actual repo URL
for f in deploy/argocd/applications/*.yaml; do
  sed "s|YOUR_REPO_URL|${REPO_URL}|g" "${f}" | kubectl apply -f -
done
echo "✅ All ArgoCD Applications deployed."

# ── Get initial admin password ────────────────────────────────────────────────
ARGOCD_PASSWORD=$(kubectl -n argocd get secret argocd-initial-admin-secret \
  -o jsonpath="{.data.password}" | base64 -d)

echo ""
echo "====================================================="
echo " ✅ ArgoCD + KEDA installation complete!"
echo ""
echo " ArgoCD UI: https://${ARGOCD_HOSTNAME}  (after Ingress setup)"
echo " Or port-forward: kubectl port-forward svc/argocd-server 8443:443 -n argocd"
echo ""
echo " Initial admin credentials:"
echo "   username: admin"
echo "   password: ${ARGOCD_PASSWORD}"
echo ""
echo " IMPORTANT: Change the password immediately after first login."
echo "   argocd account update-password"
echo ""
echo " ArgoCD is now watching: ${REPO_URL}/deploy/k8s/"
echo " Any git push to that path will trigger automatic cluster sync."
echo "====================================================="
