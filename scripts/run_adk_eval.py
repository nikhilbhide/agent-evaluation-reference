"""ADK AgentEvaluator example — code-based eval plane via ADK's native evaluator.

Runs the orchestrator agent locally against an ADK eval-set, asserting on
trajectory (which AgentTools / specialists were invoked) and on final response
similarity. Complementary to:

  * src/agent_eval/evaluation/programmatic.py  (our own deterministic checks)
  * src/agent_eval/evaluation/runner.py        (Vertex EvalTask, LLM-as-judge)

Usage:
    python scripts/run_adk_eval.py
    python scripts/run_adk_eval.py --eval-set data/adk_eval_set.evalset.json

Notes:
  * AgentEvaluator runs the agent in-process; it does NOT call the deployed
    Agent Engine. Use this in CI/local. For deployed-engine trajectory eval,
    use the programmatic checks in runner.py with the live endpoint.
  * The orchestrator module exports `orchestrator_agent`; AgentEvaluator looks
    for `root_agent` by default. We pass `agent_name=` if the API supports it,
    otherwise add a `root_agent = orchestrator_agent` alias to the module.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


async def run(eval_set_path: Path, agent_module: str, num_runs: int) -> int:
    try:
        from google.adk.evaluation.agent_evaluator import AgentEvaluator
    except ImportError:
        print(
            "❌ google-adk[eval] not installed. "
            "Install with: pip install 'google-adk[eval]'"
        )
        return 1

    if not eval_set_path.exists():
        print(f"❌ Eval set not found: {eval_set_path}")
        return 1

    print(f"Running ADK AgentEvaluator on {eval_set_path.name} "
          f"(agent_module={agent_module}, num_runs={num_runs})...")

    try:
        await AgentEvaluator.evaluate(
            agent_module=agent_module,
            eval_dataset_file_path_or_dir=str(eval_set_path),
            num_runs=num_runs,
        )
    except AttributeError as exc:
        print(
            f"❌ Could not load `root_agent` from {agent_module}. "
            f"Add `root_agent = orchestrator_agent` to the module's __init__ "
            f"or agent.py and retry. ({exc})"
        )
        return 1
    except AssertionError as exc:
        print(f"❌ ADK eval assertions failed:\n{exc}")
        return 1

    print("✅ ADK eval-set passed all assertions.")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--eval-set",
        type=Path,
        default=REPO_ROOT / "data" / "adk_eval_set.evalset.json",
    )
    parser.add_argument(
        "--agent-module",
        default="agents.orchestrator.app.agent",
        help="Importable module exposing `root_agent` (or alias).",
    )
    parser.add_argument("--num-runs", type=int, default=1)
    args = parser.parse_args()
    sys.exit(asyncio.run(run(args.eval_set, args.agent_module, args.num_runs)))


if __name__ == "__main__":
    main()
