"""
Account Agent — handles user profile, settings, and address changes.

ROUTING: Handles all account_agent requests from the Orchestrator.
TOOL: Uses lookup_account and lookup_transaction through the MCP server.
"""

import os
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

GCP_PROJECT    = os.environ.get("GCP_PROJECT", "")
GCP_LOCATION   = os.environ.get("GCP_LOCATION", "us-central1")
MODEL_NAME     = os.environ.get("MODEL_NAME", "gemini-2.5-flash")
APP_VERSION    = os.environ.get("APP_VERSION", "unknown")
MCP_SERVER_URL = os.environ.get("MCP_SERVER_URL", "http://mcp-server.agent.svc.cluster.local")

SYSTEM_INSTRUCTION = """
You are the Account Specialist Agent for TechCorp.
You handle all account-related queries: status, reactivation, plan details, 
and profile updates like names or billing addresses.

When a customer asks about their account:
1. ALWAYS use lookup_account first with their email or account ID.
2. If they are suspended, explain why (usually 3 failed payments).
3. If they want to change an address, confirm the change after lookup.
4. If they need reactivation, tell them it takes 24h after payment.
"""

_model: GenerativeModel = None
_tools = None
_ready: bool = False

ACCOUNT_TOOL_NAMES = {"lookup_account", "lookup_transaction"}


def _get_mcp_tools():
    resp = requests.get(f"{MCP_SERVER_URL}/mcp/tools/list", timeout=5)
    resp.raise_for_status()
    declarations = [
        FunctionDeclaration(name=t["name"], description=t["description"], parameters=t["parameters"])
        for t in resp.json()["tools"]
        if t["name"] in ACCOUNT_TOOL_NAMES
    ]
    return Tool(function_declarations=declarations)


def _call_mcp_tool(name: str, arguments: dict) -> dict:
    resp = requests.post(f"{MCP_SERVER_URL}/mcp/tools/call", json={"name": name, "arguments": arguments}, timeout=10)
    resp.raise_for_status()
    return resp.json()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _model, _tools, _ready
    try:
        vertexai.init(project=GCP_PROJECT, location=GCP_LOCATION)
        _tools = _get_mcp_tools()
        _model = GenerativeModel(MODEL_NAME, system_instruction=[SYSTEM_INSTRUCTION])
        _ready = True
        logger.info(f"Account agent ready — version={APP_VERSION}")
    except Exception as e:
        logger.error(f"Account agent startup failed: {e}")
    yield


app = FastAPI(title="Account Agent", version=APP_VERSION, lifespan=lifespan)


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
        raise HTTPException(status_code=503, detail="Account agent not ready")

    t0 = time.perf_counter()
    tools_called = []
    chat = _model.start_chat()

    try:
        response = chat.send_message(req.prompt, tools=[_tools])
        for _ in range(3):
            fc = [p.function_call for p in response.candidates[0].content.parts if p.function_call]
            if not fc: break
            
            tool_results = []
            for call in fc:
                tools_called.append(call.name)
                result = _call_mcp_tool(call.name, dict(call.args))
                tool_results.append(Part.from_function_response(name=call.name, response={"result": result}))
            response = chat.send_message(tool_results)

        return PredictResponse(
            response=response.text, 
            version=APP_VERSION, 
            latency_ms=round((time.perf_counter() - t0) * 1000, 2),
            tools_called=tools_called
        )
    except Exception as e:
        logger.error(f"Account agent error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
async def health(): return {"status": "alive", "version": APP_VERSION}

@app.get("/ready")
async def ready(): 
    if not _ready: return JSONResponse(status_code=503, content={"status": "not_ready"})
    return {"status": "ready", "version": APP_VERSION}
