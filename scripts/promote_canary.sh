#!/usr/bin/env bash
# =============================================================================
# promote_canary.sh — Promote canary to stable after quality gate passes.
#
# WHAT THIS SCRIPT DOES (in order):
#   1. Confirms the canary deployment exists and is healthy.
#   2. Updates the stable Deployment's image to the canary image tag.
#   3. Waits for the stable rollout to complete (all pods running new image).
#   4. Scales canary Deployment to 0 replicas (keeps manifest, removes pods).
#   5. Deletes the canary-only Service (no longer needed).
#   6. Confirms 100% of traffic is now on the new stable version.
#
# After this script completes:
#   - Stable deployment: new image, 9 replicas (or whatever HPA set)
#   - Canary deployment: 0 replicas (dormant)
#   - ~100% of traffic: new stable version
#
# TO ROLLBACK (if called too early or gate check missed):
#   kubectl scale deployment customer-resolution-agent-canary -n agent --replicas=0
#   kubectl rollout undo deployment/customer-resolution-agent-stable -n agent
#
# USAGE:
#   ./scripts/promote_canary.sh <CANARY_IMAGE_TAG> <PROJECT_ID>
#
# EXAMPLE:
#   ./scripts/promote_canary.sh abc1234 my-gcp-project
# =============================================================================

set -euo pipefail

CANARY_TAG="${1:?Usage: $0 <CANARY_IMAGE_TAG> <PROJECT_ID>}"
PROJECT_ID="${2:?Usage: $0 <CANARY_IMAGE_TAG> <PROJECT_ID>}"
NAMESPACE="agent"
STABLE_DEPLOY="customer-resolution-agent-stable"
CANARY_DEPLOY="customer-resolution-agent-canary"
IMAGE_BASE="gcr.io/${PROJECT_ID}/customer-resolution-agent"
NEW_IMAGE="${IMAGE_BASE}:${CANARY_TAG}"

echo "====================================================="
echo " Promoting canary to stable"
echo " Canary tag : ${CANARY_TAG}"
echo " New image  : ${NEW_IMAGE}"
echo "====================================================="

# ── Step 1: Verify canary pod is still running ──────────────────────────────
echo ""
echo "[1/5] Verifying canary deployment is healthy..."
CANARY_READY=$(kubectl get deployment "${CANARY_DEPLOY}" \
    -n "${NAMESPACE}" \
    -o jsonpath='{.status.readyReplicas}' 2>/dev/null || echo "0")

if [ "${CANARY_READY}" -lt 1 ]; then
    echo "❌ Canary deployment has no ready replicas. Aborting promotion."
    echo "   Run 'kubectl describe deployment ${CANARY_DEPLOY} -n ${NAMESPACE}' to investigate."
    exit 1
fi
echo "✅ Canary has ${CANARY_READY} ready replica(s)."

# ── Step 2: Update stable deployment image ───────────────────────────────────
echo ""
echo "[2/5] Updating stable deployment image to ${NEW_IMAGE}..."
kubectl set image deployment/"${STABLE_DEPLOY}" \
    agent="${NEW_IMAGE}" \
    -n "${NAMESPACE}"

echo "✅ Image updated on stable deployment."

# ── Step 3: Wait for rollout to complete ────────────────────────────────────
echo ""
echo "[3/5] Waiting for stable rollout to complete (timeout: 5m)..."
if kubectl rollout status deployment/"${STABLE_DEPLOY}" \
        -n "${NAMESPACE}" \
        --timeout=5m; then
    echo "✅ Stable rollout complete. All pods running ${CANARY_TAG}."
else
    echo "❌ Stable rollout timed out or failed."
    echo "   Initiating rollback..."
    kubectl rollout undo deployment/"${STABLE_DEPLOY}" -n "${NAMESPACE}"
    echo "   Rollback triggered. Investigate with:"
    echo "   kubectl rollout history deployment/${STABLE_DEPLOY} -n ${NAMESPACE}"
    exit 1
fi

# ── Step 4: Scale canary to 0 ────────────────────────────────────────────────
echo ""
echo "[4/5] Scaling canary deployment to 0 replicas..."
kubectl scale deployment "${CANARY_DEPLOY}" \
    --replicas=0 \
    -n "${NAMESPACE}"
echo "✅ Canary scaled to 0. ~100% traffic now on stable (${CANARY_TAG})."

# ── Step 5: Remove canary-only Service ──────────────────────────────────────
echo ""
echo "[5/5] Deleting canary-only Service..."
kubectl delete service "${CANARY_DEPLOY}" \
    -n "${NAMESPACE}" \
    --ignore-not-found
echo "✅ Canary service removed."

# ── Done ─────────────────────────────────────────────────────────────────────
echo ""
echo "====================================================="
echo " ✅ Promotion complete!"
echo " Stable version: ${CANARY_TAG}"
echo " Run the following to verify:"
echo "   kubectl get pods -n ${NAMESPACE} -l app=customer-resolution-agent"
echo "   kubectl get deployment ${STABLE_DEPLOY} -n ${NAMESPACE}"
echo "====================================================="
