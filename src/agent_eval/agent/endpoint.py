"""
Endpoint-based agent client for CD (Continuous Deployment) validation.

In a CI run, the mock agent (core.py) is used — no real dependencies.
In a CD run, this module calls the REAL deployed agent endpoint
(e.g. Cloud Run, Vertex AI Reasoning Engine) so all wired components
(tools, RAG, sub-agents) are exercised in their actual state before
traffic is activated.
"""
import requests
import os
from vertexai.preview import reasoning_engines
from agent_eval.utils.logger import get_logger

logger = get_logger(__name__)


def run_agent_via_endpoint(prompt: str, endpoint_url: str, session_id: str = "default-session", timeout: int = 30) -> str:
    """
    Calls a live deployed agent endpoint and returns its response text.
    Supports both REST URLs and Vertex AI Reasoning Engine resource names.
    """
    
    # Check if it's a Reasoning Engine resource name → use GA agent_engines API.
    if endpoint_url.startswith("projects/"):
        logger.info(f"Calling Reasoning Engine: {endpoint_url} (session={session_id})")
        try:
            from vertexai import agent_engines
            engine = agent_engines.get(endpoint_url)
            user_id = "eval-user"
            chunks = []
            for ev in engine.stream_query(message=prompt, user_id=user_id):
                content = ev.get("content") if isinstance(ev, dict) else None
                if not content:
                    continue
                for p in (content.get("parts") or []):
                    if isinstance(p, dict) and p.get("text"):
                        chunks.append(p["text"])
            return "".join(chunks).strip() or "(empty response)"
        except Exception as e:
            logger.error(f"Reasoning Engine call failed: {e}")
            return f"Agent Error: {str(e)}"

    # Fallback to standard REST endpoint
    predict_url = f"{endpoint_url.rstrip('/')}/predict"
    logger.info(f"Calling live agent REST endpoint: {predict_url} (session={session_id})")
    try:
        resp = requests.post(
            predict_url,
            json={"prompt": prompt, "session_id": session_id},
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
