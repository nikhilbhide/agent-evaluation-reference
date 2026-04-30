"""Centralized config for runtime knobs.

Every value here resolves from an environment variable. Defaults are chosen
to match the original hardcoded values so behavior is unchanged unless the
operator overrides. Project ID has NO default — agents fail loud rather than
silently target the wrong project.
"""

from __future__ import annotations

import os


def require(env_var: str) -> str:
    """Read a required env var or raise. Use for values that must not silently
    default to something wrong (project IDs, region, etc)."""
    val = os.environ.get(env_var)
    if not val:
        raise RuntimeError(
            f"Required environment variable {env_var} is not set. "
            f"Set it in .env or export it before running."
        )
    return val


# ── Models ────────────────────────────────────────────────────────────────────
# Orchestrator runs on Pro by default for routing/reasoning quality.
# Specialists run on Flash for cost.
ORCHESTRATOR_MODEL = os.environ.get("ORCHESTRATOR_MODEL", "gemini-2.5-pro")
SPECIALIST_MODEL = os.environ.get("SPECIALIST_MODEL", "gemini-2.5-flash")

# Memory Bank uses one model to summarize sessions into memories and another
# to embed them for similarity search.
MEMORY_BANK_GENERATION_MODEL = os.environ.get(
    "MEMORY_BANK_GENERATION_MODEL", "gemini-2.5-flash"
)
MEMORY_BANK_EMBEDDING_MODEL = os.environ.get(
    "MEMORY_BANK_EMBEDDING_MODEL", "text-embedding-005"
)

# Red-team attacker + judge models. Default to Flash so the gate is cheap.
REDTEAM_ATTACKER_MODEL = os.environ.get("REDTEAM_ATTACKER_MODEL", "gemini-2.5-flash")
REDTEAM_JUDGE_MODEL = os.environ.get("REDTEAM_JUDGE_MODEL", "gemini-2.5-flash")


# ── Region ────────────────────────────────────────────────────────────────────
GCP_LOCATION = os.environ.get("GCP_LOCATION", "us-central1")


# ── Agent Engine display names ────────────────────────────────────────────────
# Canonical display names used as the de-facto identity of each Reasoning
# Engine in the Vertex AI Console. They double as the keys
# `redeploy_all.py` matches against when tearing down stale resources, and as
# the `agent_name` field stamped into the ABOM, so they MUST agree across
# every script. Override only when running side-by-side with the reference
# stack in the same project.
ORCHESTRATOR_DISPLAY_NAME = os.environ.get(
    "ORCHESTRATOR_DISPLAY_NAME", "customer-resolution-orchestrator"
)
BILLING_DISPLAY_NAME = os.environ.get("BILLING_DISPLAY_NAME", "billing-specialist")
ACCOUNT_DISPLAY_NAME = os.environ.get("ACCOUNT_DISPLAY_NAME", "account-specialist")
TECHNICAL_DISPLAY_NAME = os.environ.get("TECHNICAL_DISPLAY_NAME", "technical-specialist")


# ── GCS staging bucket ────────────────────────────────────────────────────────
# The Agent Engine SDK pickles the agent into this bucket before creating the
# Reasoning Engine. The default `gs://<prefix>-<project>` shape is what the
# scripts have always assumed; surfacing the prefix makes it overridable
# without forcing every operator to set the full GCS URL.
STAGING_BUCKET_PREFIX = os.environ.get("GCP_STAGING_BUCKET_PREFIX", "agent-eval-staging")


def staging_bucket(project_id: str) -> str:
    """Resolve the staging bucket URL.

    Priority: explicit `GCP_STAGING_BUCKET` env > `gs://{prefix}-{project}`.
    Centralized so deploy/redeploy/tuning scripts don't drift apart.
    """
    explicit = os.environ.get("GCP_STAGING_BUCKET")
    if explicit:
        return explicit
    return f"gs://{STAGING_BUCKET_PREFIX}-{project_id}"
