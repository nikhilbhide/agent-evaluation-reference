# Multi-stage Docker build for the Customer Resolution Agent
#
# Stage 1 (builder): Install Python dependencies in isolation.
#   - Keeps the final image lean by not including pip, build tools, etc.
#
# Stage 2 (runtime): Only copy what's needed to run the app.
#   - Non-root user for security (GKE security policies often require this).
#   - Explicitly set port for Cloud Build / GKE discovery.

# ── Stage 1: builder ──────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

COPY agent/requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ── Stage 2: runtime ──────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

# APP_VERSION is injected by Cloud Build at build time:
#   docker build --build-arg APP_VERSION=$SHORT_SHA .
# This value is exposed via the /version endpoint and logged on startup,
# making it easy to confirm which image version a canary pod is running.
ARG APP_VERSION=dev
ENV APP_VERSION=${APP_VERSION}
ENV GCP_LOCATION=us-central1
ENV MODEL_NAME=gemini-2.5-flash

# Security: run as non-root
RUN adduser --disabled-password --gecos "" appuser
USER appuser

WORKDIR /app

# Copy installed packages from builder stage
COPY --from=builder /install /usr/local

# Copy the agent application source
COPY agent/app /app/app

EXPOSE 8080

# Uvicorn with:
#   --workers 1       — one worker per pod (let K8s handle scaling via HPA)
#   --timeout-keep-alive 65 — matches GKE load balancer timeout (60s default + buffer)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", \
     "--workers", "1", "--timeout-keep-alive", "65"]
