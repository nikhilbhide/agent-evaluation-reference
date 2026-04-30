"""
Optimize phase — pull weak responses from BigQuery telemetry, build an SFT
dataset, upload to GCS, and (optionally) submit a Vertex AI Supervised
Fine-Tuning job.

Set TUNING_DRY_RUN=0 to actually submit the SFT job. Default is dry-run so
the optimize loop can be exercised in CI without burning training credits.

Required env:
  GCP_PROJECT
Optional:
  GCP_LOCATION         (default us-central1)
  BQ_LOGS_DATASET      (default agent_telemetry)
  BQ_LOGS_TABLE        (default agent_traces)
  GCP_STAGING_BUCKET   (default gs://agent-eval-staging-<project>)
  TUNING_DRY_RUN       (default "1" → don't submit; "0" → submit)
  TUNING_SOURCE_MODEL  (default gemini-2.5-flash)
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sys
from pathlib import Path

import vertexai
from google.cloud import bigquery, storage

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agents._shared.config import staging_bucket  # noqa: E402

PROJECT_ID = os.environ.get("GCP_PROJECT")
LOCATION = os.environ.get("GCP_LOCATION", "us-central1")
DATASET_ID = os.environ.get("BQ_LOGS_DATASET", "agent_telemetry")
TABLE_ID = os.environ.get("BQ_LOGS_TABLE", "agent_traces")
DRY_RUN = os.environ.get("TUNING_DRY_RUN", "1") != "0"
SOURCE_MODEL = os.environ.get("TUNING_SOURCE_MODEL", "gemini-2.5-flash")

if not PROJECT_ID:
    print("❌ GCP_PROJECT must be set.")
    sys.exit(1)

STAGING_BUCKET = staging_bucket(PROJECT_ID)


def _query_bq() -> list[dict]:
    """Pull failed-or-low-quality turns from the last 30 days."""
    client = bigquery.Client(project=PROJECT_ID)
    query = f"""
    SELECT
      prompt        AS input_text,
      reference     AS output_text,
      safety_score,
      groundedness_score,
      fulfillment_score,
      redteam_pass,
      redteam_severity
    FROM `{PROJECT_ID}.{DATASET_ID}.{TABLE_ID}`
    WHERE timestamp > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY)
      AND prompt IS NOT NULL
      AND reference IS NOT NULL
      AND (
        (fulfillment_score IS NOT NULL AND fulfillment_score < 0.8)
        OR (safety_score IS NOT NULL AND safety_score < 0.9)
        OR (groundedness_score IS NOT NULL AND groundedness_score < 0.85)
        OR (redteam_pass = FALSE)
      )
    LIMIT 500
    """
    return [dict(r) for r in client.query(query).result()]


def _format_jsonl(rows: list[dict], path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with path.open("w") as f:
        for r in rows:
            inp = (r.get("input_text") or "").strip()
            out = (r.get("output_text") or "").strip()
            if not inp or not out:
                continue
            f.write(json.dumps({"input_text": inp, "output_text": out}) + "\n")
            written += 1
    return written


def _upload_to_gcs(local_path: Path, gcs_uri: str) -> str:
    assert gcs_uri.startswith("gs://")
    bucket_name, _, blob_path = gcs_uri[5:].partition("/")
    client = storage.Client(project=PROJECT_ID)
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(blob_path)
    blob.upload_from_filename(str(local_path))
    return gcs_uri


def main() -> None:
    print("=====================================================")
    print(" 🔄  Optimize phase — automated tuning loop")
    print(f" Project   : {PROJECT_ID}")
    print(f" BQ source : {PROJECT_ID}.{DATASET_ID}.{TABLE_ID}")
    print(f" Dry-run   : {DRY_RUN}")
    print("=====================================================\n")

    print("[1/4] 🔍 Querying BigQuery for low-quality turns...")
    try:
        rows = _query_bq()
    except Exception as exc:
        print(f"❌ BigQuery query failed: {exc}")
        print("   Run scripts/setup_telemetry_sink.py and ensure runner.py / "
              "run_simulation.py have written some rows.")
        sys.exit(1)
    print(f"   ↳ {len(rows)} candidate examples found")

    if not rows:
        print("ℹ️ No examples meet improvement criteria. Nothing to tune.")
        return

    print("\n[2/4] 📄 Building SFT JSONL...")
    local_path = Path("build/tuning_dataset.jsonl")
    written = _format_jsonl(rows, local_path)
    print(f"   ↳ wrote {written} rows to {local_path}")
    if written == 0:
        print("ℹ️ All rows missing input/output. Skipping.")
        return

    print("\n[3/4] ☁️  Uploading to GCS...")
    gcs_uri = f"{STAGING_BUCKET}/tuning/{dt.date.today().isoformat()}_{written}.jsonl"
    try:
        _upload_to_gcs(local_path, gcs_uri)
        print(f"   ↳ {gcs_uri}")
    except Exception as exc:
        print(f"❌ GCS upload failed: {exc}")
        sys.exit(1)

    print(f"\n[4/4] 🚀 Submitting Vertex AI SFT job (dry_run={DRY_RUN})...")
    vertexai.init(project=PROJECT_ID, location=LOCATION, staging_bucket=STAGING_BUCKET)

    if DRY_RUN:
        print("   ↳ DRY RUN: would call vertexai.tuning.sft.train(")
        print(f"            source_model={SOURCE_MODEL!r},")
        print(f"            train_dataset={gcs_uri!r},")
        print(f"            tuned_model_display_name='customer-orchestrator-tuned-"
              f"{dt.date.today().strftime('%Y%m%d')}')")
        print("   ↳ Set TUNING_DRY_RUN=0 to actually submit the job.")
        return

    from vertexai.tuning import sft  # imported lazily so dry-run works without it
    job = sft.train(
        source_model=SOURCE_MODEL,
        train_dataset=gcs_uri,
        tuned_model_display_name=(
            f"customer-orchestrator-tuned-{dt.date.today().strftime('%Y%m%d')}"
        ),
        epochs=3,
        learning_rate_multiplier=1.0,
    )
    print(f"   ✅ Tuning job submitted: {job.resource_name}")
    print("   Track progress in Vertex AI Console → Tuning.")


if __name__ == "__main__":
    main()
