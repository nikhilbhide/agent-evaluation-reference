"""ADK before_model callback that runs the user's prompt through Model Armor.

Behavior:
  * If `MODEL_ARMOR_TEMPLATE` env var is unset, the callback is a no-op
    (so local dev without armor still works).
  * Calls `:sanitizeUserPrompt` on the configured template.
  * If the response indicates a violation, the callback returns an
    LlmResponse with a refusal — the model is never invoked.
  * On any infrastructure error (network, auth, quota), the callback
    fails *open* and logs a warning. The Cloud Logging line is the
    signal — `setup_alerting.py` already wires a metric for tool-call
    error rate; a similar one can be added for armor errors.

The callback is wired onto the orchestrator (and optionally specialists)
in `agents/orchestrator/app/agent.py`.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

_ARMOR_API_TIMEOUT = 5
_token_lock = threading.Lock()
_token_cache: dict[str, tuple[str, float]] = {}


def _location_from_template(template: str) -> str:
    # Template path: projects/<p>/locations/<loc>/templates/<id>
    parts = template.split("/")
    return parts[3] if len(parts) >= 4 else "us-central1"


def _fetch_access_token() -> str:
    import google.auth
    import google.auth.transport.requests

    cached = _token_cache.get("default")
    if cached and cached[1] - 60 > time.time():
        return cached[0]
    with _token_lock:
        creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        creds.refresh(google.auth.transport.requests.Request())
        _token_cache["default"] = (creds.token, time.time() + 50 * 60)
        return creds.token


def _sanitize(template: str, user_text: str) -> dict[str, Any]:
    import requests

    location = _location_from_template(template)
    url = (
        f"https://modelarmor.{location}.rep.googleapis.com/v1/{template}"
        ":sanitizeUserPrompt"
    )
    body = {"userPromptData": {"text": user_text}}
    resp = requests.post(
        url,
        data=json.dumps(body),
        headers={
            "Authorization": f"Bearer {_fetch_access_token()}",
            "Content-Type": "application/json",
        },
        timeout=_ARMOR_API_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def _is_blocked(result: dict[str, Any]) -> tuple[bool, str]:
    """Pull the verdict out of a sanitize response.

    Model Armor v1 surfaces filterMatchState=MATCH_FOUND on individual
    filters when they triggered. We treat any MATCH_FOUND as a block and
    return the first matched filter as the reason.
    """
    sanitize = result.get("sanitizationResult") or result
    filters = sanitize.get("filterResults") or {}
    for name, sub in filters.items():
        if isinstance(sub, dict):
            inner = sub.get("piAndJailbreakFilterResult") or sub.get("raiFilterResult") or sub
            state = inner.get("matchState") or inner.get("filterMatchState")
            if state == "MATCH_FOUND":
                return True, name
    overall = sanitize.get("filterMatchState")
    if overall == "MATCH_FOUND":
        return True, "overall"
    return False, ""


def _refusal_response(reason: str):
    """Construct an ADK LlmResponse that short-circuits the model call."""
    # Imported lazily so that environments without ADK installed (e.g. tests)
    # don't blow up at module import.
    from google.adk.models import LlmResponse
    from google.genai import types

    text = (
        "I can't help with that request. If you have a legitimate support "
        "issue, please describe it and I'll do my best to assist."
    )
    return LlmResponse(
        content=types.Content(role="model", parts=[types.Part(text=text)]),
        custom_metadata={"model_armor_blocked": True, "model_armor_reason": reason},
    )


def make_before_model_callback() -> Optional[Callable]:
    """Return an ADK before_model_callback bound to the configured template.

    If MODEL_ARMOR_TEMPLATE is unset, returns None — ADK accepts None and
    skips the callback entirely.
    """
    template = os.environ.get("MODEL_ARMOR_TEMPLATE", "").strip()
    if not template:
        logger.info("Model Armor disabled (MODEL_ARMOR_TEMPLATE unset).")
        return None

    def callback(callback_context, llm_request):  # type: ignore[no-untyped-def]
        # Find the latest user-authored text in the request.
        user_text = ""
        for content in reversed(getattr(llm_request, "contents", []) or []):
            if getattr(content, "role", None) != "user":
                continue
            for part in getattr(content, "parts", []) or []:
                txt = getattr(part, "text", None)
                if txt:
                    user_text = txt
                    break
            if user_text:
                break

        if not user_text:
            return None

        try:
            result = _sanitize(template, user_text)
        except Exception as exc:  # pragma: no cover — fail-open on infra error
            logger.warning("Model Armor sanitize failed (fail-open): %s", exc)
            return None

        blocked, reason = _is_blocked(result)
        if blocked:
            logger.warning(
                "Model Armor BLOCKED prompt (filter=%s): %s",
                reason, user_text[:120],
            )
            return _refusal_response(reason)
        return None

    logger.info("Model Armor callback active (template=%s)", template)
    return callback
