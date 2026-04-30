"""
Provision Cloud Monitoring channels, log-based metrics, and alert policies
for the agentic platform.

What this creates:
  1. Notification channels — email (one per ALERT_EMAILS entry) and an
     optional PubSub channel (downstream Slack/PagerDuty fan-out).
  2. Log-based metrics — `agent/mcp/tool_call_count` from MCP server logs,
     with labels {tool, status, principal} so we can break it down by
     calling agent and tool.
  3. Alert policies:
       - Safety quality gate (safety_mean < 0.9)
       - Groundedness regression (groundedness_mean < 0.85)
       - Tool error rate > 5% over 5 min
"""

from __future__ import annotations

import os
import sys
from typing import Iterable

from google.api_core.exceptions import AlreadyExists
from google.cloud import logging_v2, monitoring_v3
from google.cloud.monitoring_v3 import (
    AlertPolicy,
    NotificationChannel,
    NotificationChannelServiceClient,
)

PROJECT_ID = os.environ.get("GCP_PROJECT")
ALERT_EMAILS = [e.strip() for e in os.environ.get("ALERT_EMAILS", "").split(",") if e.strip()]
ALERT_PUBSUB_TOPIC = os.environ.get("ALERT_PUBSUB_TOPIC")  # e.g. projects/P/topics/alerts


def _project_path() -> str:
    return f"projects/{PROJECT_ID}"


def ensure_email_channels(client: NotificationChannelServiceClient) -> list[str]:
    """Create one email NotificationChannel per ALERT_EMAILS entry. Returns channel names."""
    if not ALERT_EMAILS:
        print("ℹ️  ALERT_EMAILS not set; skipping email channels.")
        return []

    existing = {c.labels.get("email_address"): c.name
                for c in client.list_notification_channels(name=_project_path())
                if c.type_ == "email"}

    names: list[str] = []
    for email in ALERT_EMAILS:
        if email in existing:
            print(f"   ✅ email channel already exists for {email}")
            names.append(existing[email])
            continue
        channel = NotificationChannel(
            type_="email",
            display_name=f"Agent platform alerts ({email})",
            labels={"email_address": email},
            enabled=True,
        )
        created = client.create_notification_channel(name=_project_path(), notification_channel=channel)
        print(f"   ✅ created email channel for {email}")
        names.append(created.name)
    return names


def ensure_pubsub_channel(client: NotificationChannelServiceClient) -> list[str]:
    if not ALERT_PUBSUB_TOPIC:
        return []
    for c in client.list_notification_channels(name=_project_path()):
        if c.type_ == "pubsub" and c.labels.get("topic") == ALERT_PUBSUB_TOPIC:
            print(f"   ✅ pubsub channel already exists ({ALERT_PUBSUB_TOPIC})")
            return [c.name]
    channel = NotificationChannel(
        type_="pubsub",
        display_name="Agent platform alerts (pubsub)",
        labels={"topic": ALERT_PUBSUB_TOPIC},
        enabled=True,
    )
    created = client.create_notification_channel(name=_project_path(), notification_channel=channel)
    print(f"   ✅ created pubsub channel for {ALERT_PUBSUB_TOPIC}")
    return [created.name]


def ensure_tool_call_log_metric() -> None:
    """Distill MCP tool-call results into a counter with per-tool/per-status labels."""
    from google.api import label_pb2 as ga_label
    from google.api import metric_pb2 as ga_metric
    from google.cloud.logging_v2.services.metrics_service_v2 import MetricsServiceV2Client
    from google.cloud.logging_v2.types import LogMetric

    metric_name = "agent_mcp_tool_call_count"
    full_name = f"{_project_path()}/metrics/{metric_name}"

    descriptor = ga_metric.MetricDescriptor(
        metric_kind=ga_metric.MetricDescriptor.MetricKind.DELTA,
        value_type=ga_metric.MetricDescriptor.ValueType.INT64,
        unit="1",
        labels=[
            ga_label.LabelDescriptor(key="tool", value_type=ga_label.LabelDescriptor.ValueType.STRING),
            ga_label.LabelDescriptor(key="status", value_type=ga_label.LabelDescriptor.ValueType.STRING),
            ga_label.LabelDescriptor(key="principal", value_type=ga_label.LabelDescriptor.ValueType.STRING),
        ],
    )
    metric = LogMetric(
        name=metric_name,
        description="Counts of MCP tool invocations, labeled by tool, status, and calling principal.",
        filter=(
            'resource.type="cloud_run_revision" '
            'AND resource.labels.service_name="mcp-server" '
            'AND textPayload=~"principal=.* tool=.* status=(ok|error|bad_args)"'
        ),
        metric_descriptor=descriptor,
        label_extractors={
            "tool": 'REGEXP_EXTRACT(textPayload, "tool=([^ ]+)")',
            "status": 'REGEXP_EXTRACT(textPayload, "status=([a-z_]+)")',
            "principal": 'REGEXP_EXTRACT(textPayload, "principal=([^ ]+)")',
        },
    )

    client = MetricsServiceV2Client()
    parent = _project_path()
    try:
        client.create_log_metric(parent=parent, metric=metric)
        print(f"   ✅ created log-based metric {metric_name}")
    except AlreadyExists:
        client.update_log_metric(metric_name=full_name, metric=metric)
        print(f"   ✅ updated log-based metric {metric_name}")


def _safety_alert(channels: Iterable[str]) -> AlertPolicy:
    return AlertPolicy(
        display_name="Agent · Safety Quality Gate",
        combiner=AlertPolicy.ConditionCombinerType.OR,
        conditions=[AlertPolicy.Condition(
            display_name="safety/mean < 0.9",
            condition_threshold=AlertPolicy.Condition.MetricThreshold(
                filter=('metric.type="custom.googleapis.com/agent/evaluation/safety_mean" '
                        'AND resource.type="global"'),
                comparison=monitoring_v3.ComparisonType.COMPARISON_LT,
                threshold_value=0.9,
                duration={"seconds": 0},
                aggregations=[monitoring_v3.Aggregation(
                    alignment_period={"seconds": 300},
                    per_series_aligner=monitoring_v3.Aggregation.Aligner.ALIGN_MEAN,
                )],
            ),
        )],
        notification_channels=list(channels),
        documentation=AlertPolicy.Documentation(
            content="Safety mean fell below 0.9 — automated quality gate failed. "
                    "Block promotion and investigate the most recent eval run.",
            mime_type="text/markdown",
        ),
        enabled={"value": True},
    )


def _groundedness_alert(channels: Iterable[str]) -> AlertPolicy:
    return AlertPolicy(
        display_name="Agent · Groundedness Regression",
        combiner=AlertPolicy.ConditionCombinerType.OR,
        conditions=[AlertPolicy.Condition(
            display_name="groundedness/mean < 0.85",
            condition_threshold=AlertPolicy.Condition.MetricThreshold(
                filter=('metric.type="custom.googleapis.com/agent/evaluation/groundedness_mean" '
                        'AND resource.type="global"'),
                comparison=monitoring_v3.ComparisonType.COMPARISON_LT,
                threshold_value=0.85,
                duration={"seconds": 0},
                aggregations=[monitoring_v3.Aggregation(
                    alignment_period={"seconds": 300},
                    per_series_aligner=monitoring_v3.Aggregation.Aligner.ALIGN_MEAN,
                )],
            ),
        )],
        notification_channels=list(channels),
        documentation=AlertPolicy.Documentation(
            content="Groundedness regression — agent is hallucinating more than baseline.",
            mime_type="text/markdown",
        ),
        enabled={"value": True},
    )


def _tool_error_rate_alert(channels: Iterable[str]) -> AlertPolicy:
    """Fires when error/bad_args calls exceed 5% of total over a 5-minute window."""
    base = ('metric.type="logging.googleapis.com/user/agent_mcp_tool_call_count" '
            'AND resource.type="cloud_run_revision"')
    return AlertPolicy(
        display_name="Agent · MCP Tool Error Rate",
        combiner=AlertPolicy.ConditionCombinerType.OR,
        conditions=[AlertPolicy.Condition(
            display_name="error rate > 5% over 5 min",
            condition_monitoring_query_language=AlertPolicy.Condition.MonitoringQueryLanguageCondition(
                query=(
                    "fetch cloud_run_revision "
                    "| metric 'logging.googleapis.com/user/agent_mcp_tool_call_count' "
                    "| align rate(5m) "
                    "| group_by [metric.tool], "
                    "[error_rate: sum(if(metric.status != 'ok', val(), 0.0)) / sum(val())] "
                    "| condition val() > 0.05"
                ),
                duration={"seconds": 300},
                trigger=AlertPolicy.Condition.Trigger(count=1),
            ),
        )],
        notification_channels=list(channels),
        documentation=AlertPolicy.Documentation(
            content="MCP tool error rate exceeded 5%. Check whether a tool dependency "
                    "is down or whether an agent is hitting an authorization wall.",
            mime_type="text/markdown",
        ),
        enabled={"value": True},
    )


def upsert_alert(client: monitoring_v3.AlertPolicyServiceClient, policy: AlertPolicy) -> None:
    existing = next(
        (p for p in client.list_alert_policies(name=_project_path())
         if p.display_name == policy.display_name),
        None,
    )
    if existing:
        policy.name = existing.name
        client.update_alert_policy(alert_policy=policy)
        print(f"   ✅ updated alert policy: {policy.display_name}")
    else:
        client.create_alert_policy(name=_project_path(), alert_policy=policy)
        print(f"   ✅ created alert policy: {policy.display_name}")


def main() -> None:
    if not PROJECT_ID:
        print("❌ GCP_PROJECT environment variable must be set.")
        sys.exit(1)

    print(f"🔔 Provisioning monitoring for {PROJECT_ID}")

    print("\n[1/3] notification channels")
    ch_client = NotificationChannelServiceClient()
    channels = ensure_email_channels(ch_client) + ensure_pubsub_channel(ch_client)
    if not channels:
        print("⚠️  No notification channels configured — alerts will fire silently. "
              "Set ALERT_EMAILS=you@example.com (and/or ALERT_PUBSUB_TOPIC=projects/.../topics/...) "
              "and re-run.")

    print("\n[2/3] log-based metrics")
    ensure_tool_call_log_metric()

    print("\n[3/3] alert policies")
    alert_client = monitoring_v3.AlertPolicyServiceClient()
    for builder in (_safety_alert, _groundedness_alert, _tool_error_rate_alert):
        upsert_alert(alert_client, builder(channels))

    print("\n✅ Monitoring provisioning complete.")


if __name__ == "__main__":
    main()
