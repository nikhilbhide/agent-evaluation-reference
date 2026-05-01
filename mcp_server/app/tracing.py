"""OpenTelemetry → Cloud Trace setup for the MCP server.

Why: the Agent Platform Topology view stitches its agent ↔ MCP graph
from spans in Cloud Trace. Agent Engine already exports spans for the
agent side (``enable_tracing=True`` on AdkApp); this module is the
matching server side. The ``service.name`` resource attribute MUST match
the registered service ID (``mcp-tool-server``) so the Topology UI can
link spans to the Registry entry.

Trace context propagates automatically: the FastAPI instrumentor
extracts the W3C ``traceparent`` header from inbound requests, so the
parent span is the agent's outbound MCP call. No client-side glue is
needed beyond ``opentelemetry.propagate.inject(headers)`` in the agent's
HTTP client (see ``agents/_shared/mcp_client.py``).

Failure mode: if Cloud Trace credentials aren't available (local dev
without ADC, or trace API not enabled), we log a warning and skip
instrumentation rather than crashing the server.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# The service ID registered via scripts/register_in_agent_registry.py.
# Keep these in sync — the Topology view stitches edges by matching
# `service.name` to the Registry service ID.
SERVICE_NAME = "mcp-tool-server"

_initialized = False


def setup_tracing() -> None:
    """Configure OTel TracerProvider with the Cloud Trace exporter.

    Idempotent: safe to call multiple times. No-ops on import errors so
    the server still boots in a stripped-down dev environment.
    """
    global _initialized
    if _initialized:
        return

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.cloud_trace import CloudTraceSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError as exc:
        logger.warning("OpenTelemetry not installed; tracing disabled: %s", exc)
        return

    project_id = os.environ.get("GCP_PROJECT")
    if not project_id:
        logger.warning("GCP_PROJECT not set; tracing disabled.")
        return

    resource = Resource.create({
        "service.name": SERVICE_NAME,
        "service.version": os.environ.get("MCP_SERVICE_VERSION", "1.0.0"),
        "gcp.project_id": project_id,
    })
    provider = TracerProvider(resource=resource)
    try:
        exporter = CloudTraceSpanExporter(project_id=project_id)
    except Exception as exc:
        logger.warning("Cloud Trace exporter init failed: %s", exc)
        return

    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    _initialized = True
    logger.info("✅ OTel tracing → Cloud Trace enabled (service.name=%s)", SERVICE_NAME)


def instrument_app(app) -> None:
    """Wire FastAPI request spans. Call after the FastAPI app is constructed."""
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    except ImportError as exc:
        logger.warning("FastAPI instrumentor unavailable; skipping: %s", exc)
        return
    if not _initialized:
        # Tracing setup failed earlier (no project / missing deps). Don't
        # instrument FastAPI either — the spans would have no exporter.
        return
    FastAPIInstrumentor.instrument_app(app)
    logger.info("✅ FastAPI auto-instrumented with OTel")


def get_tracer():
    """Return a tracer for manual span creation around tool execution."""
    from opentelemetry import trace
    return trace.get_tracer(SERVICE_NAME)
