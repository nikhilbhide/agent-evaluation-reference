# =============================================================================
# Makefile — convenience targets for the GCP Agent Evaluation reference project
#
# Usage: make <target>
# Run   `make help` to see all available targets.
# =============================================================================

.DEFAULT_GOAL := help
VENV          := venv
PYTHON        := $(VENV)/bin/python
PIP           := $(VENV)/bin/pip

## ── Setup ────────────────────────────────────────────────────────────────────

.PHONY: setup
setup:  ## Create venv and install all dependencies (including dev)
	python3 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -e ".[dev]"
	@echo "✅  Setup complete. Activate with: source $(VENV)/bin/activate"

## ── Build phase: deploy MCP + 4 agents to Agent Engine ───────────────────────

.PHONY: deploy-mcp
deploy-mcp:  ## Build and deploy the MCP tool server to Cloud Run
	@test -n "$(GCP_PROJECT)" || (echo "❌ Set GCP_PROJECT" && exit 1)
	./scripts/deploy_mcp_cloud_run.sh $(GCP_PROJECT) $(GCP_LOCATION)

.PHONY: enterprise-deploy
enterprise-deploy:  ## Deploy orchestrator + 3 specialists (Agent Engine + ABOM + SCC)
	@test -n "$(GCP_PROJECT)" || (echo "❌ Set GCP_PROJECT" && exit 1)
	$(PYTHON) scripts/deploy_agent_engine.py
	$(PYTHON) scripts/register_agents.py

.PHONY: redeploy-all
redeploy-all:  ## Tear down all 4 agents and redeploy from scratch
	@test -n "$(GCP_PROJECT)" || (echo "❌ Set GCP_PROJECT" && exit 1)
	$(PYTHON) scripts/redeploy_all.py

.PHONY: patch-adk-labels
patch-adk-labels:  ## Stamp ADK Playground labels on a deployed agent (reads deployed_agent_resource.txt if RESOURCE unset)
	$(PYTHON) scripts/patch_agent_labels.py $(RESOURCE)

.PHONY: register-in-platform
register-in-platform:  ## Register MCP + agents in the Agent Platform Registry (populates Topology)
	@test -n "$(GCP_PROJECT)" || (echo "❌ Set GCP_PROJECT" && exit 1)
	$(PYTHON) scripts/register_in_agent_registry.py

.PHONY: online-monitor
online-monitor:  ## Provision/update the OnlineEvaluator that scores live traffic from Cloud Trace
	@test -n "$(GCP_PROJECT)" || (echo "❌ Set GCP_PROJECT" && exit 1)
	$(PYTHON) scripts/create_online_monitor.py

## ── Scale phase: smoke + load test ───────────────────────────────────────────

.PHONY: smoke
smoke:  ## End-to-end smoke test (memory persistence, tool calls, routing)
	@test -n "$(GCP_PROJECT)" || (echo "❌ Set GCP_PROJECT" && exit 1)
	$(PYTHON) scripts/smoke_test_agents.py

.PHONY: load-test
load-test:  ## Throughput + p95 latency probe against the deployed orchestrator
	@test -n "$(GCP_PROJECT)" || (echo "❌ Set GCP_PROJECT" && exit 1)
	$(PYTHON) scripts/load_test_agent_engine.py

## ── Govern phase: telemetry, alerts, red-team, model armor ───────────────────

.PHONY: govern-setup
govern-setup:  ## Provision telemetry sink, alerts, SCC findings (build/abom.json must exist)
	@test -n "$(GCP_PROJECT)" || (echo "❌ Set GCP_PROJECT" && exit 1)
	$(PYTHON) scripts/setup_telemetry_sink.py
	$(PYTHON) scripts/setup_alerting.py
	$(PYTHON) scripts/publish_scc_findings.py

.PHONY: setup-telemetry
setup-telemetry:  ## Provision BigQuery telemetry dataset/table + log sink
	@test -n "$(GCP_PROJECT)" || (echo "❌ Set GCP_PROJECT" && exit 1)
	$(PYTHON) scripts/setup_telemetry_sink.py

.PHONY: setup-armor
setup-armor:  ## Provision the Model Armor template + write model_armor_template.txt
	@test -n "$(GCP_PROJECT)" || (echo "❌ Set GCP_PROJECT" && exit 1)
	$(PYTHON) scripts/setup_model_armor.py

.PHONY: redteam
redteam:  ## Run adversarial red-team gate (auto-grades, appends failures to golden dataset)
	@test -n "$(GCP_PROJECT)"     || (echo "❌ Set GCP_PROJECT" && exit 1)
	@test -n "$(AGENT_ENDPOINT)"  || (echo "❌ Set AGENT_ENDPOINT" && exit 1)
	$(PYTHON) scripts/run_simulation.py

## ── Optimize phase: continuous improvement loop ──────────────────────────────

.PHONY: optimize-trigger
optimize-trigger:  ## Mine BigQuery, build SFT dataset, trigger Vertex AI tuning (TUNING_DRY_RUN=0 to submit)
	@test -n "$(GCP_PROJECT)" || (echo "❌ Set GCP_PROJECT" && exit 1)
	$(PYTHON) scripts/trigger_tuning.py

## ── Evaluation ───────────────────────────────────────────────────────────────

.PHONY: eval
eval:  ## Run evaluation with the CI mock agent (needs GOOGLE_CLOUD_PROJECT)
	$(VENV)/bin/agent-eval run-eval \
		--dataset data/golden_dataset.json

.PHONY: eval-live
eval-live:  ## Evaluate the deployed orchestrator (set AGENT_ENDPOINT in .env)
	@test -n "$(AGENT_ENDPOINT)" || (echo "❌ Set AGENT_ENDPOINT first" && exit 1)
	$(VENV)/bin/agent-eval run-eval \
		--dataset data/golden_dataset.json \
		--endpoint $(AGENT_ENDPOINT) \
		--safety-threshold 0.9

## ── Local development ────────────────────────────────────────────────────────

.PHONY: run-adk-local
run-adk-local:  ## Run the ADK orchestrator locally (set MCP_SERVER_URL to your local/staging MCP)
	@test -n "$(GOOGLE_CLOUD_PROJECT)" || (echo "❌ Set GOOGLE_CLOUD_PROJECT" && exit 1)
	GCP_PROJECT=$(GOOGLE_CLOUD_PROJECT) \
	$(PYTHON) agents/orchestrator/app/main_adk.py

## ── Testing ──────────────────────────────────────────────────────────────────

.PHONY: test
test:  ## Run unit tests (no GCP calls, fully mocked)
	$(VENV)/bin/pytest tests/ -v --tb=short

.PHONY: test-coverage
test-coverage:  ## Run tests with coverage report
	$(VENV)/bin/pytest tests/ --cov=src/agent_eval --cov-report=term-missing

.PHONY: lint
lint:  ## Run mypy type checker
	$(VENV)/bin/mypy src/agent_eval --ignore-missing-imports

## ── Help ─────────────────────────────────────────────────────────────────────

.PHONY: help
help:  ## Show this help message
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'
