from google.adk.agents import Agent
import requests
import os
import logging

logger = logging.getLogger(__name__)

MCP_SERVER_URL = os.environ.get("MCP_SERVER_URL", "http://mcp-server.agent.svc.cluster.local")

def lookup_account(identifier: str) -> str:
    """Retrieves account information for a customer by email or account ID."""
    logger.info(f"Tool call: lookup_account({identifier})")
    resp = requests.post(
        f"{MCP_SERVER_URL}/mcp/tools/call",
        json={"name": "lookup_account", "arguments": {"identifier": identifier}},
        timeout=10,
    )
    resp.raise_for_status()
    return str(resp.json().get("result"))

account_agent = Agent(
    name="account_agent",
    model="gemini-1.5-flash",
    instruction="""
    You are the Account Management Agent for TechCorp.
    You handle account access, profile updates, and plan changes.
    
    1. Always verify the account status using lookup_account.
    2. Help users with password resets, address updates, or plan migrations.
    3. Be professional and secure.
    """,
    description="Specialist for account access, security, and profile management.",
    tools=[lookup_account]
)
