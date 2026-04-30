"""Single source of truth for ADK / aiplatform / genai pin versions.

These pins flow into every Agent Engine deploy via the `requirements` argument
to `agent_engines.create()`. Bumping a version here updates all four engines
(orchestrator + 3 specialists) on the next deploy.
"""

from __future__ import annotations

ADK_VERSION = "1.31.1"
AIPLATFORM_VERSION = "1.148.1"
GENAI_VERSION = "1.73.1"

REQUIREMENTS: list[str] = [
    f"google-adk=={ADK_VERSION}",
    f"google-cloud-aiplatform[agent_engines,adk]=={AIPLATFORM_VERSION}",
    f"google-genai=={GENAI_VERSION}",
    "requests",
]

EXTRA_PACKAGES: list[str] = ["agents", "src"]
