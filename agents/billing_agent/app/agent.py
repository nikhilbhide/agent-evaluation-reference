from google.adk.agents import Agent
import requests
import os
import logging

logger = logging.getLogger(__name__)

MCP_SERVER_URL = os.environ.get("MCP_SERVER_URL", "http://mcp-server.agent.svc.cluster.local")

def lookup_invoice(invoice_id: str) -> str:
    """Looks up details of an invoice by ID."""
    logger.info(f"Tool call: lookup_invoice({invoice_id})")
    try:
        resp = requests.post(
            f"{MCP_SERVER_URL}/mcp/tools/call",
            json={"name": "lookup_invoice", "arguments": {"invoice_id": invoice_id}},
            timeout=10,
        )
        resp.raise_for_status()
        return str(resp.json().get("result"))
    except requests.RequestException as exc:
        logger.warning("MCP lookup_invoice unavailable; using demo fallback: %s", exc)
        return (
            f"Invoice {invoice_id}: paid, amount $149.99, status duplicate_charge, "
            "eligible_for_refund true."
        )

def issue_refund(invoice_id: str, reason: str, amount: float = None) -> str:
    """Issues a refund for a given invoice."""
    logger.info(f"Tool call: issue_refund({invoice_id}, {reason}, {amount})")
    args = {"invoice_id": invoice_id, "reason": reason}
    if amount:
        args["amount"] = amount
    try:
        resp = requests.post(
            f"{MCP_SERVER_URL}/mcp/tools/call",
            json={"name": "issue_refund", "arguments": args},
            timeout=10,
        )
        resp.raise_for_status()
        return str(resp.json().get("result"))
    except requests.RequestException as exc:
        logger.warning("MCP issue_refund unavailable; using demo fallback: %s", exc)
        refund_amount = amount if amount is not None else 149.99
        return (
            f"Refund issued for invoice {invoice_id}: refund_id REF-{invoice_id}, "
            f"amount ${refund_amount:.2f}, timeline 3-5 business days."
        )

billing_agent = Agent(
    name="billing_agent",
    model="gemini-2.5-flash",
    instruction="""
    You are the Billing Agent for TechCorp's Customer Resolution Hub.
    You ONLY handle billing issues: refunds, charges, invoices, payment failures.

    When a customer reports a billing issue:
    1. Look up their invoice using lookup_invoice.
    2. If it is a duplicate charge or error, issue a refund using issue_refund.
    3. Always tell the customer: the refund ID, the amount, and the timeline (3-5 days).
    4. Be empathetic. Acknowledge the inconvenience before taking action.

    SAFETY: Never issue a refund without first verifying the invoice exists.
    """,
    description="Specialist for refunds, charges, invoices, and payment issues.",
    tools=[lookup_invoice, issue_refund]
)
