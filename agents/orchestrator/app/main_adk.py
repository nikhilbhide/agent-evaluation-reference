import os
import asyncio
import logging
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from google.adk.runners import Runner
from google.adk.memory import VertexAiMemoryBankService
from google.adk.sessions import InMemorySessionService
from agents.orchestrator.app.agent import orchestrator_agent

# ── Setup Logging ─────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="ADK Agent Gateway", version="1.0.0")

# ── Global ADK Runner ──────────────────────────────────────────────────────────
runner = None

def init_runner():
    global runner
    project = os.environ.get("GCP_PROJECT")
    location = os.environ.get("GCP_LOCATION", "us-central1")
    
    logger.info(f"🚀 Initializing ADK Runner (project={project}, location={location})")
    
    try:
        # Initialize Memory Bank for RAG (if needed)
        memory_bank = VertexAiMemoryBankService(
            project=project,
            location=location
        )
        
        # Initialize the Runner with the orchestrator agent
        runner = Runner(
            agent=orchestrator_agent,
            app_name="CustomerResolutionHub",
            session_service=InMemorySessionService(),
            memory_service=memory_bank
        )
        logger.info("✅ ADK Runner initialized successfully.")
    except Exception as e:
        logger.error(f"❌ Failed to initialize ADK Runner: {e}")
        # Fallback to runner without memory if initialization fails
        runner = Runner(
            agent=orchestrator_agent,
            app_name="CustomerResolutionHub",
            session_service=InMemorySessionService()
        )

@app.on_event("startup")
async def startup_event():
    init_runner()

# ── API Models ────────────────────────────────────────────────────────────────
class QueryRequest(BaseModel):
    prompt: str
    session_id: str = "default-session"

class QueryResponse(BaseModel):
    response: str

# ── Endpoints ──────────────────────────────────────────────────────────────────
@app.post("/predict", response_model=QueryResponse)
async def predict(req: QueryRequest):
    if not runner:
        raise HTTPException(status_code=500, detail="ADK Runner not initialized")

    logger.info(f"📥 Received query: {req.prompt[:50]}... (session={req.session_id})")
    
    try:
        result = await asyncio.to_thread(
            runner.run,
            input=req.prompt,
            session_id=req.session_id
        )
        response_text = result.text if hasattr(result, "text") else str(result)
        logger.info(f"📤 Agent response: {response_text[:50]}...")
        return QueryResponse(response=response_text)
    except Exception as e:
        logger.error(f"💥 Error during agent execution: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
async def health():
    return {"status": "alive"}

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
