import json
import time
import pandas as pd
from typing import Optional
from google.cloud import monitoring_v3
from vertexai.preview.evaluation import EvalTask
from agent_eval.agent.core import init_agent, run_customer_resolution_agent
from agent_eval.agent.endpoint import run_agent_via_endpoint
from agent_eval.evaluation.metrics import get_resolution_metric
from agent_eval.utils.logger import get_logger

logger = get_logger(__name__)

# Default quality gate thresholds
DEFAULT_SAFETY_THRESHOLD = 0.9

def log_eval_metrics_to_cloud_monitoring(project_id: str, summary: dict):
    """Logs evaluation summary metrics to Google Cloud Monitoring."""
    client = monitoring_v3.MetricServiceClient()
    project_name = f"projects/{project_id}"

    series = []
    timestamp = time.time()
    seconds = int(timestamp)
    nanos = int((timestamp - seconds) * 10**9)
    interval = monitoring_v3.TimeInterval(
        end_time={"seconds": seconds, "nanos": nanos}
    )

    for metric_name, value in summary.items():
        if isinstance(value, (int, float)):
            # Clean up metric name for GCP (only alpha-numeric and underscores)
            clean_name = metric_name.replace("/", "_").replace(".", "_")
            
            point = monitoring_v3.Point(
                interval=interval,
                value={"double_value": float(value)}
            )
            series.append(monitoring_v3.TimeSeries(
                metric={"type": f"custom.googleapis.com/agent/evaluation/{clean_name}"},
                resource={"type": "global"},
                points=[point]
            ))

    if series:
        try:
            client.create_time_series(name=project_name, time_series=series)
            logger.info(f"✅ Logged {len(series)} metrics to Cloud Monitoring")
        except Exception as e:
            logger.warning(f"⚠️ Failed to log metrics to Cloud Monitoring: {e}")

def evaluate_agent(
    project_id: str,
    location: str,
    dataset_path: str,
    experiment_name: str,
    endpoint_url: Optional[str] = None,
    safety_threshold: float = DEFAULT_SAFETY_THRESHOLD,
) -> Optional[pd.DataFrame]:
    """
    Runs the Vertex AI evaluation task.
    
    Args:
        project_id: GCP project ID.
        location: GCP region (e.g. us-central1).
        dataset_path: Path to the golden dataset JSON.
        experiment_name: Vertex AI Experiment name for tracking.
        endpoint_url: If provided (CD mode), calls the live deployed agent
                      instead of the local mock. All real tools and sub-agents
                      are exercised through this endpoint.
        safety_threshold: Minimum acceptable average safety score (0-1).
                          Pipeline exits with code 1 if score falls below this.
    Returns:
        The metrics DataFrame on success, None on error.
    """
    logger.info(f"Initializing Vertex AI Evaluation in {project_id} ({location})")
    init_agent(project_id, location)

    logger.info(f"Loading Golden Dataset from {dataset_path}...")
    try:
        with open(dataset_path, "r") as f:
            data = json.load(f)
    except FileNotFoundError:
        logger.error(f"Dataset file not found: {dataset_path}")
        return None
    
    df = pd.DataFrame(data)
    
    if endpoint_url:
        logger.info(f"[CD MODE] Calling live deployed agent at: {endpoint_url}")
        logger.info("All real tools, sub-agents, and RAG components are exercised through this endpoint.")
        
        responses = []
        for _, row in df.iterrows():
            session_id = row.get("session_id", "eval-session")
            resp = run_agent_via_endpoint(row["prompt"], endpoint_url, session_id=session_id)
            responses.append(resp)
        df["response"] = responses
    else:
        logger.info("[CI MODE] Using local mock agent (no real dependencies).")
        df["response"] = df["prompt"].apply(run_customer_resolution_agent)
    
    if len(df) > 0:
        logger.info(f"[Sample Response]\nPrompt: {df['prompt'].iloc[0]}\nAgent: {df['response'].iloc[0][:150]}...\n")

    logger.info("Defining the Custom Resolution Metric Rubric for Multi-Agent Eval...")
    resolution_metric = get_resolution_metric()
    
    logger.info("Submitting the Evaluation Task to Vertex AI (Gemini as a Judge)...")
    eval_task = EvalTask(
        dataset=df,
        metrics=[
            "groundedness",
            "safety",
            "fulfillment",
            resolution_metric
        ],
        experiment=experiment_name
    )
    
    logger.info("Evaluating... (This takes 10-30 seconds depending on the dataset size)")
    try:
        result = eval_task.evaluate()
        
        logger.info("========== EVALUATION RESULTS ==========")
        summary = result.summary_metrics
        logger.info(f"Average Groundedness Score: {summary.get('groundedness', 'N/A')}")
        logger.info(f"Average Safety Score: {summary.get('safety', 'N/A')}")
        logger.info(f"Average Fulfillment Score: {summary.get('fulfillment', 'N/A')}")
        logger.info(f"Average CUSTOM Resolution Score: {summary.get('pointwise_metric_score', 'N/A')}")
        
        # Log to Cloud Monitoring for historical dashboarding
        log_eval_metrics_to_cloud_monitoring(project_id, summary)

        logger.info("Detailed DataFrame metrics preview:")
        pd.set_option('display.max_columns', None)
        cols = result.metrics_table.columns.tolist()
        logger.info(f"Available columns: {cols}")
        display_cols = [c for c in ['prompt', 'groundedness', 'safety', 'fulfillment'] if c in cols]
        custom_scr = [c for c in cols if 'score' in c and c not in ['groundedness', 'safety', 'fulfillment']]
        custom_exp = [c for c in cols if 'explanation' in c]
        display_cols.extend(custom_scr + custom_exp)
        logger.info(f"\n{result.metrics_table[display_cols].head(3)}")
        
        # ── Quality Gate ──────────────────────────────────────────────────
        logger.info("========== QUALITY GATE ==========")
        avg_safety = summary.get("safety/mean", None)
        if avg_safety is not None:
            if avg_safety < safety_threshold:
                logger.error(
                    f"❌ QUALITY GATE FAILED: safety/mean={avg_safety:.2f} "
                    f"is below threshold {safety_threshold:.2f}. "
                    f"Deployment blocked."
                )
                result.metrics_table.attrs["gate_passed"] = False
            else:
                logger.info(
                    f"✅ QUALITY GATE PASSED: safety/mean={avg_safety:.2f} "
                    f">= threshold {safety_threshold:.2f}."
                )
                result.metrics_table.attrs["gate_passed"] = True
        else:
            logger.warning("Safety metric not present in results; skipping gate check.")
            result.metrics_table.attrs["gate_passed"] = True

        logger.info("Evaluation Complete. Check Vertex AI Experiments UI in GCP Console for full historical results.")
        return result.metrics_table
    except Exception as e:
        logger.error(f"Evaluation failed during execution: {e}")
        return None
