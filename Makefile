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

## ── Enterprise Lifecycle (Build → Scale → Govern → Optimize) ──────────────

.PHONY: enterprise-deploy
enterprise-deploy:  ## Full enterprise deployment (Build + ABOM + SCC + RE)
	@test -n "$(PROJECT_ID)" || (echo "❌ Set PROJECT_ID" && exit 1)
	$(PYTHON) scripts/deploy_enterprise_agent.py

.PHONY: govern-setup
govern-setup:  ## Setup SCC source, Alerting policies, and Agent Registry
	@test -n "$(GCP_PROJECT)" || (echo "❌ Set GCP_PROJECT" && exit 1)
	$(PYTHON) scripts/setup_alerting.py
	$(PYTHON) scripts/publish_scc_findings.py
	$(PYTHON) scripts/register_agents.py
	$(PYTHON) scripts/register_tools.py

.PHONY: optimize-trigger
optimize-trigger:  ## Trigger automated model tuning based on feedback
	@test -n "$(GCP_PROJECT)" || (echo "❌ Set GCP_PROJECT" && exit 1)
	$(PYTHON) scripts/trigger_tuning.py

## ── Testing ──────────────────────────────────────────────────────────────────

.PHONY: test
test:  ## Run unit tests (no GCP calls, fully mocked)
	$(VENV)/bin/pytest tests/ -v --tb=short

.PHONY: test-coverage
test-coverage:  ## Run tests with coverage report
	$(VENV)/bin/pytest tests/ --cov=src/agent_eval --cov-report=term-missing

## ── Evaluation ───────────────────────────────────────────────────────────────

.PHONY: eval
eval:  ## Run evaluation with mock agent (needs GOOGLE_CLOUD_PROJECT set)
	$(VENV)/bin/agent-eval run-eval \
		--dataset data/golden_dataset.json

.PHONY: eval-live
eval-live:  ## Run evaluation against a live agent (set AGENT_ENDPOINT in .env)
	@test -n "$(AGENT_ENDPOINT)" || (echo "❌ Set AGENT_ENDPOINT first" && exit 1)
	$(VENV)/bin/agent-eval run-eval \
		--dataset data/golden_dataset.json \
		--endpoint $(AGENT_ENDPOINT) \
		--safety-threshold 0.9

## ── Sanity Check ─────────────────────────────────────────────────────────────

.PHONY: sanity
sanity:  ## Run sanity check against AGENT_ENDPOINT (set in .env)
	@test -n "$(AGENT_ENDPOINT)" || (echo "❌ Set AGENT_ENDPOINT first" && exit 1)
	$(PYTHON) scripts/sanity_check.py --endpoint $(AGENT_ENDPOINT)

## ── Docker ───────────────────────────────────────────────────────────────────

.PHONY: deploy-adk
deploy-adk:  ## Deploy the ADK agent to Vertex AI Agent Engine
	@test -n "$(GCP_PROJECT)" || (echo "❌ Set GCP_PROJECT" && exit 1)
	@test -n "$(GCP_STAGING_BUCKET)" || (echo "❌ Set GCP_STAGING_BUCKET" && exit 1)
	$(PYTHON) scripts/deploy_agent_engine.py

.PHONY: patch-adk-labels
patch-adk-labels:  ## Apply ADK playground labels to a deployed agent (reads deployed_agent_resource.txt if RESOURCE unset)
	@GCP_PROJECT=$(GCP_PROJECT) GCP_LOCATION=$(GCP_LOCATION) \
	$(PYTHON) scripts/patch_agent_labels.py $(RESOURCE)

.PHONY: run-adk-local
run-adk-local:  ## Run the ADK orchestrator locally
	export GCP_PROJECT=$(GOOGLE_CLOUD_PROJECT) && \
	export MCP_SERVER_URL=http://localhost:8081 && \
	$(PYTHON) agents/orchestrator/app/main_adk.py
	docker build \
		--build-arg APP_VERSION=local-dev \
		-t customer-resolution-agent:local .

.PHONY: docker-run
docker-run:  ## Run the agent container locally on port 8080
	docker run --rm -p 8080:8080 \
		-e GCP_PROJECT=$(GOOGLE_CLOUD_PROJECT) \
		-e GOOGLE_APPLICATION_CREDENTIALS=/tmp/creds.json \
		-v $(HOME)/.config/gcloud/application_default_credentials.json:/tmp/creds.json:ro \
		customer-resolution-agent:local

## ── GCP / GKE ────────────────────────────────────────────────────────────────

.PHONY: gcp-setup
gcp-setup:  ## One-time GCP setup (cluster, IAM, Workload Identity)
	@test -n "$(PROJECT_ID)"    || (echo "❌ Set PROJECT_ID"    && exit 1)
	@test -n "$(CLUSTER_NAME)"  || (echo "❌ Set CLUSTER_NAME"  && exit 1)
	@test -n "$(ZONE)"          || (echo "❌ Set ZONE"           && exit 1)
	chmod +x scripts/setup_gcp.sh
	./scripts/setup_gcp.sh $(PROJECT_ID) $(CLUSTER_NAME) $(ZONE)

.PHONY: canary-enable
canary-enable:  ## Enable 20% real traffic to canary (Phase 1 → Phase 2)
	@test -n "$(CANARY_TAG)" || (echo "❌ Set CANARY_TAG" && exit 1)
	chmod +x scripts/enable_canary_traffic.sh
	./scripts/enable_canary_traffic.sh $(CANARY_TAG)

.PHONY: canary-promote
canary-promote:  ## Promote canary to stable (Phase 2 → Phase 3)
	@test -n "$(CANARY_TAG)"  || (echo "❌ Set CANARY_TAG"  && exit 1)
	@test -n "$(PROJECT_ID)"  || (echo "❌ Set PROJECT_ID"  && exit 1)
	chmod +x scripts/promote_canary.sh
	./scripts/promote_canary.sh $(CANARY_TAG) $(PROJECT_ID)

.PHONY: canary-rollback
canary-rollback:  ## Rollback canary at any phase
	chmod +x scripts/rollback_canary.sh
	./scripts/rollback_canary.sh

## ── Linting ──────────────────────────────────────────────────────────────────

.PHONY: lint
lint:  ## Run mypy type checker
	$(VENV)/bin/mypy src/agent_eval --ignore-missing-imports

## ── Help ─────────────────────────────────────────────────────────────────────

.PHONY: help
help:  ## Show this help message
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'
