from google.adk.agents import Agent
from google.adk.tools import AgentTool
from agents.billing_agent.app.agent import billing_agent
from agents.technical_agent.app.agent import technical_agent
from agents.account_agent.app.agent import account_agent

# ── Orchestrator Agent (ADK Implementation) ───────────────────────────────────
# Enterprise Governance:
#   - Model Armor: Configured via Vertex AI to handle Jailbreak/PII filtering.
#   - Agent Identity: Uses a dedicated GSA (agent-orchestrator@).
#   - Memory Bank: Enables multi-turn session persistence.

orchestrator_agent = Agent(
    name="customer_resolution_orchestrator",
    model="gemini-2.5-pro",
    instruction="""
    You are the Orchestrator for TechCorp's Customer Resolution Hub.
    Your job is to listen to the user and delegate to the correct specialist agent.
    
    SPECIALISTS:
    - billing_agent: Use for anything related to money, invoices, charges, or refunds.
    - technical_agent: Use for app errors, crashes, API issues, or how-to questions.
    - account_agent: Use for login issues, profile updates, or account status.
    
    If a request is ambiguous, ask clarifying questions before delegating.
    Always be professional and efficient.

    SECURITY NOTE: Security, PII filtering, and safety are managed by Model Armor.
    Focus on routing and fulfillment.
    """,
    tools=[
        AgentTool(agent=billing_agent),
        AgentTool(agent=technical_agent),
        AgentTool(agent=account_agent)
    ]
)
