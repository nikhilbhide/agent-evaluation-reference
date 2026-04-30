"""Per-turn writer for the BigQuery telemetry table.

Both the eval runner (runner.py) and the red-team simulator
(scripts/run_simulation.py) call this. The table is provisioned by
`scripts/setup_telemetry_sink.py`; if it doesn't exist, write_rows() logs
a warning and returns without raising — telemetry must never block the
caller.
"""

from __future__ import annotations

import datetime as dt
import logging
import os
from typing import Any, Iterable

logger = logging.getLogger(__name__)

DATASET_ID = os.environ.get("BQ_LOGS_DATASET", "agent_telemetry")
TABLE_ID = os.environ.get("BQ_LOGS_TABLE", "agent_traces")


def _client(project_id: str):
    from google.cloud import bigquery
    return bigquery.Client(project=project_id)


def _now_iso() -> str:
    return dt.datetime.utcnow().replace(tzinfo=dt.timezone.utc).isoformat()


def write_rows(project_id: str, rows: Iterable[dict[str, Any]]) -> None:
    rows = [r for r in rows if r]
    if not rows:
        return
    table_ref = f"{project_id}.{DATASET_ID}.{TABLE_ID}"
    try:
        client = _client(project_id)
        normalized = []
        for r in rows:
            r = {**r}
            r.setdefault("timestamp", _now_iso())
            normalized.append(r)
        errors = client.insert_rows_json(table_ref, normalized)
        if errors:
            logger.warning("BigQuery insert had errors: %s", errors[:3])
        else:
            logger.info("📨 wrote %d telemetry row(s) to %s", len(normalized), table_ref)
    except Exception as exc:
        logger.warning("BigQuery telemetry skipped (%s). "
                       "Run scripts/setup_telemetry_sink.py to provision the dataset.", exc)


def eval_row(
    *,
    run_id: str,
    experiment: str,
    engine_id: str | None,
    prompt: str,
    response: str,
    reference: str | None,
    metrics: dict[str, Any],
    expected_route: str | None = None,
    category: str | None = None,
    session_id: str | None = None,
    user_id: str | None = None,
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "source": "eval",
        "experiment": experiment,
        "reasoning_engine_id": engine_id,
        "session_id": session_id,
        "user_id": user_id,
        "prompt": prompt,
        "response": response,
        "reference": reference,
        "safety_score": metrics.get("safety/score"),
        "groundedness_score": metrics.get("groundedness/score"),
        "fulfillment_score": metrics.get("fulfillment/score"),
        "custom_score": metrics.get("pointwise_metric_score"),
        "expected_route": expected_route,
        "category": category,
    }


def redteam_row(
    *,
    run_id: str,
    prompt: str,
    response: str,
    pass_: bool,
    severity: str,
    reason: str,
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "source": "redteam",
        "prompt": prompt,
        "response": response,
        "reference": reason,
        "redteam_pass": pass_,
        "redteam_severity": severity,
        "category": "security",
    }
