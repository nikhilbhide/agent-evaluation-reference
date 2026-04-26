import os
import asyncio
import logging
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from google.adk.runtime import Runner
from google.adk.services.memory import VertexAiMemoryBankService
from agents.orchestrator.app.agent import orchestrator_agent

# ── Setup Logging ─────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ── Configuration ─────────────────────────────────────────────────────────────
GCP_PROJECT = os.environ.get("GCP_PROJECT", "")
GCP_LOCATION = os.environ.get("GCP_LOCATION", "us-central1")
MEMORY_INDEX_ID = os.environ.get("MEMORY_INDEX_ID", "default-index")

# ── Initialize ADK Components ─────────────────────────────────────────────────
# Memory Bank Service handles long-term fact persistence
memory_service = None
if GCP_PROJECT:
    try:
        memory_service = VertexAiMemoryBankService(
            project_id=GCP_PROJECT,
            location=GCP_LOCATION,
            index_id=MEMORY_INDEX_ID
        )
        logger.info(f"Memory Bank initialized: {MEMORY_INDEX_ID}")
    except Exception as e:
        logger.warning(f"Could not initialize Memory Bank: {e}. Running without memory.")

# Runner orchestrates the agent + memory interaction
runner = Runner(
    agent=orchestrator_agent,
    memory_service=memory_service
)

# ── FastAPI Wrapper ───────────────────────────────────────────────────────────
app = FastAPI(title="ADK Orchestrator Service")

class PredictRequest(BaseModel):
    prompt: str
    session_id: str = "default-session"
    user_id: str = "default-user"

class PredictResponse(BaseModel):
    response: str
    session_id: str

@app.post("/predict")
async def predict(req: PredictRequest):
    try:
        # 1. Query the agent via ADK Runner
        # This handles PreloadMemoryTool automatically if configured in the agent
        response = await runner.query(
            req.prompt,
            session_id=req.session_id,
            user_id=req.user_id
        )
        
        # 2. Extract facts and save to memory bank at the end of the turn
        # In a real high-traffic app, you might do this background/async
        if memory_service:
            await memory_service.add_session_to_memory(session_id=req.session_id)
            
        return PredictResponse(
            response=response.text,
            session_id=req.session_id
        )
    except Exception as e:
        logger.error(f"Prediction error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
async def health():
    return {"status": "alive"}

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
