"""Provision an Online Evaluator (continuous eval on live agent traffic).

This is the production-side counterpart to ``runner.py``: the runner does
offline batch eval over the golden dataset; the OnlineEvaluator samples
live traces from Cloud Trace and runs the same family of LLM-as-judge
metrics on them. Results land in the ``Online monitors`` tab of the
Vertex AI Agent Evaluation console and can be queried via the
``evaluationRuns`` API.

Resource: ``projects/{project}/locations/{location}/onlineEvaluators``
API:      ``aiplatform.googleapis.com/v1beta1``  (REST only — no Python
          SDK wrapper as of google-cloud-aiplatform 1.148.x)

Hard prerequisite: agents and the MCP server must emit OTel traces with
``semconvVersion >= 1.39.0`` (Batch D wired this on the MCP side; Agent
Engine's ``enable_tracing=True`` does the agent side). The monitor reads
those spans — without them, sampling returns zero traces.

Idempotent: re-runs replace the same monitor ID via PATCH on the
mutable fields, so tweaking sampling/metrics is a one-command update.

Tunables (env):
  ``GCP_PROJECT``                   required
  ``GCP_LOCATION``                  default us-central1
  ``ONLINE_MONITOR_ID``             default customer-resolution-monitor
  ``ONLINE_MONITOR_PERCENTAGE``     default 10  (1-100)
  ``ONLINE_MONITOR_MAX_PER_RUN``    default 100 (0 = unbounded)
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import google.auth
import google.auth.transport.requests
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agents._shared.config import GCP_LOCATION, require  # noqa: E402

PROJECT_ID = require("GCP_PROJECT")
LOCATION = os.environ.get("GCP_LOCATION", GCP_LOCATION)
MONITOR_ID = os.environ.get("ONLINE_MONITOR_ID", "customer-resolution-monitor")
SAMPLE_PERCENTAGE = int(os.environ.get("ONLINE_MONITOR_PERCENTAGE", "10"))
MAX_PER_RUN = int(os.environ.get("ONLINE_MONITOR_MAX_PER_RUN", "100"))

ORCHESTRATOR_RESOURCE_FILE = ROOT / "deployed_agent_resource.txt"

# Predefined metric names accepted by ``predefinedMetricSpec.metricSpecName``.
# Discovered from the API's INVALID_ARGUMENT response — the OnlineEvaluator
# accepts a different (smaller, more agent-specific) set than the offline
# EvalTask API. These are the four valid names today; offline eval uses
# the SDK-side ``BUILTIN_METRICS`` list which is a different surface.
PREDEFINED_METRICS: list[tuple[str, str]] = [
    ("safety_v1", "Safety"),
    ("hallucination_v1", "Hallucination"),
    ("final_response_quality_v1", "Final Response Quality"),
    ("tool_use_quality_v1", "Tool Use Quality"),
]


def _orchestrator_resource() -> str:
    if not ORCHESTRATOR_RESOURCE_FILE.exists():
        raise RuntimeError(
            f"{ORCHESTRATOR_RESOURCE_FILE} missing. Deploy the orchestrator "
            f"first (make enterprise-deploy)."
        )
    val = ORCHESTRATOR_RESOURCE_FILE.read_text().strip()
    if not val:
        raise RuntimeError(f"{ORCHESTRATOR_RESOURCE_FILE} is empty.")
    return val


def _access_token() -> str:
    creds, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )
    creds.refresh(google.auth.transport.requests.Request())
    return creds.token


def _api_base() -> str:
    return f"https://{LOCATION}-aiplatform.googleapis.com/v1beta1"


def _parent() -> str:
    return f"projects/{PROJECT_ID}/locations/{LOCATION}"


def _resource_name() -> str:
    return f"{_parent()}/onlineEvaluators/{MONITOR_ID}"


def _build_body(agent_resource: str) -> dict:
    return {
        # Setting `name` makes this a client-specified-ID create. Without
        # it, every run produces a new server-generated ID and idempotency
        # is lost. The discovery doc lists no `onlineEvaluatorId` query
        # param, so this body field is the only place the ID can travel.
        "name": _resource_name(),
        "displayName": "Customer Resolution Online Monitor",
        "agentResource": agent_resource,
        "config": {
            "randomSampling": {"percentage": SAMPLE_PERCENTAGE},
            "maxEvaluatedSamplesPerRun": str(MAX_PER_RUN),
        },
        "cloudObservability": {
            # Pinned to the minimum the API accepts. Bump as Cloud Trace
            # adopts newer semconv revisions in the future.
            "openTelemetry": {"semconvVersion": "1.39.0"},
            # The API requires one of cloudObservability's eval_scope
            # oneOf fields to be set. Empty traceScope = "all traces"
            # (no predicate filtering).
            "traceScope": {},
        },
        "metricSources": [
            {
                "metric": {
                    "predefinedMetricSpec": {"metricSpecName": metric_name},
                    "metadata": {
                        "title": title,
                        "scoreRange": {"min": 0, "max": 1},
                    },
                },
            }
            for metric_name, title in PREDEFINED_METRICS
        ],
    }


def _create(token: str, body: dict) -> tuple[int, dict]:
    """POST to create. Returns (status, json_body) without raising — caller
    decides whether ALREADY_EXISTS warrants a PATCH fallback."""
    url = f"{_api_base()}/{_parent()}/onlineEvaluators"
    resp = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        data=json.dumps(body),
        timeout=60,
    )
    try:
        body_json = resp.json()
    except Exception:
        body_json = {"raw": resp.text[:300]}
    return resp.status_code, body_json


def _patch(token: str, body: dict) -> dict:
    """Update tunable fields. agentResource is immutable so we leave it out of the mask."""
    url = f"{_api_base()}/{_resource_name()}"
    update_mask = "displayName,config,metricSources,cloudObservability"
    resp = requests.patch(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        params={"updateMask": update_mask},
        data=json.dumps(body),
        timeout=60,
    )
    if resp.status_code >= 300:
        raise RuntimeError(f"PATCH {url} → {resp.status_code}: {resp.text[:600]}")
    return resp.json()


def main() -> None:
    print(f"📡 Provisioning OnlineEvaluator '{MONITOR_ID}' in {PROJECT_ID} ({LOCATION})")
    print(f"   sampling: {SAMPLE_PERCENTAGE}% | max samples/run: {MAX_PER_RUN}")
    agent = _orchestrator_resource()
    print(f"   target agent: {agent}")

    token = _access_token()
    body = _build_body(agent)

    # Try CREATE first; on ALREADY_EXISTS fall back to PATCH. The GET-probe
    # path returns 500 INTERNAL for missing resources on this API, so going
    # create-first is more reliable.
    status, result = _create(token, body)
    if status >= 300:
        err_status = (result.get("error") or {}).get("status", "")
        if status == 409 or err_status == "ALREADY_EXISTS":
            print("   → evaluator exists, patching mutable fields")
            result = _patch(token, body)
        else:
            raise RuntimeError(
                f"CREATE failed → HTTP {status}: {json.dumps(result)[:600]}"
            )
    else:
        print("   → created new evaluator")

    print("\n✅ Online monitor ready.")
    print(f"   resource: {result.get('name', _resource_name())}")
    print(f"   state:    {result.get('state', 'unknown')}")
    print("\n   View at: Vertex AI Console → Agent Platform → Evaluation → Online monitors")


if __name__ == "__main__":
    main()
