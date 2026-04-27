import os
import sys
from google.cloud import monitoring_v3

def create_alert_policy(project_id: str):
    """
    Creates a Cloud Monitoring Alerting Policy for Agent Evaluation failures.
    """
    client = monitoring_v3.AlertPolicyServiceClient()
    project_name = f"projects/{project_id}"

    # Define the alerting policy for Safety Score
    # This alerts if the average safety score drops below 0.9
    alert_policy = {
        "display_name": "Agent Evaluation Safety Quality Gate",
        "combiner": "OR",
        "conditions": [
            {
                "display_name": "Safety Score < 0.9",
                "condition_threshold": {
                    "filter": 'metric.type="custom.googleapis.com/agent/evaluation/safety_mean" AND resource.type="global"',
                    "duration": {"seconds": 0},
                    "comparison": "COMPARISON_LT",
                    "threshold_value": 0.9,
                    "trigger": {"count": 1},
                    "aggregations": [
                        {
                            "alignment_period": {"seconds": 60},
                            "per_series_aligner": "ALIGN_MEAN",
                        }
                    ],
                },
            }
        ],
        "notification_channels": [], # In production, link to Slack/Email channels
        "enabled": True,
        "documentation": {
            "content": "The Agent Evaluation Quality Gate has failed. Safety scores are below the 0.9 threshold. Deployment is blocked and immediate investigation is required.",
            "mime_type": "text/markdown",
        },
    }

    try:
        policy = client.create_alert_policy(name=project_name, alert_policy=alert_policy)
        print(f"✅ Alert Policy created: {policy.name}")
    except Exception as e:
        print(f"❌ Failed to create alert policy: {e}")

if __name__ == "__main__":
    project = os.environ.get("GCP_PROJECT")
    if not project:
        print("❌ GCP_PROJECT environment variable must be set.")
        sys.exit(1)
    
    print(f"🔔 Setting up enterprise alerting for project: {project}")
    create_alert_policy(project)
