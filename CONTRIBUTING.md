# Contributing to GCP Agent Eval

Thank you for your interest in contributing! This is a reference implementation — contributions that improve clarity, correctness, or coverage are especially welcome.

## Running Locally

```bash
# Clone and set up
git clone https://github.com/YOUR_ORG/agent-evaluation-reference.git
cd agent-evaluation-reference
make setup
source venv/bin/activate

# Configure GCP
cp .env.example .env
# Edit .env: set GOOGLE_CLOUD_PROJECT
gcloud auth application-default login

# Run unit tests (no GCP needed)
make test

# Run live evaluation (needs Vertex AI API access)
make eval
```

## Making Changes

1. **Fork** the repo and create a branch: `git checkout -b feat/my-improvement`
2. **Write tests** for any new code in `tests/`. All PRs must keep `make test` green.
3. **Run the full test suite** before opening a PR: `make test`
4. **Open a PR** against `main`. The CI pipeline (`.github/workflows/ci.yml`) will run automatically.

## PR Guidelines

- Keep PRs focused: one logical change per PR
- Add/update docstrings for any new public functions
- If adding a new CLI flag, update `README.md`
- If touching deploy flow, update the relevant `Makefile` target and the README phase tables

## Project Structure Quick Reference

| Directory | Purpose |
|---|---|
| `src/agent_eval/` | Evaluation framework Python package (runner, metrics, telemetry, ABOM) |
| `agents/` | ADK agents deployed to Vertex AI Agent Engine (orchestrator + 3 specialists + `_shared/`) |
| `mcp_server/` | FastAPI MCP tool server deployed to Cloud Run |
| `deploy/monitoring/` | Cloud Monitoring dashboard + alert policy JSON |
| `scripts/` | Deploy, register, telemetry, red-team, tuning, and load-test scripts |
| `tests/` | Unit tests (no GCP calls — fully mocked) |
| `data/` | Golden evaluation dataset + red-team prompts |
| `.github/workflows/` | CI pipeline (`ci.yml`) |

## Reporting Issues

Please open a GitHub Issue with:
- What you were trying to do
- What you expected to happen
- What actually happened (include logs/output if possible)
