"""
Smoke-test the four deployed Agent Engine reasoning engines by sending a
single query through the GA agent_engines runtime and printing the response.

    GCP_PROJECT=your-project-id \
    GCP_LOCATION=us-central1 \
    ./venv-adk/bin/python scripts/smoke_test_agents.py
"""

import json
import os
import sys
import textwrap
from pathlib import Path

import vertexai
from vertexai import agent_engines

PROJECT_ID = os.environ.get("GCP_PROJECT", "your-project-id")
LOCATION = os.environ.get("GCP_LOCATION", "us-central1")

ROOT = Path(__file__).resolve().parents[1]

PROMPTS = {
    "orchestrator": "I was double-charged on my last invoice — can you refund me?",
    "billing-specialist": "Refund the duplicate $42 charge on invoice INV-2231.",
    "account-specialist": "Update my email on file from old@example.com to new@example.com.",
    "technical-specialist": "My device keeps crashing after the latest firmware update.",
}


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

    text_chunks: list[str] = []
    for event in events:
        content = event.get("content") if isinstance(event, dict) else None
        if not content:
            continue
        for part in content.get("parts") or []:
            if isinstance(part, dict) and "text" in part and part["text"]:
                text_chunks.append(part["text"])

    response = "".join(text_chunks).strip()
    if response:
        wrapped = textwrap.fill(response, width=100)
        print(f"✅ response:\n{wrapped}")
    else:
        print(f"⚠️  no text in {len(events)} event(s); raw: {events[:1]}")


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


if __name__ == "__main__":
    main()
