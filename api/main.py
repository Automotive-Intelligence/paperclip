"""
AATA Demo API - FastAPI endpoints for Jose's demo.
"""

import os
import sys
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import Field

# --- Mock Data Models for Companies, Agents, and Work History ---
from pydantic import BaseModel as PydanticBaseModel

class Company(PydanticBaseModel):
    id: str
    name: str

class Agent(PydanticBaseModel):
    id: str
    name: str
    role: str
    company_id: str

class WorkHistoryItem(PydanticBaseModel):
    id: str
    agent_id: str
    timestamp: str
    type: str
    description: str

# --- Mock Data ---
MOCK_COMPANIES = [
    Company(id="classic_chevrolet", name="Classic Chevrolet"),
    Company(id="future_ford", name="Future Ford")
]

MOCK_AGENTS = [
    Agent(id="michael_meta_ii", name="Michael Meta II", role="Dealership Agent", company_id="classic_chevrolet"),
    Agent(id="vera", name="Vera", role="Consumer Agent", company_id="classic_chevrolet"),
    Agent(id="alex_gpt", name="Alex GPT", role="Dealership Agent", company_id="future_ford")
]

MOCK_WORK_HISTORY = [
    WorkHistoryItem(id="h1", agent_id="michael_meta_ii", timestamp="2026-03-30T10:00:00Z", type="negotiation", description="Closed deal for VIN 123456"),
    WorkHistoryItem(id="h2", agent_id="michael_meta_ii", timestamp="2026-03-29T15:30:00Z", type="negotiation", description="Negotiation failed for VIN 654321"),
    WorkHistoryItem(id="h3", agent_id="vera", timestamp="2026-03-30T10:00:00Z", type="negotiation", description="Closed deal for VIN 123456"),
    WorkHistoryItem(id="h4", agent_id="alex_gpt", timestamp="2026-03-28T09:00:00Z", type="negotiation", description="Closed deal for VIN 999999")
]

# --- FastAPI App Initialization (move to top) ---
app = FastAPI(title="AATA Demo API", description="Agent-to-Agent Car Negotiation")

# --- New Endpoints for Companies, Agents, and Work History ---
@app.get("/companies", response_model=List[Company])
async def get_companies():
    """List all companies."""
    return MOCK_COMPANIES


@app.get("/companies/{company_id}/agents", response_model=List[Agent])
async def get_agents_for_company(company_id: str):
    """List all agents for a company."""
    return [agent for agent in MOCK_AGENTS if agent.company_id == company_id]


@app.get("/agents/{agent_id}/history", response_model=List[WorkHistoryItem])
async def get_agent_history(agent_id: str):
    """Get work history for an agent."""
    return [item for item in MOCK_WORK_HISTORY if item.agent_id == agent_id]
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel


PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))


from agents.autointelligence.michael_meta_ii import DealershipAgent
from agents.autointelligence.vera import ConsumerAgent


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


dealer: Optional[DealershipAgent] = None
vera: Optional[ConsumerAgent] = None


@app.on_event("startup")
async def startup_event() -> None:
    """Initialize reusable agent instances on startup."""
    global dealer, vera

    dealer = DealershipAgent(dealer_name="Classic Chevrolet")
    await dealer.connect_db()

    vera = ConsumerAgent(buyer_name="Vera")
    await vera.connect_db()

    print("Agents initialized and connected to database")


class NegotiateRequest(BaseModel):
    vin: str
    buyer_name: Optional[str] = "Sarah Johnson"
    max_budget: Optional[float] = 50000
    walk_away_threshold: Optional[float] = 49000
    trade_in_value: Optional[float] = 18500


class NegotiateResponse(BaseModel):
    success: bool
    final_price: Optional[float] = None
    rounds: int
    message: str
    dealer_session_id: Optional[str] = None
    consumer_session_id: Optional[str] = None


class InventoryItem(BaseModel):
    vin: str
    year: int
    make: str
    model: str
    trim: str
    color: str
    price: float
    msrp: float
    days: int


@app.get("/")
async def root() -> dict:
    return {
        "service": "AATA Demo API",
        "status": "running",
        "agents": {
            "dealer": "Michael Meta II",
            "consumer": "Vera",
        },
        "endpoints": [
            "GET /inventory",
            "POST /negotiate",
            "GET /sessions/{session_id}",
            "GET /companies",
            "GET /companies/{company_id}/agents",
            "GET /agents/{agent_id}/history",
        ],
    }


@app.get("/inventory", response_model=List[InventoryItem])
async def get_inventory() -> List[InventoryItem]:
    """Get available inventory from the dealership agent."""
    if not dealer:
        raise HTTPException(status_code=500, detail="Dealer agent not initialized")

    inventory = dealer.get_inventory()
    return [InventoryItem(**item) for item in inventory]


@app.post("/negotiate", response_model=NegotiateResponse)
async def negotiate(request: NegotiateRequest) -> NegotiateResponse:
    """Start an AI-to-AI negotiation between Vera and Michael Meta II."""
    if not dealer or not vera:
        raise HTTPException(status_code=500, detail="Agents not initialized")

    vehicle = dealer.get_vehicle(request.vin)
    if not vehicle:
        raise HTTPException(status_code=404, detail=f"Vehicle {request.vin} not found")

    vera.set_buyer_profile(
        name=request.buyer_name or "Sarah Johnson",
        max_budget=request.max_budget or 50000,
        max_monthly_payment=(request.max_budget or 50000) / 72,
        credit_tier="good",
        trade_in_value=request.trade_in_value or 18500,
        walk_away_threshold=request.walk_away_threshold or 49000,
    )

    result = await vera.start_negotiation(dealer, request.vin)

    return NegotiateResponse(
        success=result.get("success", False),
        final_price=result.get("final_price"),
        rounds=result.get("rounds", 0),
        message=result.get("message", "Negotiation completed"),
        dealer_session_id=result.get("dealer_session_id"),
        consumer_session_id=result.get("session_id"),
    )


@app.get("/sessions/{session_id}")
async def get_session(session_id: str) -> dict:
    """Get negotiation session details from the database."""
    if not dealer or not dealer.pool:
        raise HTTPException(status_code=500, detail="Database not connected")

    try:
        async with dealer.pool.acquire() as conn:
            dealer_session = await conn.fetchrow(
                "SELECT * FROM dealership.negotiation_sessions WHERE session_id = $1",
                session_id,
            )

            if not dealer_session:
                consumer_session = await conn.fetchrow(
                    "SELECT * FROM consumer.negotiation_sessions WHERE session_id = $1 OR dealer_session_id = $1",
                    session_id,
                )
                if not consumer_session:
                    raise HTTPException(status_code=404, detail="Session not found")

                offers = await conn.fetch(
                    """
                    SELECT 'consumer' AS side, round_num, offer_amount, decision, reasoning, created_at
                    FROM consumer.offers
                    WHERE session_id = $1
                    ORDER BY round_num
                    """,
                    consumer_session["session_id"],
                )

                return {
                    "session_id": consumer_session["session_id"],
                    "type": "consumer",
                    "buyer_id": consumer_session["buyer_id"],
                    "dealer_session_id": consumer_session["dealer_session_id"],
                    "status": consumer_session["status"],
                    "final_price": consumer_session["final_price"],
                    "offers": [dict(offer) for offer in offers],
                }

            consumer_session = await conn.fetchrow(
                "SELECT * FROM consumer.negotiation_sessions WHERE dealer_session_id = $1",
                session_id,
            )

            dealer_offers = await conn.fetch(
                "SELECT * FROM dealership.offers WHERE session_id = $1 ORDER BY round_num",
                session_id,
            )

            consumer_offers = []
            if consumer_session:
                consumer_offers = await conn.fetch(
                    "SELECT * FROM consumer.offers WHERE session_id = $1 ORDER BY round_num",
                    consumer_session["session_id"],
                )

            return {
                "session_id": session_id,
                "type": "dealer",
                "vin": dealer_session["vin"],
                "status": dealer_session["status"],
                "final_price": dealer_session["final_price"],
                "dealer_offers": [dict(offer) for offer in dealer_offers],
                "consumer_offers": [dict(offer) for offer in consumer_offers],
                "consumer_session": dict(consumer_session) if consumer_session else None,
            }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)