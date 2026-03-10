import os
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from crewai import Crew, Task, Process

# --- The AI Phone Guy ---
from agents.aiphoneguy.alex import alex
from agents.aiphoneguy.tyler import tyler
from agents.aiphoneguy.zoe import zoe
from agents.aiphoneguy.jennifer import jennifer

# --- Calling Digital ---
from agents.callingdigital.dek import dek
from agents.callingdigital.marcus import marcus
from agents.callingdigital.sofia import sofia
from agents.callingdigital.carlos import carlos
from agents.callingdigital.nova import nova

# --- Automotive Intelligence ---
from agents.autointelligence.michael_mata import michael_mata
from agents.autointelligence.ryan_data import ryan_data
from agents.autointelligence.chase import chase
from agents.autointelligence.atlas import atlas
from agents.autointelligence.phoenix import phoenix

# ── Agent Registry ─────────────────────────────────────────────────────────────
AGENTS = {
    # The AI Phone Guy
    "alex":     alex,
    "tyler":    tyler,
    "zoe":      zoe,
    "jennifer": jennifer,
    # Calling Digital
    "dek":      dek,
    "marcus":   marcus,
    "sofia":    sofia,
    "carlos":   carlos,
    "nova":     nova,
    # Automotive Intelligence
    "michael-mata": michael_mata,
    "ryan-data":    ryan_data,
    "chase":        chase,
    "atlas":        atlas,
    "phoenix":      phoenix,
}

BUSINESSES = {
    "aiphoneguy": {
        "name": "The AI Phone Guy",
        "agents": ["alex", "tyler", "zoe", "jennifer"]
    },
    "callingdigital": {
        "name": "Calling Digital",
        "agents": ["dek", "marcus", "sofia", "carlos", "nova"]
    },
    "autointelligence": {
        "name": "Automotive Intelligence",
        "agents": ["michael-mata", "ryan-data", "chase", "atlas", "phoenix"]
    },
}

# ── FastAPI App ────────────────────────────────────────────────────────────────
app = FastAPI(
    title="Paperclip",
    description="AI Agent Infrastructure — The AI Phone Guy | Calling Digital | Automotive Intelligence",
    version="2.0.0"
)

class ChatRequest(BaseModel):
    message: str

class ChatResponse(BaseModel):
    agent_id: str
    agent_role: str
    business: str
    response: str

def get_business_for_agent(agent_id: str) -> str:
    for biz_id, biz in BUSINESSES.items():
        if agent_id in biz["agents"]:
            return biz["name"]
    return "Unknown"

# ── Routes ─────────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {
        "status": "ok",
        "version": "2.0.0",
        "framework": "crewai",
        "total_agents": len(AGENTS),
        "businesses": list(BUSINESSES.keys())
    }

@app.get("/")
def root():
    return {"status": "ok", "service": "Paperclip", "version": "2.0.0"}

@app.get("/agents")
def list_agents():
    return {
        "businesses": BUSINESSES,
        "all_agent_ids": list(AGENTS.keys())
    }

@app.post("/chat/{agent_id}", response_model=ChatResponse)
async def chat(agent_id: str, request: ChatRequest):
    if agent_id not in AGENTS:
        raise HTTPException(
            status_code=404,
            detail=f"Agent '{agent_id}' not found. Available agents: {list(AGENTS.keys())}"
        )

    agent = AGENTS[agent_id]

    task = Task(
        description=request.message,
        expected_output=(
            "A comprehensive, actionable response in the agent's voice, "
            "expertise, and personality. Be specific, strategic, and useful."
        ),
        agent=agent
    )

    crew = Crew(
    agents=[agent],
    tasks=[task],
    process=Process.sequential,
    memory=False,
    verbose=False
)
    result = crew.kickoff()

    return ChatResponse(
        agent_id=agent_id,
        agent_role=agent.role,
        business=get_business_for_agent(agent_id),
        response=str(result)
    )

# ── Entry Point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 8000))
    )
