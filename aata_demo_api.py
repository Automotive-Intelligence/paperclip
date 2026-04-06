"""
AATA Demo API — FastAPI backend for React UI
"""

import os
import sys
import uuid
import asyncio
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, List
from dotenv import load_dotenv

load_dotenv()

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents.autointelligence.michael_meta_ii import DealershipAgent
from agents.autointelligence.vera import ConsumerAgent

app = FastAPI(title="AATA Demo", description="AI Agent-to-Agent Car Negotiation")

# CORS for React
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global agent instances
dealer = None
vera = None

# Models
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
    negotiation_log: Optional[List[dict]] = None

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

@app.on_event("startup")
async def startup_event():
    global dealer, vera
    dealer = DealershipAgent(dealer_name="Classic Chevrolet")
    await dealer.connect_db()
    vera = ConsumerAgent(buyer_name="Vera")
    await vera.connect_db()
    print("✅ Agents ready")

@app.get("/")
async def root():
    return {"service": "AATA Demo", "status": "ready"}

@app.get("/inventory", response_model=List[InventoryItem])
async def get_inventory():
    return dealer.get_inventory()

@app.post("/negotiate", response_model=NegotiateResponse)
async def negotiate(request: NegotiateRequest):
    # Set buyer profile
    vera.set_buyer_profile(
        name=request.buyer_name,
        max_budget=request.max_budget,
        max_monthly_payment=request.max_budget / 72,
        credit_tier="good",
        trade_in_value=request.trade_in_value,
        walk_away_threshold=request.walk_away_threshold
    )

    # Run negotiation
    result = await vera.start_negotiation(dealer, request.vin)

    return NegotiateResponse(
        success=result.get("success", False),
        final_price=result.get("final_price"),
        rounds=result.get("rounds", 0),
        message=result.get("message", "Negotiation completed"),
        dealer_session_id=result.get("dealer_session_id"),
        consumer_session_id=result.get("session_id")
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)
