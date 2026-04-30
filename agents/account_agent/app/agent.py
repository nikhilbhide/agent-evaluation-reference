from google.adk.agents import Agent
from google.adk.tools.preload_memory_tool import PreloadMemoryTool
import logging

from agents._shared.config import SPECIALIST_MODEL
from agents._shared.mcp_client import MCPAuthError, MCPCallError, call_tool
from agents._shared.model_armor import make_before_model_callback

logger = logging.getLogger(__name__)


def lookup_account(identifier: str) -> str:
    """Retrieves account information for a customer by email or account ID."""
    logger.info(f"Tool call: lookup_account({identifier})")
    try:
        return str(call_tool("lookup_account", {"identifier": identifier}))
    except MCPAuthError as exc:
        logger.error("MCP auth error on lookup_account: %s", exc)
        return "TOOL_ERROR: account agent is not authorized to call lookup_account."
    except MCPCallError as exc:
        logger.error("MCP call error on lookup_account: %s", exc)
        return "TOOL_ERROR: account lookup is currently unavailable."


def lookup_transaction(transaction_id: str) -> str:
    """Retrieves details for a specific transaction by its ID."""
    logger.info(f"Tool call: lookup_transaction({transaction_id})")
    try:
        return str(call_tool("lookup_transaction", {"transaction_id": transaction_id}))
    except MCPAuthError as exc:
        logger.error("MCP auth error on lookup_transaction: %s", exc)
        return "TOOL_ERROR: account agent is not authorized to call lookup_transaction."
    except MCPCallError as exc:
        logger.error("MCP call error on lookup_transaction: %s", exc)
        return "TOOL_ERROR: transaction lookup is currently unavailable."


account_agent = Agent(
    name="account_agent",
    model=SPECIALIST_MODEL,
    instruction="""
    You are the Account Management Agent for TechCorp.
    You handle account access, profile updates, and plan changes.

    1. Always verify the account status using lookup_account.
    2. Help users with password resets, address updates, or plan migrations.
    3. Be professional and secure.

    ERROR HANDLING: If a tool returns a string starting with "TOOL_ERROR:",
    do NOT fabricate account data. Apologize and route to a human.
    """,
    description="Specialist for account access, security, and profile management.",
    before_model_callback=make_before_model_callback(),
    tools=[PreloadMemoryTool(), lookup_account, lookup_transaction]
)
