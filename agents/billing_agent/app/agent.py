from google.adk.agents import Agent
from google.adk.tools.preload_memory_tool import PreloadMemoryTool
import logging

from agents._shared.config import SPECIALIST_MODEL
from agents._shared.mcp_client import MCPAuthError, MCPCallError, call_tool
from agents._shared.model_armor import make_before_model_callback

logger = logging.getLogger(__name__)


def lookup_invoice(invoice_id: str) -> str:
    """Looks up details of an invoice by ID."""
    logger.info(f"Tool call: lookup_invoice({invoice_id})")
    try:
        return str(call_tool("lookup_invoice", {"invoice_id": invoice_id}))
    except MCPAuthError as exc:
        logger.error("MCP auth error on lookup_invoice: %s", exc)
        return (
            "TOOL_ERROR: billing agent is not authorized to call lookup_invoice. "
            "Tell the user this request needs human assistance."
        )
    except MCPCallError as exc:
        logger.error("MCP call error on lookup_invoice: %s", exc)
        return (
            "TOOL_ERROR: invoice lookup is currently unavailable. "
            "Tell the user this request needs human assistance."
        )


def issue_refund(invoice_id: str, reason: str, amount: float = None) -> str:
    """Issues a refund for a given invoice."""
    logger.info(f"Tool call: issue_refund({invoice_id}, {reason}, {amount})")
    args = {"invoice_id": invoice_id, "reason": reason}
    if amount is not None:
        args["amount"] = amount
    try:
        return str(call_tool("issue_refund", args))
    except MCPAuthError as exc:
        logger.error("MCP auth error on issue_refund: %s", exc)
        return (
            "TOOL_ERROR: billing agent is not authorized to call issue_refund. "
            "Refund was NOT processed. Tell the user a human will follow up."
        )
    except MCPCallError as exc:
        logger.error("MCP call error on issue_refund: %s", exc)
        return (
            "TOOL_ERROR: refund service is currently unavailable. "
            "Refund was NOT processed. Tell the user a human will follow up."
        )


billing_agent = Agent(
    name="billing_agent",
    model=SPECIALIST_MODEL,
    instruction="""
    You are the Billing Agent for TechCorp's Customer Resolution Hub.
    You ONLY handle billing issues: refunds, charges, invoices, payment failures.

    When a customer reports a billing issue:
    1. Look up their invoice using lookup_invoice.
    2. If it is a duplicate charge or error, issue a refund using issue_refund.
    3. Always tell the customer: the refund ID, the amount, and the timeline (3-5 days).
    4. Be empathetic. Acknowledge the inconvenience before taking action.

    SAFETY: Never issue a refund without first verifying the invoice exists.

    ERROR HANDLING: If a tool returns a string starting with "TOOL_ERROR:",
    do NOT fabricate invoice data or refund IDs. Apologize, explain the
    system is temporarily unavailable, and route to a human.
    """,
    description="Specialist for refunds, charges, invoices, and payment issues.",
    before_model_callback=make_before_model_callback(),
    tools=[PreloadMemoryTool(), lookup_invoice, issue_refund]
)
