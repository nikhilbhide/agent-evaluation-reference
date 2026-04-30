import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import vertexai
from vertexai import agent_engines
from agents._shared.config import (
    MEMORY_BANK_EMBEDDING_MODEL,
    MEMORY_BANK_GENERATION_MODEL,
    ORCHESTRATOR_DISPLAY_NAME,
    staging_bucket,
)
from agents._shared.versions import (
    ADK_VERSION,
    EXTRA_PACKAGES,
    REQUIREMENTS,
)
from agents.orchestrator.app.agent import orchestrator_agent

# ── Configuration ─────────────────────────────────────────────────────────────
PROJECT_ID = os.environ.get("GCP_PROJECT")
LOCATION = os.environ.get("GCP_LOCATION", "us-central1")

if not PROJECT_ID:
    print("❌ GCP_PROJECT environment variable must be set.")
    sys.exit(1)

STAGING_BUCKET = staging_bucket(PROJECT_ID)


def _read_mcp_url() -> str | None:
    """MCP_SERVER_URL drives the audience for the authenticated MCP client.
    Source priority: env var → mcp_server_url.txt at repo root."""
    env = os.environ.get("MCP_SERVER_URL")
    if env:
        return env.strip()
    url_file = Path(__file__).resolve().parents[1] / "mcp_server_url.txt"
    if url_file.exists():
        return url_file.read_text().strip() or None
    return None


_MCP_URL = _read_mcp_url()


def _read_model_armor_template() -> str | None:
    env = os.environ.get("MODEL_ARMOR_TEMPLATE")
    if env:
        return env.strip()
    f = Path(__file__).resolve().parents[1] / "model_armor_template.txt"
    if f.exists():
        return f.read_text().strip() or None
    return None


_MODEL_ARMOR = _read_model_armor_template()

ENV_VARS = {"GOOGLE_GENAI_USE_VERTEXAI": "true"}
if _MCP_URL:
    ENV_VARS["MCP_SERVER_URL"] = _MCP_URL
else:
    print("⚠️  MCP_SERVER_URL not set and mcp_server_url.txt missing — "
          "specialists deployed without it will fail loud on tool calls.")
if _MODEL_ARMOR:
    ENV_VARS["MODEL_ARMOR_TEMPLATE"] = _MODEL_ARMOR
else:
    print("⚠️  MODEL_ARMOR_TEMPLATE not set and model_armor_template.txt missing — "
          "agents will deploy WITHOUT armor enforcement. Run scripts/setup_model_armor.py.")

ORCHESTRATOR_SERVICE_ACCOUNT = f"agent-orchestrator@{PROJECT_ID}.iam.gserviceaccount.com"
# Unique GCS staging path so multiple engines deployed to the same bucket
# don't overwrite each other's pickles. Without this, the SDK defaults to
# `agent_engine/` and the LAST deploy wins on cold-start across all engines.
ORCHESTRATOR_GCS_DIR_NAME = "agent_engine_orchestrator"


def _make_memory_builder(agent_engine_id: str):
    """Factory that returns a callable producing a Memory Bank service bound to agent_engine_id."""
    def builder():
        from google.adk.memory import VertexAiMemoryBankService
        return VertexAiMemoryBankService(
            project=PROJECT_ID,
            location=LOCATION,
            agent_engine_id=agent_engine_id,
        )
    return builder


def _make_session_builder(agent_engine_id: str):
    def builder():
        from google.adk.sessions import VertexAiSessionService
        return VertexAiSessionService(
            project=PROJECT_ID,
            location=LOCATION,
            agent_engine_id=agent_engine_id,
        )
    return builder


def deploy():
    if sys.version_info < (3, 10):
        print("❌ Agent Engine ADK deployments must be created from Python 3.10+.")
        sys.exit(1)

    print(f"🚀 Initializing Vertex AI (project={PROJECT_ID}, location={LOCATION})")
    vertexai.init(project=PROJECT_ID, location=LOCATION, staging_bucket=STAGING_BUCKET)

    # ── Pass 1: create without memory wiring ──────────────────────────────────
    # Memory Bank + Session services are self-referential: they need the engine's
    # own resource ID, which we don't know until create() returns. So we create
    # first, then re-bind via update().
    print("📦 Pass 1: wrapping Orchestrator in AdkApp without memory wiring...")
    app = agent_engines.AdkApp(
        agent=orchestrator_agent,
        enable_tracing=True,
    )

    print(f"☁️  Creating Vertex AI Agent Engine (orchestrator) as {ORCHESTRATOR_SERVICE_ACCOUNT}...")
    remote_app = agent_engines.create(
        agent_engine=app,
        display_name=ORCHESTRATOR_DISPLAY_NAME,
        description="Managed ADK multi-agent orchestrator for TechCorp support.",
        requirements=REQUIREMENTS,
        extra_packages=EXTRA_PACKAGES,
        env_vars=ENV_VARS,
        service_account=ORCHESTRATOR_SERVICE_ACCOUNT,
        gcs_dir_name=ORCHESTRATOR_GCS_DIR_NAME,
    )

    resource_name = remote_app.resource_name
    engine_id = resource_name.split("/")[-1]
    print(f"✅ Orchestrator created: {resource_name}")

    with open("deployed_agent_resource.txt", "w") as f:
        f.write(resource_name)

    # ── Pass 2: re-bind with self-referential memory + session services ───────
    print("🔁 Pass 2: re-binding orchestrator with memory_service_builder + session_service_builder pointing at self...")
    wired_app = agent_engines.AdkApp(
        agent=orchestrator_agent,
        enable_tracing=True,
        memory_service_builder=_make_memory_builder(engine_id),
        session_service_builder=_make_session_builder(engine_id),
    )

    agent_engines.update(
        resource_name,
        agent_engine=wired_app,
        requirements=REQUIREMENTS,
        extra_packages=EXTRA_PACKAGES,
        env_vars=ENV_VARS,
        service_account=ORCHESTRATOR_SERVICE_ACCOUNT,
        gcs_dir_name=ORCHESTRATOR_GCS_DIR_NAME,
    )
    print("✅ Orchestrator updated with self-bound memory + session services.")

    # ── Enable Memory Bank on the engine resource ─────────────────────────────
    # The AdkApp's memory_service_builder is just a client-side factory.
    # The Memory Bank itself only exists if context_spec.memory_bank_config is
    # set on the ReasoningEngine resource. Without this, writes succeed at the
    # SDK level but no memories are ever persisted.
    try:
        from google.cloud import aiplatform_v1beta1 as aip
        from google.cloud.aiplatform_v1beta1.types import ReasoningEngineContextSpec, ReasoningEngine
        from google.protobuf import field_mask_pb2

        re_client = aip.ReasoningEngineServiceClient(
            client_options={"api_endpoint": f"{LOCATION}-aiplatform.googleapis.com"}
        )
        gen_model = f"projects/{PROJECT_ID}/locations/{LOCATION}/publishers/google/models/{MEMORY_BANK_GENERATION_MODEL}"
        emb_model = f"projects/{PROJECT_ID}/locations/{LOCATION}/publishers/google/models/{MEMORY_BANK_EMBEDDING_MODEL}"
        ctx_spec = ReasoningEngineContextSpec(
            memory_bank_config=ReasoningEngineContextSpec.MemoryBankConfig(
                generation_config=ReasoningEngineContextSpec.MemoryBankConfig.GenerationConfig(model=gen_model),
                similarity_search_config=ReasoningEngineContextSpec.MemoryBankConfig.SimilaritySearchConfig(embedding_model=emb_model),
            )
        )
        op = re_client.update_reasoning_engine(
            reasoning_engine=ReasoningEngine(name=resource_name, context_spec=ctx_spec),
            update_mask=field_mask_pb2.FieldMask(paths=["context_spec"]),
        )
        op.result(timeout=120)
        print("✅ Memory Bank enabled on orchestrator engine (context_spec.memory_bank_config).")
    except Exception as mb_err:
        print(f"⚠️  Could not enable Memory Bank: {mb_err}")

    # ── ADK Labels ────────────────────────────────────────────────────────────
    # Belt-and-suspenders: the GA path already registers the runtime as ADK,
    # but explicit labels keep parity with register_agents.py and surface the
    # agent in label-based dashboards.
    try:
        from google.cloud import aiplatform_v1beta1
        from google.protobuf import field_mask_pb2

        client = aiplatform_v1beta1.ReasoningEngineServiceClient(
            client_options={"api_endpoint": f"{LOCATION}-aiplatform.googleapis.com"}
        )

        labels = {
            "goog-vertex-reasoning-engine-adk": "true",
            "goog-adk-version": ADK_VERSION.replace(".", "-"),
            "goog-vertex-reasoning-engine-template": "adk",
            "enterprise-agent-type": "orchestrator",
        }

        res = client.get_reasoning_engine(name=resource_name)
        res.labels.update(labels)
        mask = field_mask_pb2.FieldMask(paths=["labels"])

        print("⏳ Applying ADK labels for playground visibility...")
        operation = client.update_reasoning_engine(reasoning_engine=res, update_mask=mask)
        operation.result(timeout=90)
        print("✅ ADK labels applied. Playground should be active in the Console.")
    except Exception as label_err:
        print(f"⚠️  Warning: Could not apply ADK labels: {label_err}")

    # ── Build-phase governance: ABOM + SCC publish ───────────────────────────
    # Folded in from the deprecated scripts/deploy_enterprise_agent.py so the
    # Build-phase artifacts reflect the *actually deployed* configuration:
    # real model name from orchestrator_agent.model, real tool tree, real
    # Model Armor template binding (or absent, if not provisioned).
    try:
        from src.agent_eval.utils.abom import ABOMGenerator, extract_tool_manifest
    except ModuleNotFoundError:
        # extra_packages flatten src/* — import without the prefix.
        from agent_eval.utils.abom import ABOMGenerator, extract_tool_manifest  # type: ignore

    agent_version = os.environ.get("GITHUB_SHA", "dev-manual")
    abom = ABOMGenerator(
        agent_name=ORCHESTRATOR_DISPLAY_NAME,
        version=agent_version,
        gsa_identity=ORCHESTRATOR_SERVICE_ACCOUNT,
        model_name=getattr(orchestrator_agent, "model", "unknown"),
        model_version="latest",
        system_instructions=getattr(orchestrator_agent, "instruction", "") or "",
        tools=extract_tool_manifest(orchestrator_agent),
        dependencies=REQUIREMENTS,
        eval_run_id=os.environ.get("GITHUB_RUN_ID", "local-dev"),
        model_armor_template=_MODEL_ARMOR,
    )
    abom_path = "build/abom.json"
    abom.save(abom_path)

    try:
        from scripts.publish_scc_findings import publish_finding
        publish_finding(PROJECT_ID, abom_path)
    except Exception as scc_err:
        print(f"⚠️  SCC publish skipped: {scc_err}")

    enterprise_meta = {
        "resource_name": resource_name,
        "abom_link": abom_path,
        "identity": ORCHESTRATOR_SERVICE_ACCOUNT,
        "model_armor_template": _MODEL_ARMOR or "(disabled)",
        "mcp_server_url": _MCP_URL or "(unset)",
    }
    Path("build").mkdir(exist_ok=True)
    Path("build/enterprise_metadata.json").write_text(json.dumps(enterprise_meta, indent=2))
    print(f"✅ ABOM + governance metadata written to build/")


if __name__ == "__main__":
    deploy()
