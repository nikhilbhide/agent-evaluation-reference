from google.adk.agents import Agent
import requests
import os
import logging

logger = logging.getLogger(__name__)

MCP_SERVER_URL = os.environ.get("MCP_SERVER_URL", "http://mcp-server.agent.svc.cluster.local")

def search_knowledge_base(query: str) -> str:
    """Searches the internal knowledge base for troubleshooting guides and FAQs."""
    logger.info(f"Tool call: search_knowledge_base({query})")
    try:
        resp = requests.post(
            f"{MCP_SERVER_URL}/mcp/tools/call",
            json={"name": "search_knowledge_base", "arguments": {"query": query}},
            timeout=10,
        )
        resp.raise_for_status()
        return str(resp.json().get("result"))
    except requests.RequestException as exc:
        logger.warning("MCP search_knowledge_base unavailable; using demo fallback: %s", exc)
        return (
            "KNOWLEDGE BASE FALLBACK: \n"
            "1. Check if the service is down on the status page.\n"
            "2. Clear browser cache and cookies.\n"
            "3. Ensure your API key is valid and has not expired."
        )

technical_agent = Agent(
    name="technical_agent",
    model="gemini-2.5-flash",
    instruction="""
    You are the Technical Support Agent for TechCorp.
    You handle app crashes, error codes, API issues, and performance questions.
    
    1. Search the knowledge base for relevant troubleshooting steps.
    2. Provide clear, step-by-step instructions to the user.
    3. If the issue is not found, advise the user that a senior engineer will follow up.
    """,
    description="Specialist for technical issues, crashes, and API troubleshooting.",
    tools=[search_knowledge_base]
)
