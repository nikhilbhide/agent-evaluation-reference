"""HITL eval plane CLI — disagreement adjudication.

Subcommands:

    enqueue   — scan agent_traces, queue rows where code-based and judge
                disagree (or judge is borderline) into human_annotations.

    label     — interactive prompt: pull pending rows one at a time, ask the
                labeler for a verdict (good/bad/partial/skip/quit) and an
                optional note, append a status='labeled' row.

    report    — print judge-vs-human and code-vs-human agreement rates over
                everything labeled to date.

The annotation table is append-only — re-running ``enqueue`` is safe; a
``labeled`` row supersedes its prior ``pending`` row via latest-per-id.

Requires ``setup_telemetry_sink.py`` to have provisioned the dataset and
both tables.
"""

from __future__ import annotations

import argparse
import os
import sys

from agent_eval.evaluation import hitl
from agent_eval.utils.logger import get_logger

logger = get_logger(__name__)


def _resolve_project_id(arg: str | None) -> str:
    pid = arg or os.environ.get("GCP_PROJECT") or os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not pid:
        print("❌ Set GCP_PROJECT (or pass --project).", file=sys.stderr)
        sys.exit(1)
    return pid


def _resolve_labeler(arg: str | None) -> str:
    user = arg or os.environ.get("HITL_LABELER") or os.environ.get("USER")
    if not user:
        print("❌ Set --labeler or HITL_LABELER (e.g. your email).", file=sys.stderr)
        sys.exit(1)
    return user


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", help="GCP project ID (else $GCP_PROJECT).")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_enq = sub.add_parser("enqueue", help="Find disagreements and queue them.")
    p_enq.add_argument("--since-hours", type=int, default=168,
                       help="Look back this many hours of trace data (default: 168 = 1 week).")
    p_enq.add_argument("--limit", type=int, default=500,
                       help="Max trace rows to scan (default: 500).")
    p_enq.add_argument("--dry-run", action="store_true",
                       help="Report what would be queued; don't write to BigQuery.")

    p_lab = sub.add_parser("label", help="Interactively label pending rows.")
    p_lab.add_argument("--labeler", help="Identifier (e.g. email) recorded with each label.")
    p_lab.add_argument("--limit", type=int, default=25,
                       help="Max rows to fetch for this session (default: 25).")

    sub.add_parser("report", help="Print judge/code agreement rates.")

    args = parser.parse_args()
    project_id = _resolve_project_id(args.project)

    if args.cmd == "enqueue":
        result = hitl.cmd_enqueue(
            project_id,
            since_hours=args.since_hours,
            limit=args.limit,
            dry_run=args.dry_run,
        )
        action = "would queue" if args.dry_run else "queued"
        print(f"scanned={result['scanned']}  {action}={result['queued']}  "
              f"inserted={result['inserted']}")
    elif args.cmd == "label":
        labeler = _resolve_labeler(args.labeler)
        hitl.cmd_label(project_id, labeler=labeler, limit=args.limit)
    elif args.cmd == "report":
        hitl.cmd_report(project_id)


if __name__ == "__main__":
    main()
