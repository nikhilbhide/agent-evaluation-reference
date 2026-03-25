#!/usr/bin/env bash
# =============================================================================
# enable_canary_traffic.sh
#
# PHASE TRANSITION: Dark Launch → Canary (20% real traffic)
#
# Called after the CD quality gate (evaluation) passes in dark launch mode.
# It does two things atomically:
#
#   1. Adds `app: customer-resolution-agent` to the canary pod label.
#      → The main Kubernetes Service now selects this pod.
#      → Real user traffic starts flowing to it.
#
#   2. Scales stable down to 4 replicas.
#      → Traffic split becomes: 4 stable + 1 canary = 20% to canary.
#      → HPA is NOT disabled — if load spikes, stable scales up, which
#        lowers canary's share further. That's acceptable and safe.
#
# WHAT HAPPENS AFTER THIS SCRIPT:
#   - Monitor error rate and latency in Cloud Monitoring for a soak period.
#   - If metrics are healthy: run promote_canary.sh
#   - If metrics spike:       run rollback_canary.sh
#
# USAGE:
#   ./scripts/enable_canary_traffic.sh <CANARY_TAG>
#
# EXAMPLE:
#   ./scripts/enable_canary_traffic.sh abc1234
# =============================================================================

set -euo pipefail

CANARY_TAG="${1:?Usage: $0 <CANARY_TAG>}"
NAMESPACE="agent"
CANARY_DEPLOY="customer-resolution-agent-canary"
STABLE_DEPLOY="customer-resolution-agent-stable"
CANARY_TRAFFIC_PERCENT=20
# stable replicas = (100 - CANARY_TRAFFIC_PERCENT) / CANARY_TRAFFIC_PERCENT
# 80/20 split → 4 stable, 1 canary
STABLE_REPLICAS=4

echo "====================================================="
echo " Enabling canary traffic: dark launch → 20% canary"
echo " Canary tag : ${CANARY_TAG}"
echo "====================================================="

# ── Step 1: Patch canary pod labels to add `app` label ───────────────────────
# kubectl patch updates the pod template labels in the Deployment spec.
# Kubernetes will rolling-restart the canary pod with the new label.
# Within seconds, the main Service starts routing ~20% of traffic to it.
echo ""
echo "[1/3] Adding 'app: customer-resolution-agent' label to canary pods..."
kubectl patch deployment "${CANARY_DEPLOY}" \
  -n "${NAMESPACE}" \
  --type=json \
  -p='[{
    "op": "add",
    "path": "/spec/template/metadata/labels/app",
    "value": "customer-resolution-agent"
  }]'

# Update the phase annotation for observability
kubectl annotate deployment "${CANARY_DEPLOY}" \
  -n "${NAMESPACE}" \
  "deployment.kubernetes.io/canary-phase=canary-20pct" \
  --overwrite

echo "✅ Canary pod label added. Main Service will now route to it."

# ── Step 2: Wait for pod to restart with new label ──────────────────────────
echo ""
echo "[2/3] Waiting for canary pod to restart and become ready..."
# Increased timeout to 5 minutes to account for rolling update delays
kubectl rollout status deployment/"${CANARY_DEPLOY}" \
  -n "${NAMESPACE}" --timeout=5m
echo "✅ Canary pod is ready and receiving traffic."

# ── Step 3: Scale stable to get 20% split ─────────────────────────────────
# Note: If HPA scales stable up due to load, canary percentage drops.
# That's fine — under high load, canary's share automatically decreases,
# reducing blast radius when we need it most.
echo ""
echo "[3/3] Scaling stable to ${STABLE_REPLICAS} replicas for ~${CANARY_TRAFFIC_PERCENT}% canary split..."
kubectl scale deployment "${STABLE_DEPLOY}" \
  --replicas="${STABLE_REPLICAS}" \
  -n "${NAMESPACE}"
echo "✅ Stable scaled to ${STABLE_REPLICAS} pods."

# ── Summary ──────────────────────────────────────────────────────────────────
echo ""
echo "====================================================="
echo " ✅ Canary is now receiving ~${CANARY_TRAFFIC_PERCENT}% of real traffic."
echo ""
echo " Traffic split:"
echo "   stable: ${STABLE_REPLICAS} pods → ~$((100 - CANARY_TRAFFIC_PERCENT))% of traffic"
echo "   canary: 1 pod  → ~${CANARY_TRAFFIC_PERCENT}% of traffic"
echo ""
echo " NEXT STEPS — monitor for the soak period:"
echo "   1. Check error rate in Cloud Monitoring (target: < 1%)"
echo "   2. Check p99 latency (target: < 5000ms)"
echo "   3. Check agent eval scores once more (optional, see README)"
echo ""
echo " If healthy → run: ./scripts/promote_canary.sh ${CANARY_TAG} <PROJECT_ID>"
echo " If errors  → run: ./scripts/rollback_canary.sh"
echo "====================================================="
