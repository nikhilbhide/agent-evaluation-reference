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
    
    # Check if it's a Reasoning Engine resource name
    if endpoint_url.startswith("projects/"):
        logger.info(f"Calling Reasoning Engine: {endpoint_url} (session={session_id})")
        try:
            remote_agent = reasoning_engines.ReasoningEngine(endpoint_url)
            # Standardize sid
            sid = str(session_id) if (session_id and str(session_id) != "nan") else "eval-session"
            
            # 1. Try .query()
            if hasattr(remote_agent, 'query'):
                response = remote_agent.query(input=prompt, user_id="eval-user", session_id=sid)
                return str(response)
            
            # 2. Try .stream_query()
            if hasattr(remote_agent, 'stream_query'):
                responses = []
                for chunk in remote_agent.stream_query(message=prompt, user_id="eval-user", session_id=sid):
                    # Handle ADK response objects
                    if hasattr(chunk, 'text'):
                        responses.append(chunk.text)
                    elif isinstance(chunk, dict) and 'text' in chunk:
                        responses.append(chunk['text'])
                    else:
                        responses.append(str(chunk))
                return "".join(responses)

            # 3. Try .predict()
            if hasattr(remote_agent, 'predict'):
                response = remote_agent.predict(input=prompt)
                return str(response)

            # 4. Final attempt: direct call if it's a callable app
            try:
                response = remote_agent.query(input=prompt, user_id="eval-user", session_id=sid)
                return str(response)
            except Exception:
                return f"Agent Error: Remote agent at {endpoint_url} has no supported methods (query/stream_query/predict)."

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
