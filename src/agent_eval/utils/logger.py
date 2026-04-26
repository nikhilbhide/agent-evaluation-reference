import logging
import sys
import json
import os

def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(logging.INFO)
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    return logger

def log_enterprise_metrics(metrics: dict, step: str = "evaluation"):
    """
    Logs metrics in a structured format for Cloud Logging/Monitoring.
    Enterprise agents use these to trigger SCC alerts or Viz dashboards.
    """
    payload = {
        "severity": "INFO",
        "message": f"Agent {step} completed",
        "metrics": metrics,
        "agent_id": os.environ.get("AGENT_ID", "orchestrator"),
        "version": os.environ.get("AGENT_VERSION", "unknown"),
        "governance": {
            "model_armor_status": "active",
            "identity": "agent-orchestrator"
        }
    }
    # Using structured print for Cloud Logging to pick up as JSON
    print(json.dumps(payload))
