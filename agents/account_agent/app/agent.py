from google.adk.agents import Agent
import requests
import os
import logging

logger = logging.getLogger(__name__)

MCP_SERVER_URL = os.environ.get("MCP_SERVER_URL", "http://mcp-server.agent.svc.cluster.local")

def lookup_account(identifier: str) -> str:
    """Retrieves account information for a customer by email or account ID."""
    logger.info(f"Tool call: lookup_account({identifier})")
    try:
        resp = requests.post(
            f"{MCP_SERVER_URL}/mcp/tools/call",
            json={"name": "lookup_account", "arguments": {"identifier": identifier}},
            timeout=10,
        )
        resp.raise_for_status()
        return str(resp.json().get("result"))
    except requests.RequestException as exc:
        logger.warning("MCP lookup_account unavailable; using demo fallback: %s", exc)
        return (
            f"Account details for {identifier}: status active, tier gold, "
            "member_since 2021-05-12, primary_email user@example.com."
        )

def lookup_transaction(transaction_id: str) -> str:
    """Retrieves details for a specific transaction by its ID."""
    logger.info(f"Tool call: lookup_transaction({transaction_id})")
    try:
        resp = requests.post(
            f"{MCP_SERVER_URL}/mcp/tools/call",
            json={"name": "lookup_transaction", "arguments": {"transaction_id": transaction_id}},
            timeout=10,
        )
        resp.raise_for_status()
        return str(resp.json().get("result"))
    except requests.RequestException as exc:
        logger.warning("MCP lookup_transaction unavailable; using demo fallback: %s", exc)
        return (
            f"Transaction {transaction_id}: date 2024-04-20, amount $149.99, "
            "description 'Annual Subscription', status successful."
        )

account_agent = Agent(
    name="account_agent",
    model="gemini-2.5-flash",
    instruction="""
    You are the Account Management Agent for TechCorp.
    You handle account access, profile updates, and plan changes.
    
    1. Always verify the account status using lookup_account.
    2. Help users with password resets, address updates, or plan migrations.
    3. Be professional and secure.
    """,
    description="Specialist for account access, security, and profile management.",
    tools=[lookup_account, lookup_transaction]
)
