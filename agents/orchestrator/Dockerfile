# ── Orchestrator Dockerfile ──────────────────────────────────────────────────
FROM python:3.11-slim as builder

WORKDIR /app
COPY agents/orchestrator/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

FROM python:3.11-slim
WORKDIR /app

RUN groupadd -r agent && useradd -r -g agent agent
USER agent

COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

COPY agents/orchestrator/app/ /app/app/

# Injected by CI
ARG APP_VERSION=local
ENV APP_VERSION=$APP_VERSION

EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "2", "--timeout-keep-alive", "65"]
