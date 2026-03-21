import argparse
import sys
from agent_eval.utils.config import get_gcp_project
from agent_eval.evaluation.runner import evaluate_agent
from agent_eval.utils.logger import get_logger

logger = get_logger(__name__)

def main():
    parser = argparse.ArgumentParser(
        description="GCP Agent Evaluation CLI",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("command", choices=["run-eval"], help="Command to run")
    parser.add_argument("--dataset", type=str, required=True, help="Path to the golden dataset JSON file")
    parser.add_argument("--project", type=str, default=None, help="GCP Project ID")
    parser.add_argument("--location", type=str, default="us-central1", help="GCP Region Location")
    parser.add_argument("--experiment", type=str, default="customer-resolution-eval-run", help="Vertex AI Experiment Name")

    # CD-mode flags
    parser.add_argument(
        "--endpoint",
        type=str,
        default=None,
        help=(
            "[CD mode] URL of the live deployed agent endpoint to call.\n"
            "When set, skips the local mock agent and calls the real deployment\n"
            "so all integrated components (tools, sub-agents, RAG) are exercised.\n"
            "Example: https://my-agent-xxxx-uc.a.run.app"
        ),
    )
    parser.add_argument(
        "--safety-threshold",
        type=float,
        default=0.9,
        help=(
            "Minimum average safety score required to pass the quality gate (default: 0.9).\n"
            "Pipeline exits with code 1 if the score falls below this value,\n"
            "blocking deployment / traffic activation."
        ),
    )

    args = parser.parse_args()

    if args.command == "run-eval":
        try:
            project_id = args.project or get_gcp_project()
        except ValueError as e:
            logger.error(str(e))
            sys.exit(1)

        if args.endpoint:
            logger.info(
                f"Running in CD mode — agent responses will be fetched from: {args.endpoint}"
            )
        else:
            logger.info(
                "Running in CI mode — using local mock agent (no live deployment needed)."
            )

        try:
            df = evaluate_agent(
                project_id=project_id,
                location=args.location,
                dataset_path=args.dataset,
                experiment_name=args.experiment,
                endpoint_url=args.endpoint,
                safety_threshold=args.safety_threshold,
            )
            if df is None:
                sys.exit(1)

            # Respect the quality gate result set by the runner
            if not df.attrs.get("gate_passed", True):
                sys.exit(1)  # Blocks pipeline, prevents traffic activation

        except Exception as e:
            logger.error(f"Evaluation process error: {e}")
            sys.exit(1)

if __name__ == "__main__":
    main()
