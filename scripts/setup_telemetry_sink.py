"""
Provision the BigQuery telemetry sink that powers the optimize loop.

Creates:
  - dataset: agent_telemetry
  - table:   agent_telemetry.agent_traces
              (one row per evaluated turn — prompt, response, scores, source)
  - table:   agent_telemetry.human_annotations
              (append-only HITL queue; latest-per-id wins)
  - log sink: agent_mcp_to_bq routes MCP tool-call logs into
              agent_telemetry.mcp_tool_calls (auto-created by the sink).

Run once per project, idempotent.
"""

from __future__ import annotations

import os
import sys
from typing import Any

from google.api_core.exceptions import AlreadyExists, Conflict, NotFound
from google.cloud import bigquery, logging_v2

PROJECT_ID = os.environ.get("GCP_PROJECT")
LOCATION = os.environ.get("GCP_LOCATION", "us-central1")
DATASET_ID = os.environ.get("BQ_LOGS_DATASET", "agent_telemetry")
TRACES_TABLE = os.environ.get("BQ_LOGS_TABLE", "agent_traces")
ANNOTATIONS_TABLE = os.environ.get("BQ_ANNOTATIONS_TABLE", "human_annotations")
SINK_NAME = os.environ.get("BQ_LOG_SINK_NAME", "agent_mcp_to_bq")


TRACE_SCHEMA = [
    bigquery.SchemaField("timestamp", "TIMESTAMP", mode="REQUIRED"),
    bigquery.SchemaField("run_id", "STRING"),
    bigquery.SchemaField("source", "STRING", description="eval | redteam | shadow"),
    bigquery.SchemaField("experiment", "STRING"),
    bigquery.SchemaField("reasoning_engine_id", "STRING"),
    bigquery.SchemaField("session_id", "STRING"),
    bigquery.SchemaField("user_id", "STRING"),
    bigquery.SchemaField("prompt", "STRING"),
    bigquery.SchemaField("response", "STRING"),
    bigquery.SchemaField("reference", "STRING"),
    bigquery.SchemaField("safety_score", "FLOAT"),
    bigquery.SchemaField("groundedness_score", "FLOAT"),
    bigquery.SchemaField("fulfillment_score", "FLOAT"),
    bigquery.SchemaField("instruction_following_score", "FLOAT"),
    bigquery.SchemaField("question_answering_quality_score", "FLOAT"),
    bigquery.SchemaField("text_quality_score", "FLOAT"),
    bigquery.SchemaField("custom_score", "FLOAT"),
    bigquery.SchemaField("programmatic_pass", "BOOL"),
    bigquery.SchemaField("programmatic_score", "FLOAT"),
    bigquery.SchemaField("redteam_pass", "BOOL"),
    bigquery.SchemaField("redteam_severity", "STRING"),
    bigquery.SchemaField("expected_route", "STRING"),
    bigquery.SchemaField("category", "STRING"),
]


ANNOTATION_SCHEMA = [
    bigquery.SchemaField("created_at", "TIMESTAMP", mode="REQUIRED"),
    bigquery.SchemaField("annotation_id", "STRING", mode="REQUIRED",
                         description="sha1(run_id+prompt+response)[:16]"),
    bigquery.SchemaField("status", "STRING",
                         description="pending | labeled"),
    bigquery.SchemaField("run_id", "STRING"),
    bigquery.SchemaField("experiment", "STRING"),
    bigquery.SchemaField("source", "STRING"),
    bigquery.SchemaField("prompt", "STRING"),
    bigquery.SchemaField("response", "STRING"),
    bigquery.SchemaField("expected_route", "STRING"),
    bigquery.SchemaField("category", "STRING"),
    bigquery.SchemaField("code_pass", "BOOL"),
    bigquery.SchemaField("code_score", "FLOAT"),
    bigquery.SchemaField("judge_safety", "FLOAT"),
    bigquery.SchemaField("judge_custom", "FLOAT"),
    bigquery.SchemaField("disagreement_reason", "STRING",
                         description="judge_pass_code_fail | judge_fail_code_pass | judge_borderline"),
    bigquery.SchemaField("human_verdict", "STRING",
                         description="good | bad | partial (skip rows aren't written)"),
    bigquery.SchemaField("human_note", "STRING"),
    bigquery.SchemaField("labeler", "STRING"),
    bigquery.SchemaField("labeled_at", "TIMESTAMP"),
]


def ensure_dataset(client: bigquery.Client) -> str:
    dataset_ref = bigquery.Dataset(f"{PROJECT_ID}.{DATASET_ID}")
    dataset_ref.location = LOCATION
    try:
        client.create_dataset(dataset_ref)
        print(f"   ✅ created dataset {DATASET_ID}")
    except (Conflict, AlreadyExists):
        print(f"   ✅ dataset {DATASET_ID} already exists")
    return f"{PROJECT_ID}.{DATASET_ID}"


def ensure_table(
    client: bigquery.Client,
    dataset_fqn: str,
    table_id: str,
    schema: list,
    partition_field: str,
) -> str:
    table_fqn = f"{dataset_fqn}.{table_id}"
    try:
        existing = client.get_table(table_fqn)
        print(f"   ✅ table {table_id} already exists ({len(existing.schema)} cols)")
        # Idempotent schema patch — adds missing fields (BQ permits adding only).
        existing_fields = {f.name for f in existing.schema}
        new_fields = [f for f in schema if f.name not in existing_fields]
        if new_fields:
            updated_schema = list(existing.schema) + new_fields
            existing.schema = updated_schema
            client.update_table(existing, ["schema"])
            print(f"   ✅ added {len(new_fields)} new fields to {table_id}")
    except NotFound:
        table = bigquery.Table(table_fqn, schema=schema)
        table.time_partitioning = bigquery.TimePartitioning(field=partition_field)
        client.create_table(table)
        print(f"   ✅ created table {table_id} (partitioned on {partition_field})")
    return table_fqn


def ensure_log_sink() -> None:
    """Route MCP tool-call logs into BigQuery so we have a per-call audit trail."""
    from google.cloud.logging_v2.services.config_service_v2 import ConfigServiceV2Client
    from google.cloud.logging_v2.types import (
        BigQueryOptions,
        CreateSinkRequest,
        LogSink,
        UpdateSinkRequest,
    )
    sink_client = ConfigServiceV2Client()
    parent = f"projects/{PROJECT_ID}"
    destination = f"bigquery.googleapis.com/projects/{PROJECT_ID}/datasets/{DATASET_ID}"
    sink = LogSink(
        name=SINK_NAME,
        destination=destination,
        filter=(
            'resource.type="cloud_run_revision" '
            'AND resource.labels.service_name="mcp-server" '
            'AND textPayload=~"principal=.* tool=.* status="'
        ),
        bigquery_options=BigQueryOptions(use_partitioned_tables=True),
    )

    try:
        created = sink_client.create_sink(
            request=CreateSinkRequest(parent=parent, sink=sink, unique_writer_identity=True)
        )
        print(f"   ✅ created log sink {SINK_NAME}")
        print(f"      grant BQ Data Editor to: {created.writer_identity}")
        print(f"      gcloud projects add-iam-policy-binding {PROJECT_ID} \\")
        print(f"        --member='{created.writer_identity}' \\")
        print(f"        --role='roles/bigquery.dataEditor'")
    except (AlreadyExists, Conflict):
        sink.name = f"{parent}/sinks/{SINK_NAME}"
        sink_client.update_sink(
            request=UpdateSinkRequest(
                sink_name=sink.name,
                sink=sink,
                unique_writer_identity=True,
            )
        )
        print(f"   ✅ updated log sink {SINK_NAME}")


def main() -> None:
    if not PROJECT_ID:
        print("❌ GCP_PROJECT must be set.")
        sys.exit(1)

    print(f"📡 Provisioning telemetry sink in {PROJECT_ID} ({LOCATION})")

    print("\n[1/4] BigQuery dataset")
    bq_client = bigquery.Client(project=PROJECT_ID, location=LOCATION)
    dataset_fqn = ensure_dataset(bq_client)

    print("\n[2/4] BigQuery trace table")
    ensure_table(bq_client, dataset_fqn, TRACES_TABLE, TRACE_SCHEMA, "timestamp")

    print("\n[3/4] BigQuery human-annotations table (HITL plane)")
    ensure_table(bq_client, dataset_fqn, ANNOTATIONS_TABLE, ANNOTATION_SCHEMA, "created_at")

    print("\n[4/4] Cloud Logging → BigQuery sink (MCP tool calls)")
    ensure_log_sink()

    print("\n✅ Telemetry sink ready. runner.py and run_simulation.py will write here.")
    print(f"   Query example:")
    print(f"   SELECT prompt, response, safety_score FROM `{PROJECT_ID}.{DATASET_ID}.{TRACES_TABLE}`")
    print(f"   WHERE timestamp > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 1 DAY) LIMIT 50;")


if __name__ == "__main__":
    main()
