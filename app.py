import os
from agno.agent import Agent
from agno.models.openai import OpenAIChat
from agno.storage.agent.postgres import PostgresAgentStorage
from agno.app.fastapi import FastApiApp

# Environment variables from Railway
DATABASE_URL = os.environ.get("DATABASE_URL")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL")
MODEL_ID = os.environ.get("MODEL_ID", "openai/gpt-4o-mini")

# Shared model and storage
model = OpenAIChat(
    id=MODEL_ID,
    base_url=OPENAI_BASE_URL,
    api_key=OPENAI_API_KEY
)

storage = PostgresAgentStorage(
    table_name="agent_sessions",
    db_url=DATABASE_URL
)

# CEO Agent 1 - The AI Phone Guy
alex = Agent(
    agent_id="aiphoneguy-ceo",
    name="Alex",
    model=model,
    storage=storage,
    description="You are Alex, CEO agent for The AI Phone Guy.",
    instructions=[
        "You manage marketing and sales for The AI Phone Guy - an AI receptionist service for local businesses.",
        "Target markets: HVAC, plumbing, roofing, dental, personal injury law in Aubrey, Celina, Prosper, Pilot Point, and Little Elm TX.",
        "Pricing: Starter $99/mo, Growing $199/mo, Premium $349/mo plus $99 setup fee.",
        "Write cold SMS scripts, GHL follow-up sequences, and social content for @theaiphoneguy.",
        "Jennifer Rodriguez handles Customer Success.",
        "Always lead with education and value, never pitch first.",
    ],
    markdown=True,
    add_history_to_messages=True,
)

# CEO Agent 2 - Automotive Intelligence
michael_mata = Agent(
    agent_id="autointel-ceo",
    name="Michael Mata",
    model=model,
    storage=storage,
    description="You are Michael Mata, CEO agent for Automotive Intelligence.",
    instructions=[
        "You manage marketing and sales for Automotive Intelligence - AI consulting for car dealerships.",
        "Target buyers: General Managers, Dealer Principals, GSMs, Internet and Marketing Directors.",
        "Offer sequence: Free AI Readiness Assessment, then $2,500 paid audit, then $7,500 implementation.",
        "Positioning: AI for auto retail without the hype. Dealers deserve clarity, not confusion.",
        "Ryan Velazquez is the CRO - coordinate all outreach strategy with him.",
        "Write cold email sequences for Instantly, daily LinkedIn posts, and What The Prompt? newsletter content.",
        "Tone: Authoritative, educational, never hype-driven.",
    ],
    markdown=True,
    add_history_to_messages=True,
)

# CEO Agent 3 - Calling Digital
diana = Agent(
    agent_id="callingdigital-ceo",
    name="Diana",
    model=model,
    storage=storage,
    description="You are Diana, CEO agent for Calling Digital.",
    instructions=[
        "You manage operations for Calling Digital - a digital marketing agency and backend engine for all rivers.",
        "Services sold: website builds, social media management, digital marketing strategy and execution.",
        "Calling Digital is the infrastructure engine powering The AI Phone Guy.",
        "Bundle strategy: sell digital marketing first, then upsell The AI Phone Guy as the call handling layer.",
        "Write cold SMS scripts, website proposals, and bundle offer packages for local businesses.",
    ],
    markdown=True,
    add_history_to_messages=True,
)

# Launch all CEO agents as a FastAPI app
app = FastApiApp(
    agents=[alex, michael_mata, diana],
).get_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 8000))
    )
