"""
Smoke-test the four deployed Agent Engine reasoning engines by sending a
single query through the GA agent_engines runtime and printing the response.

    GCP_PROJECT=<your-project-id> \
    GCP_LOCATION=us-central1 \
    ./venv-adk/bin/python scripts/smoke_test_agents.py
"""

import asyncio
import json
import os
import sys
import textwrap
import time
from pathlib import Path

import vertexai
from vertexai import agent_engines

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agents._shared.config import require

PROJECT_ID = require("GCP_PROJECT")
LOCATION = os.environ.get("GCP_LOCATION", "us-central1")

PROMPTS = {
    "orchestrator": "I was double-charged on my last invoice — can you refund me?",
    "billing-specialist": "Refund the duplicate $42 charge on invoice INV-2231.",
    "account-specialist": "Update my email on file from old@example.com to new@example.com.",
    "technical-specialist": "My device keeps crashing after the latest firmware update.",
}

# Cross-session memory test: stash a fact in session A, retrieve it in session B
# under the same user_id. Validates Memory Bank wiring + PreloadMemoryTool.
MEMORY_TEST_USER = "mem-test-001"
MEMORY_SEED_PROMPT = "I'm customer C-9001, my preferred contact is email."
MEMORY_RECALL_PROMPT = "What's my preferred contact method?"
MEMORY_EXPECTED_TOKEN = "email"
MEMORY_PERSIST_WAIT_SECONDS = int(os.environ.get("MEMORY_PERSIST_WAIT_SECONDS", "30"))


def load_resource_names() -> dict[str, str]:
    names: dict[str, str] = {}
    orch = (ROOT / "deployed_agent_resource.txt").read_text().strip()
    if orch:
        names["orchestrator"] = orch
    manifest_path = ROOT / "deployed_specialist_agents.json"
    if manifest_path.exists():
        names.update(json.loads(manifest_path.read_text()))
    return names


def query_one(label: str, resource_name: str, prompt: str) -> None:
    print(f"\n=== {label} ===")
    print(f"resource: {resource_name}")
    print(f"prompt:   {prompt}")
    engine = agent_engines.get(resource_name)
    try:
        events = list(engine.stream_query(message=prompt, user_id=f"smoke-{label}"))
    except Exception as exc:
        print(f"❌ stream_query failed: {exc}")
        return

    response = _collect_response_text(events)
    if response:
        wrapped = textwrap.fill(response, width=100)
        print(f"✅ response:\n{wrapped}")
    else:
        print(f"⚠️  no text in {len(events)} event(s); raw: {events[:1]}")


def _collect_response_text(events) -> str:
    text_chunks: list[str] = []
    for event in events:
        content = event.get("content") if isinstance(event, dict) else None
        if not content:
            continue
        for part in content.get("parts") or []:
            if isinstance(part, dict) and "text" in part and part["text"]:
                text_chunks.append(part["text"])
    return "".join(text_chunks).strip()


def cross_session_memory_test(orchestrator_resource: str) -> None:
    """Two-turn cross-session test: turn 1 seeds a fact, turn 2 (different
    session, same user_id) recalls it from Memory Bank via PreloadMemoryTool.

    delete_session does NOT reliably trigger implicit memory generation, so
    we explicitly call async_add_session_to_memory after turn 1. This is the
    documented contract for Vertex AI Memory Bank — implicit auto-generation
    requires either a `LLMAgent` lifecycle hook or an explicit add call.
    """
    print("\n=== cross-session memory test (orchestrator) ===")
    print(f"resource: {orchestrator_resource}")
    engine = agent_engines.get(orchestrator_resource)

    # ── Turn 1: session A seeds the fact ──
    session_a = engine.create_session(user_id=MEMORY_TEST_USER)
    session_a_id = session_a["id"] if isinstance(session_a, dict) else session_a.id
    print(f"turn 1 (session={session_a_id}): {MEMORY_SEED_PROMPT}")
    try:
        events_a = list(engine.stream_query(
            message=MEMORY_SEED_PROMPT,
            user_id=MEMORY_TEST_USER,
            session_id=session_a_id,
        ))
    except Exception as exc:
        print(f"❌ turn 1 stream_query failed: {exc}")
        return
    print(f"  turn 1 response: {textwrap.shorten(_collect_response_text(events_a), width=120)}")

    # Explicit memory write — fetch the full session (with events) and feed
    # it to the AdkApp's add_session_to_memory hook on the runtime.
    try:
        full_session = engine.get_session(
            user_id=MEMORY_TEST_USER, session_id=session_a_id,
        )
        asyncio.run(engine.async_add_session_to_memory(session=full_session))
        print(f"  add_session_to_memory called ({len(full_session.get('events', []))} events)")
    except Exception as exc:
        print(f"❌ add_session_to_memory failed: {exc}")
        return

    print(f"  waiting {MEMORY_PERSIST_WAIT_SECONDS}s for memory persistence...")
    time.sleep(MEMORY_PERSIST_WAIT_SECONDS)

    # ── Turn 2: session B (different session, same user) should recall ──
    session_b = engine.create_session(user_id=MEMORY_TEST_USER)
    session_b_id = session_b["id"] if isinstance(session_b, dict) else session_b.id
    print(f"turn 2 (session={session_b_id}): {MEMORY_RECALL_PROMPT}")
    try:
        events_b = list(engine.stream_query(
            message=MEMORY_RECALL_PROMPT,
            user_id=MEMORY_TEST_USER,
            session_id=session_b_id,
        ))
    except Exception as exc:
        print(f"❌ turn 2 stream_query failed: {exc}")
        return

    response_b = _collect_response_text(events_b)
    wrapped = textwrap.fill(response_b, width=100)
    print(f"  turn 2 response:\n{wrapped}")

    # Stronger assertion — the agent must affirmatively *state* the preference
    # (e.g. "your preferred contact is email"), not just mention "email" while
    # asking the user to provide it.
    rl = response_b.lower()
    affirmative_phrases = [
        "preferred contact is email",
        "preferred contact method is email",
        "preferred contact method is your email",
        "you prefer email",
        "your preferred contact method is your primary email",
    ]
    if any(p in rl for p in affirmative_phrases):
        print("✅ memory recall PASSED — agent affirmatively stated email as preferred contact")
    else:
        print("❌ memory recall FAILED — agent did not affirmatively recall the preference")

    try:
        engine.delete_session(user_id=MEMORY_TEST_USER, session_id=session_a_id)
        engine.delete_session(user_id=MEMORY_TEST_USER, session_id=session_b_id)
    except Exception as exc:
        print(f"⚠️  cleanup delete_session failed: {exc}")


def main() -> None:
    vertexai.init(project=PROJECT_ID, location=LOCATION)
    resource_names = load_resource_names()
    if not resource_names:
        print("❌ No deployed_*_resource.txt / deployed_specialist_agents.json found.")
        sys.exit(1)

    for label, prompt in PROMPTS.items():
        resource_name = resource_names.get(label)
        if not resource_name:
            print(f"\n⚠️  Skipping {label}: no resource name on disk.")
            continue
        query_one(label, resource_name, prompt)

    orchestrator_resource = resource_names.get("orchestrator")
    if orchestrator_resource:
        cross_session_memory_test(orchestrator_resource)
    else:
        print("\n⚠️  Skipping cross-session memory test: orchestrator resource missing.")


if __name__ == "__main__":
    main()
