# ── MCP Server Dockerfile ────────────────────────────────────────────────────
FROM python:3.11-slim as builder

WORKDIR /app
COPY mcp_server/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

FROM python:3.11-slim
WORKDIR /app

# Create non-root user for security
RUN groupadd -r mcp && useradd -r -g mcp mcp
USER mcp

COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy MCP server code
COPY mcp_server/app/ /app/app/

EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "2"]
