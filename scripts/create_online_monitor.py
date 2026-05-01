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
# The schema documents the ``_v1`` versioned form; aligning these with
# ``BUILTIN_METRICS`` from offline eval keeps online + offline scores
# directly comparable in dashboards.
PREDEFINED_METRICS: list[tuple[str, str]] = [
    ("safety_v1", "Safety"),
    ("groundedness_v1", "Groundedness"),
    ("instruction_following_v1", "Instruction Following"),
    ("question_answering_quality_v1", "Question Answering Quality"),
    ("text_quality_v1", "Text Quality"),
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


def _get_existing(token: str) -> dict | None:
    url = f"{_api_base()}/{_resource_name()}"
    resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=30)
    if resp.status_code == 200:
        return resp.json()
    if resp.status_code == 404:
        return None
    raise RuntimeError(f"GET {url} → {resp.status_code}: {resp.text[:300]}")


def _create(token: str, body: dict) -> dict:
    url = f"{_api_base()}/{_parent()}/onlineEvaluators"
    resp = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        params={"onlineEvaluatorId": MONITOR_ID},
        data=json.dumps(body),
        timeout=60,
    )
    if resp.status_code >= 300:
        raise RuntimeError(f"CREATE {url} → {resp.status_code}: {resp.text[:600]}")
    return resp.json()


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

    existing = _get_existing(token)
    if existing is None:
        print("   → creating new evaluator")
        result = _create(token, body)
    else:
        print("   → evaluator exists, patching mutable fields")
        result = _patch(token, body)

    print("\n✅ Online monitor ready.")
    print(f"   resource: {result.get('name', _resource_name())}")
    print(f"   state:    {result.get('state', 'unknown')}")
    print("\n   View at: Vertex AI Console → Agent Platform → Evaluation → Online monitors")


if __name__ == "__main__":
    main()
