"""
Billing Agent — handles all charge, refund, and invoice queries.

TOOL PATTERN (Gemini → MCP server):
  1. Receives prompt from orchestrator
  2. Forms tool call args (e.g. issue_refund + invoice_id)
  3. Calls MCP server at /mcp/tools/call
  4. Uses tool result to compose a helpful, accurate response
"""
import os
import json
import time
import logging
from contextlib import asynccontextmanager

import requests
import vertexai
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from vertexai.generative_models import GenerativeModel, Tool, FunctionDeclaration, Part

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

GCP_PROJECT   = os.environ.get("GCP_PROJECT", "")
GCP_LOCATION  = os.environ.get("GCP_LOCATION", "us-central1")
MODEL_NAME    = os.environ.get("MODEL_NAME", "gemini-2.5-flash")
APP_VERSION   = os.environ.get("APP_VERSION", "unknown")
MCP_SERVER_URL = os.environ.get("MCP_SERVER_URL", "http://mcp-server.agent.svc.cluster.local")

SYSTEM_INSTRUCTION = """
You are the Billing Agent for TechCorp's Customer Resolution Hub.
You ONLY handle billing issues: refunds, charges, invoices, payment failures.

When a customer reports a billing issue:
1. Look up their invoice using lookup_invoice.
2. If it is a duplicate charge, issue a refund using issue_refund.
3. Always tell the customer: the refund ID, the amount, and the timeline (3-5 days).
4. Be empathetic. Acknowledge the inconvenience before taking action.

SAFETY: Never issue a refund without first verifying the invoice exists.
"""

_model: GenerativeModel = None
_tools: list = None
_ready: bool = False


def _get_mcp_tools() -> Tool:
    """Fetch tool schemas from MCP server and convert to Gemini Tool format."""
    resp = requests.get(f"{MCP_SERVER_URL}/mcp/tools/list", timeout=5)
    resp.raise_for_status()
    # Filter to only billing-relevant tools
    billing_tool_names = {"issue_refund", "lookup_invoice", "lookup_account", "lookup_transaction"}
    declarations = [
        FunctionDeclaration(
            name=t["name"],
            description=t["description"],
            parameters=t["parameters"],
        )
        for t in resp.json()["tools"]
        if t["name"] in billing_tool_names
    ]
    return Tool(function_declarations=declarations)


def _call_mcp_tool(name: str, arguments: dict) -> dict:
    """Execute a tool via the MCP server."""
    resp = requests.post(
        f"{MCP_SERVER_URL}/mcp/tools/call",
        json={"name": name, "arguments": arguments},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _model, _tools, _ready
    try:
        vertexai.init(project=GCP_PROJECT, location=GCP_LOCATION)
        # Fetch tool schemas from MCP server at startup
        # (these are used in every generate_content call)
        _tools = _get_mcp_tools()
        _model = GenerativeModel(MODEL_NAME, system_instruction=[SYSTEM_INSTRUCTION])
        _ready = True
        logger.info(f"Billing agent ready — version={APP_VERSION}")
    except Exception as e:
        logger.error(f"Billing agent startup failed: {e}")
    yield


app = FastAPI(title="Billing Agent", version=APP_VERSION, lifespan=lifespan)


class PredictRequest(BaseModel):
    prompt: str


class PredictResponse(BaseModel):
    response: str
    version: str
    latency_ms: float
    tools_called: list[str] = []


@app.post("/predict", response_model=PredictResponse)
async def predict(req: PredictRequest):
    if not _ready:
        raise HTTPException(status_code=503, detail="Billing agent not ready")

    t0 = time.perf_counter()
    tools_called = []
    chat = _model.start_chat()

    # ── Agentic tool-use loop ──────────────────────────────────────────────────
    # 1. Send user prompt + tools to Gemini
    # 2. If Gemini calls a tool → execute via MCP server → send result back
    # 3. Repeat until Gemini returns a text response (no more tool calls)
    try:
        response = chat.send_message(req.prompt, tools=[_tools])

        for _ in range(5):  # max 5 tool calls per request (prevents infinite loops)
            function_calls = [
                p.function_call
                for p in response.candidates[0].content.parts
                if p.function_call
            ]
            if not function_calls:
                break  # Gemini is done calling tools — it has a final text answer

            # Execute each tool call via MCP server
            tool_results = []
            for fc in function_calls:
                tools_called.append(fc.name)
                logger.info(f"Calling MCP tool: {fc.name} args={dict(fc.args)}")
                result = _call_mcp_tool(fc.name, dict(fc.args))
                tool_results.append(
                    Part.from_function_response(
                        name=fc.name,
                        response={"result": result},
                    )
                )

            # Feed tool results back to Gemini for the next turn
            response = chat.send_message(tool_results)

        final_text = response.text
        latency_ms = (time.perf_counter() - t0) * 1000
        return PredictResponse(
            response=final_text,
            version=APP_VERSION,
            latency_ms=round(latency_ms, 2),
            tools_called=tools_called,
        )

    except Exception as e:
        logger.error(f"Billing agent error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
async def health():
    return {"status": "alive", "version": APP_VERSION}


@app.get("/ready")
async def ready():
    if not _ready:
        return JSONResponse(status_code=503, content={"status": "not_ready"})
    return {"status": "ready", "version": APP_VERSION}


@app.get("/version")
async def version():
    return {"version": APP_VERSION, "model": MODEL_NAME}
