"""
Endpoint-based agent client for CD (Continuous Deployment) validation.

In a CI run, the mock agent (core.py) is used — no real dependencies.
In a CD run, this module calls the REAL deployed agent endpoint
(e.g. Cloud Run, Vertex AI Reasoning Engine) so all wired components
(tools, RAG, sub-agents) are exercised in their actual state before
traffic is activated.
"""
import requests
from agent_eval.utils.logger import get_logger

logger = get_logger(__name__)


def run_agent_via_endpoint(prompt: str, endpoint_url: str, timeout: int = 30) -> str:
    """
    Calls a live deployed agent endpoint and returns its response text.
    
    Expected request format  (adjust to match your agent's API schema):
        POST {endpoint_url}
        {"prompt": "<user message>"}
    
    Expected response format:
        {"response": "<agent reply>"}
    
    Args:
        prompt: The user query to send.
        endpoint_url: Full URL of the deployed agent (staging or canary).
        timeout: Request timeout in seconds.
    
    Returns:
        The agent's text response, or an error string if the call fails.
    """
    predict_url = f"{endpoint_url.rstrip('/')}/predict"
    logger.info(f"Calling live agent endpoint: {predict_url}")
    try:
        resp = requests.post(
            predict_url,
            json={"prompt": prompt},
            timeout=timeout,
            headers={"Content-Type": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("response", str(data))
    except requests.exceptions.Timeout:
        msg = f"Endpoint timeout after {timeout}s for prompt: '{prompt[:60]}'"
        logger.error(msg)
        return f"Agent Error: {msg}"
    except requests.exceptions.HTTPError as e:
        msg = f"HTTP {e.response.status_code} from endpoint: {e}"
        logger.error(msg)
        return f"Agent Error: {msg}"
    except Exception as e:
        logger.error(f"Endpoint call failed: {e}")
        return f"Agent Error: {str(e)}"
