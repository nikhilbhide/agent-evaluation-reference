import os
import google.auth
from agent_eval.utils.logger import get_logger

logger = get_logger(__name__)

def get_gcp_project() -> str:
    """Resolves the GCP project ID from application default credentials or environment."""
    try:
        _, project = google.auth.default()
        if project:
            return project
    except Exception as e:
        logger.debug(f"Could not get project from default creds: {e}")


    project = os.environ.get("GOOGLE_CLOUD_PROJECT")
    if project:
        return project
        
    raise ValueError("GCP Project ID not found in environment and no application default credentials with project found. Please run 'gcloud auth application-default login' and set your project.")
