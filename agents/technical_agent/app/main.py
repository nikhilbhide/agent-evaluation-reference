"""
Technical Agent — handles errors, crashes, API issues via RAG-grounded responses.

KEY DIFFERENCE FROM BILLING AGENT:
  The technical agent uses the search_knowledge_base tool to retrieve
  relevant KB documents BEFORE generating a response. This is RAG in action:
    1. User reports a technical issue.
    2. Agent calls search_knowledge_base("error 500 on startup").
    3. MCP server retrieves the 3 most relevant KB articles.
    4. Gemini uses those articles as grounding context for its answer.
  
  This means responses are grounded in your internal KB, not just Gemini's
  training data. Groundedness is measured in evaluation (runner.py).
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
You are the Technical Support Agent for TechCorp's Customer Resolution Hub.
You ONLY handle technical issues: error codes, app crashes, API problems, performance.

MANDATORY PROCESS for every technical issue:
1. ALWAYS call search_knowledge_base first to find relevant troubleshooting guides.
2. Base your response on the retrieved documents — do not invent solutions.
3. If the KB contains a direct answer, quote the relevant steps.
4. If no relevant KB article is found, escalate: tell the customer a technical team
   member will follow up within 24 hours, and collect their contact email.

GROUNDING RULE: Your response must be traceable to a KB document or you must
explicitly say "I don't have a KB article for this specific issue."
"""

_model: GenerativeModel = None
_tools = None
_ready: bool = False

TECHNICAL_TOOL_NAMES = {"search_knowledge_base"}


def _get_mcp_tools():
    resp = requests.get(f"{MCP_SERVER_URL}/mcp/tools/list", timeout=5)
    resp.raise_for_status()
    declarations = [
        FunctionDeclaration(name=t["name"], description=t["description"], parameters=t["parameters"])
        for t in resp.json()["tools"]
        if t["name"] in TECHNICAL_TOOL_NAMES
    ]
    return Tool(function_declarations=declarations)


def _call_mcp_tool(name: str, arguments: dict) -> dict:
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
        _tools = _get_mcp_tools()
        _model = GenerativeModel(MODEL_NAME, system_instruction=[SYSTEM_INSTRUCTION])
        _ready = True
        logger.info(f"Technical agent ready — version={APP_VERSION}")
    except Exception as e:
        logger.error(f"Technical agent startup failed: {e}")
    yield


app = FastAPI(title="Technical Agent", version=APP_VERSION, lifespan=lifespan)


class PredictRequest(BaseModel):
    prompt: str


class PredictResponse(BaseModel):
    response: str
    version: str
    latency_ms: float
    tools_called: list[str] = []
    kb_docs_retrieved: int = 0


@app.post("/predict", response_model=PredictResponse)
async def predict(req: PredictRequest):
    if not _ready:
        raise HTTPException(status_code=503, detail="Technical agent not ready")

    t0 = time.perf_counter()
    tools_called = []
    kb_docs_retrieved = 0
    chat = _model.start_chat()

    try:
        response = chat.send_message(req.prompt, tools=[_tools])

        for _ in range(3):  # RAG agents rarely need more than 1-2 tool calls
            function_calls = [
                p.function_call for p in response.candidates[0].content.parts if p.function_call
            ]
            if not function_calls:
                break

            tool_results = []
            for fc in function_calls:
                tools_called.append(fc.name)
                logger.info(f"RAG tool call: {fc.name} args={dict(fc.args)}")
                result = _call_mcp_tool(fc.name, dict(fc.args))

                # Track how many KB docs were retrieved for evaluation
                if fc.name == "search_knowledge_base":
                    kb_docs_retrieved += result.get("result", {}).get("result_count", 0)

                tool_results.append(
                    Part.from_function_response(name=fc.name, response={"result": result})
                )

            response = chat.send_message(tool_results)

        latency_ms = (time.perf_counter() - t0) * 1000
        return PredictResponse(
            response=response.text,
            version=APP_VERSION,
            latency_ms=round(latency_ms, 2),
            tools_called=tools_called,
            kb_docs_retrieved=kb_docs_retrieved,
        )

    except Exception as e:
        logger.error(f"Technical agent error: {e}")
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
