import os
import logging
import datetime
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Header
from fastapi.responses import HTMLResponse
from typing import Optional
from pydantic import BaseModel
from crewai import Crew, Task, Process
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz

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

# Agent Registry
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

ADMIN_KEY = os.environ.get("ADMIN_KEY", "")
RYAN_KEY = os.environ.get("RYAN_KEY", "")

BUSINESS_AGENTS = {
    "aiphoneguy": ["alex", "tyler", "zoe", "jennifer"],
    "callingdigital": ["dek", "marcus", "sofia", "carlos", "nova"],
    "autointelligence": ["michael-mata", "ryan-data", "chase", "atlas", "phoenix"],
}

def get_allowed_businesses(key: str) -> list:
    if ADMIN_KEY and key == ADMIN_KEY:
        return ["aiphoneguy", "callingdigital", "autointelligence"]
    if RYAN_KEY and key == RYAN_KEY:
        return ["autointelligence"]
    return []

def get_agent_business(agent_id: str) -> str:
    for biz, agents in BUSINESS_AGENTS.items():
        if agent_id in agents:
            return biz
    return None

# Daily Briefing Scheduler
CST = pytz.timezone("America/Chicago")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

def run_alex_daily_briefing():
    """
    Alex's daily industry briefing - runs once at 8am CST.
    Searches for AI receptionist news and DFW local business trends,
    then logs the result to /logs/alex_briefing_YYYY-MM-DD.log.
    """
    today = datetime.datetime.now(CST).strftime("%Y-%m-%d")
    timestamp = datetime.datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S %Z")
    log_path = os.path.join("logs", f"alex_briefing_{today}.log")

    logging.info(f"[Scheduler] Starting Alex daily briefing for {today}")

    try:
        briefing_task = Task(
            description=(
                "Search for today's most relevant news and developments across two areas:\n\n"
                "1. AI receptionists, AI phone answering services, and conversational AI for "
                "small and local businesses -- any new products, competitors, pricing moves, "
                "or customer adoption stories.\n\n"
                "2. Local service businesses in the Dallas-Fort Worth metroplex -- HVAC, "
                "plumbing, roofing, dental practices, personal injury attorneys, and similar "
                "trades. Look for growth trends, labor news, tech adoption, or consumer behavior shifts.\n\n"
                "Search both topics thoroughly, then produce a structured morning briefing."
            ),
            expected_output=(
                "A concise daily briefing with three clearly labeled sections:\n\n"
                "AI RECEPTIONIST INDUSTRY NEWS\n"
                "- 2-3 bullet points with headline, source context, and one business implication each\n\n"
                "DFW LOCAL SERVICE BUSINESS TRENDS\n"
                "- 2-3 bullet points with headline, source context, and one business implication each\n\n"
                "STRATEGIC SUMMARY\n"
                "One paragraph: what today's findings mean for The AI Phone Guy and any "
                "immediate actions worth considering. Keep the whole brief under 400 words."
            ),
            agent=alex
        )

        crew = Crew(
            agents=[alex],
            tasks=[briefing_task],
            process=Process.sequential,
            memory=False,
            verbose=False
        )

        result = crew.kickoff()

        os.makedirs("logs", exist_ok=True)
        with open(log_path, "w") as f:
            f.write(f"=== Alex Daily Briefing -- {today} ===\n")
            f.write(f"Generated: {timestamp}\n")
            f.write("=" * 60 + "\n\n")
            f.write(str(result))
            f.write("\n")

        logging.info(f"[Scheduler] Alex briefing complete -> {log_path}")

    except Exception as e:
        logging.error(f"[Scheduler] Alex briefing failed: {type(e).__name__}: {e}")


scheduler = BackgroundScheduler(timezone=CST)
scheduler.add_job(
    run_alex_daily_briefing,
    CronTrigger(hour=8, minute=0, timezone=CST),
    id="alex_daily_briefing",
    name="Alex Daily Briefing",
    replace_existing=True,
    misfire_grace_time=3600
)


# FastAPI App
@asynccontextmanager
async def lifespan(app: FastAPI):
    os.makedirs("logs", exist_ok=True)
    scheduler.start()
    logging.info("[Scheduler] Started -- Alex daily briefing scheduled at 8:00am CST")
    yield
    scheduler.shutdown(wait=False)
    logging.info("[Scheduler] Stopped")


app = FastAPI(
    title="Paperclip",
    description="AI Agent Infrastructure -- The AI Phone Guy | Calling Digital | Automotive Intelligence",
    version="2.0.0",
    lifespan=lifespan
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

# Routes

@app.get("/health")
def health():
    job = scheduler.get_job("alex_daily_briefing")
    return {
        "status": "ok",
        "version": "2.0.0",
        "framework": "crewai",
        "total_agents": len(AGENTS),
        "businesses": list(BUSINESSES.keys()),
        "scheduler": {
            "running": scheduler.running,
            "next_briefing": str(job.next_run_time) if job else None
        }
    }

class AuthRequest(BaseModel):
    key: str

@app.post("/auth/validate")
def validate_key(request: AuthRequest):
    businesses = get_allowed_businesses(request.key)
    if businesses:
        return {"valid": True, "businesses": businesses}
    return {"valid": False, "businesses": []}

@app.get("/")
def root():
    with open("static/index.html", "r") as f:
        return HTMLResponse(content=f.read())

@app.get("/agents")
def list_agents():
    return {
        "businesses": BUSINESSES,
        "all_agent_ids": list(AGENTS.keys())
    }

@app.post("/chat/{agent_id}", response_model=ChatResponse)
async def chat(agent_id: str, request: ChatRequest, x_access_key: Optional[str] = Header(None)):
    if ADMIN_KEY or RYAN_KEY:
        allowed = get_allowed_businesses(x_access_key or "")
        if not allowed:
            raise HTTPException(status_code=401, detail="Invalid access key")
        if get_agent_business(agent_id) not in allowed:
            raise HTTPException(status_code=403, detail="Access denied to this agent")

    if agent_id not in AGENTS:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")

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

# Entry Point
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 8000))
    )
