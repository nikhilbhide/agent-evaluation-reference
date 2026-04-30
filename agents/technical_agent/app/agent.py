from google.adk.agents import Agent
from google.adk.tools.preload_memory_tool import PreloadMemoryTool
import logging

from agents._shared.config import SPECIALIST_MODEL
from agents._shared.mcp_client import MCPAuthError, MCPCallError, call_tool
from agents._shared.model_armor import make_before_model_callback

logger = logging.getLogger(__name__)


def search_knowledge_base(query: str) -> str:
    """Searches the internal knowledge base for troubleshooting guides and FAQs."""
    logger.info(f"Tool call: search_knowledge_base({query})")
    try:
        return str(call_tool("search_knowledge_base", {"query": query}))
    except MCPAuthError as exc:
        logger.error("MCP auth error on search_knowledge_base: %s", exc)
        return "TOOL_ERROR: technical agent is not authorized to call search_knowledge_base."
    except MCPCallError as exc:
        logger.error("MCP call error on search_knowledge_base: %s", exc)
        return "TOOL_ERROR: knowledge base is currently unavailable."


technical_agent = Agent(
    name="technical_agent",
    model=SPECIALIST_MODEL,
    instruction="""
    You are the Technical Support Agent for TechCorp.
    You handle app crashes, error codes, API issues, and performance questions.

    1. Search the knowledge base for relevant troubleshooting steps.
    2. Provide clear, step-by-step instructions to the user.
    3. If the issue is not found, advise the user that a senior engineer will follow up.

    ERROR HANDLING: If a tool returns a string starting with "TOOL_ERROR:",
    do NOT fabricate troubleshooting content. Apologize and route to a human.
    """,
    description="Specialist for technical issues, crashes, and API troubleshooting.",
    before_model_callback=make_before_model_callback(),
    tools=[PreloadMemoryTool(), search_knowledge_base]
)
