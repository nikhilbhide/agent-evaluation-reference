"""Pull together a single readout from BigQuery + Cloud Monitoring + on-disk
artifacts after a load test + red-team run.

Outputs JSON to build/walkthrough_<timestamp>.json and prints a
human-readable summary.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sys
from pathlib import Path

import google.auth
import google.auth.transport.requests
import requests
from google.cloud import bigquery, monitoring_v3

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents._shared.config import require

PROJECT_ID = require("GCP_PROJECT")
DATASET = os.environ.get("BQ_LOGS_DATASET", "agent_telemetry")
TABLE = os.environ.get("BQ_LOGS_TABLE", "agent_traces")

OUT = Path("build") / f"walkthrough_{dt.datetime.utcnow().strftime('%Y%m%dT%H%M%S')}.json"


def _bq_summary() -> dict:
    bq = bigquery.Client(project=PROJECT_ID)
    out: dict = {}

    out["per_source_24h"] = [dict(r) for r in bq.query(f"""
        SELECT source, COUNT(*) AS n,
               COUNT(DISTINCT run_id) AS runs
        FROM `{PROJECT_ID}.{DATASET}.{TABLE}`
        WHERE timestamp > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 24 HOUR)
        GROUP BY source ORDER BY n DESC
    """).result()]

    out["redteam_breakdown"] = [dict(r) for r in bq.query(f"""
        SELECT redteam_severity AS severity,
               redteam_pass AS pass_,
               COUNT(*) AS n
        FROM `{PROJECT_ID}.{DATASET}.{TABLE}`
        WHERE source = 'redteam'
          AND timestamp > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 24 HOUR)
        GROUP BY severity, pass_
        ORDER BY n DESC
    """).result()]

    out["loadtest_categories"] = [dict(r) for r in bq.query(f"""
        SELECT category, COUNT(*) AS n
        FROM `{PROJECT_ID}.{DATASET}.{TABLE}`
        WHERE source = 'load_test'
          AND timestamp > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 24 HOUR)
        GROUP BY category ORDER BY n DESC
    """).result()]

    return out


def _alert_policies() -> list[dict]:
    client = monitoring_v3.AlertPolicyServiceClient()
    out = []
    for p in client.list_alert_policies(name=f"projects/{PROJECT_ID}"):
        out.append({
            "name": p.display_name,
            "enabled": bool(p.enabled and p.enabled.value),
            "channels": list(p.notification_channels),
        })
    return out


def _dashboards() -> list[str]:
    creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    creds.refresh(google.auth.transport.requests.Request())
    r = requests.get(
        f"https://monitoring.googleapis.com/v1/projects/{PROJECT_ID}/dashboards",
        headers={"Authorization": f"Bearer {creds.token}"},
        timeout=15,
    )
    if r.status_code != 200:
        return [f"(list failed: {r.status_code} {r.text[:120]})"]
    return [d.get("displayName", d.get("name", "?")) for d in r.json().get("dashboards", [])]


def _latest_artifacts() -> dict:
    out: dict = {}
    load_runs = sorted(Path("build/load_runs").glob("*_summary.json")) if Path("build/load_runs").exists() else []
    if load_runs:
        out["latest_load_summary"] = json.loads(load_runs[-1].read_text())
    redteam_runs = sorted(Path("build/redteam_runs").glob("*.json")) if Path("build/redteam_runs").exists() else []
    if redteam_runs:
        rt = json.loads(redteam_runs[-1].read_text())
        out["latest_redteam"] = {
            "run_id": rt.get("run_id"),
            "endpoint": rt.get("endpoint"),
            "n_prompts": len(rt.get("prompts", [])),
            "n_failures": len(rt.get("failures", [])),
            "failures": [
                {"prompt": f.get("prompt", "")[:120],
                 "severity": f.get("severity"),
                 "reason": f.get("reason", "")[:160]}
                for f in rt.get("failures", [])
            ],
        }
    if Path("build/abom.json").exists():
        out["abom"] = json.loads(Path("build/abom.json").read_text())
    if Path("build/enterprise_metadata.json").exists():
        out["enterprise_metadata"] = json.loads(Path("build/enterprise_metadata.json").read_text())
    return out


def main() -> None:
    print(f"📊 Building walkthrough for {PROJECT_ID}")
    report = {
        "project": PROJECT_ID,
        "generated_at": dt.datetime.utcnow().isoformat() + "Z",
    }
    try:
        report["bigquery"] = _bq_summary()
    except Exception as exc:
        report["bigquery_error"] = str(exc)
    try:
        report["alert_policies"] = _alert_policies()
    except Exception as exc:
        report["alert_policies_error"] = str(exc)
    try:
        report["dashboards"] = _dashboards()
    except Exception as exc:
        report["dashboards_error"] = str(exc)
    report["artifacts"] = _latest_artifacts()

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2, default=str))
    print(f"\n✅ wrote {OUT}\n")
    print("─── HUMAN SUMMARY ───")
    for src in report.get("bigquery", {}).get("per_source_24h", []):
        print(f"  {src['source']:>10}: {src['n']} rows ({src['runs']} runs)")
    rt = report.get("artifacts", {}).get("latest_redteam") or {}
    if rt:
        print(f"  red-team   : {rt['n_prompts']} prompts, {rt['n_failures']} failures "
              f"(endpoint={rt['endpoint'][:60]}…)")
    ld = report.get("artifacts", {}).get("latest_load_summary") or {}
    if ld:
        print(f"  load-test  : {ld['succeeded']}/{ld['started']} ok, "
              f"p50={ld.get('p50_ms')}ms p95={ld.get('p95_ms')}ms p99={ld.get('p99_ms')}ms")
    print(f"  dashboards : {report.get('dashboards', [])}")
    print(f"  alerts     : {len(report.get('alert_policies', []))} policies enabled")


if __name__ == "__main__":
    main()
