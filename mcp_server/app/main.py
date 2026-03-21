"""
MCP Server — Tool Execution Layer for the Customer Resolution Hub.

WHY AN MCP SERVER?
  The Model Context Protocol (MCP) gives all sub-agents a single, versioned
  endpoint to call tools from. Benefits:
    - Tools are deployed, scaled, and monitored INDEPENDENTLY of the agents.
    - Changing a tool implementation requires no agent redeployment.
    - All tool calls are logged centrally — full audit trail.
    - Sub-agents don't need DB credentials; the MCP server holds them.

PROTOCOL:
  POST /mcp/tools/list   — returns all available tool definitions
  POST /mcp/tools/call   — executes a named tool, returns structured result
  GET  /health           — liveness probe
  GET  /ready            — readiness probe

TOOL CALL FLOW (how an agent uses this):
  1. Agent receives a user prompt.
  2. Agent sends prompt + tool definitions (from /mcp/tools/list) to Gemini.
  3. Gemini decides to call a tool, returns a function_calls response.
  4. Agent POSTs function name + args to /mcp/tools/call.
  5. MCP server executes the tool (DB query, RAG retrieval, etc.).
  6. Agent sends tool result back to Gemini for final response generation.
"""

import logging
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Any

from app.tools.billing import issue_refund, lookup_invoice
from app.tools.account import lookup_account, lookup_transaction
from app.tools.knowledge_base import search_knowledge_base

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="MCP Tool Server", version="1.0.0")


# ── Tool Registry ──────────────────────────────────────────────────────────────
# Each tool declares its input schema so agents can pass it to Gemini's
# function calling API without hardcoding the schema in each agent.
TOOL_REGISTRY = {
    "issue_refund": {
        "description": "Issues a refund for a given invoice. Use when the user reports a billing error or double charge.",
        "parameters": {
            "type": "object",
            "properties": {
                "invoice_id": {"type": "string", "description": "The invoice ID to refund (e.g. INV-12345)"},
                "reason":     {"type": "string", "description": "Reason for the refund"},
                "amount":     {"type": "number", "description": "Amount to refund in USD. If omitted, full invoice amount is refunded."}
            },
            "required": ["invoice_id", "reason"]
        },
        "fn": issue_refund,
    },
    "lookup_invoice": {
        "description": "Looks up details of an invoice by ID.",
        "parameters": {
            "type": "object",
            "properties": {
                "invoice_id": {"type": "string", "description": "The invoice ID to look up"},
            },
            "required": ["invoice_id"]
        },
        "fn": lookup_invoice,
    },
    "lookup_account": {
        "description": "Retrieves account information for a customer by email or account ID.",
        "parameters": {
            "type": "object",
            "properties": {
                "identifier": {"type": "string", "description": "Customer email or account ID"},
            },
            "required": ["identifier"]
        },
        "fn": lookup_account,
    },
    "lookup_transaction": {
        "description": "Retrieves recent transactions for a customer account.",
        "parameters": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string", "description": "Account ID to look up transactions for"},
                "limit": {"type": "integer", "description": "Number of recent transactions to return (default: 5)"},
            },
            "required": ["account_id"]
        },
        "fn": lookup_transaction,
    },
    "search_knowledge_base": {
        "description": "Searches the internal knowledge base for troubleshooting guides, FAQs, and policy documents. Use for technical issues or policy questions.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The user's issue or question to search for"},
                "top_k": {"type": "integer", "description": "Number of relevant documents to return (default: 3)"},
            },
            "required": ["query"]
        },
        "fn": search_knowledge_base,
    },
}


# ── Request / Response schemas ─────────────────────────────────────────────────
class ToolCallRequest(BaseModel):
    name: str
    arguments: dict[str, Any] = {}


class ToolCallResponse(BaseModel):
    name: str
    result: Any
    error: str | None = None


# ── Endpoints ──────────────────────────────────────────────────────────────────
@app.post("/mcp/tools/list")
async def list_tools():
    """
    Returns all available tool definitions in Gemini function-calling format.
    Agents call this once at startup (or cache it) to get tool schemas.
    """
    tools = []
    for name, meta in TOOL_REGISTRY.items():
        tools.append({
            "name": name,
            "description": meta["description"],
            "parameters": meta["parameters"],
        })
    logger.info(f"Returning {len(tools)} tool definitions")
    return {"tools": tools}


@app.post("/mcp/tools/call", response_model=ToolCallResponse)
async def call_tool(req: ToolCallRequest):
    """
    Executes a named tool with the provided arguments.
    Called by agents when Gemini's function calling returns a tool invocation.
    """
    if req.name not in TOOL_REGISTRY:
        raise HTTPException(
            status_code=404,
            detail=f"Tool '{req.name}' not found. Available: {list(TOOL_REGISTRY.keys())}"
        )

    tool_fn = TOOL_REGISTRY[req.name]["fn"]
    logger.info(f"Executing tool: {req.name} args={req.arguments}")

    try:
        result = tool_fn(**req.arguments)
        logger.info(f"Tool {req.name} succeeded")
        return ToolCallResponse(name=req.name, result=result)
    except TypeError as e:
        logger.error(f"Tool {req.name} called with wrong arguments: {e}")
        raise HTTPException(status_code=400, detail=f"Invalid arguments for tool '{req.name}': {e}")
    except Exception as e:
        logger.error(f"Tool {req.name} execution error: {e}")
        return ToolCallResponse(name=req.name, result=None, error=str(e))


@app.get("/health")
async def health():
    return {"status": "alive"}


@app.get("/ready")
async def ready():
    return {"status": "ready", "tools": list(TOOL_REGISTRY.keys())}
