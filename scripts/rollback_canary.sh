#!/usr/bin/env bash
# =============================================================================
# rollback_canary.sh — Emergency rollback during canary phase.
#
# Can be called at ANY phase:
#   - Dark launch phase:   removes pod, zero user impact
#   - Canary 20% phase:    removes pod, traffic immediately returns to stable
#   - After promotion:     use kubectl rollout undo instead (see comment below)
#
# HOW ROLLBACK WORKS:
#   1. Delete the canary Deployment and its Service.
#   2. Scale stable back up to 9 replicas (original count).
#   3. Main Service now routes 100% to stable again.
#      No user sees the rollback happen — their in-flight requests to canary
#      are completed gracefully (terminationGracePeriodSeconds), then the pod
#      is gone. All subsequent requests go to stable.
#
# USAGE:
#   ./scripts/rollback_canary.sh
#
# NOTE: If you've already promoted and want to roll back after promotion,
#   the canary deployment no longer exists. Use:
#   kubectl rollout undo deployment/customer-resolution-agent-stable -n agent
# =============================================================================

set -euo pipefail

NAMESPACE="agent"
CANARY_DEPLOY="customer-resolution-agent-canary"
STABLE_DEPLOY="customer-resolution-agent-stable"
STABLE_REPLICAS_ORIGINAL=9

echo "====================================================="
echo " 🚨 Rolling back canary deployment"
echo "====================================================="

# ── Step 1: Get current canary image for rollback report ─────────────────────
CANARY_IMAGE=$(kubectl get deployment "${CANARY_DEPLOY}" \
  -n "${NAMESPACE}" \
  -o jsonpath='{.spec.template.spec.containers[0].image}' 2>/dev/null || echo "unknown")
echo "Canary image being rolled back: ${CANARY_IMAGE}"
echo ""

# ── Step 2: Delete canary deployment (graceful — in-flight reqs complete) ────
echo "[1/3] Deleting canary deployment..."
kubectl delete deployment "${CANARY_DEPLOY}" \
  -n "${NAMESPACE}" \
  --ignore-not-found
echo "✅ Canary deployment deleted."

# ── Step 3: Delete canary-only Service ───────────────────────────────────────
echo ""
echo "[2/3] Deleting canary Service..."
kubectl delete service "${CANARY_DEPLOY}" \
  -n "${NAMESPACE}" \
  --ignore-not-found
echo "✅ Canary Service deleted."

# ── Step 4: Restore stable to full replica count ──────────────────────────────
# If we scaled stable down to 4 for the 20% canary split, we need to restore it.
# If we're still in dark launch phase, stable is still at 9 — this is a no-op.
echo ""
echo "[3/3] Restoring stable to ${STABLE_REPLICAS_ORIGINAL} replicas..."
kubectl scale deployment "${STABLE_DEPLOY}" \
  --replicas="${STABLE_REPLICAS_ORIGINAL}" \
  -n "${NAMESPACE}"
echo "✅ Stable restored to ${STABLE_REPLICAS_ORIGINAL} replicas."

echo ""
echo "====================================================="
echo " ✅ Rollback complete."
echo " 100% of traffic is back on stable (unchanged version)."
echo " Rolled back image: ${CANARY_IMAGE}"
echo ""
echo " Investigate the failure:"
echo "   kubectl logs -l version=canary -n ${NAMESPACE} --previous"
echo "   Check Vertex AI Experiments for eval scores"
echo "====================================================="

# Exit non-zero so the calling pipeline (GitHub Actions / Cloud Build)
# marks the deployment as FAILED in the run history.
exit 1
