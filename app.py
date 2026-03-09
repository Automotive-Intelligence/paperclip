import os
from agno.agent import Agent
from agno.models.openai import OpenAIChat
from agno.db.postgres import PostgresDb
from agno.os import AgentOS

# Environment variables from Railway
DATABASE_URL = os.environ.get("DATABASE_URL", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL")
MODEL_ID = os.environ.get("MODEL_ID", "openai/gpt-4o-mini")

# Fix Railway's DATABASE_URL format for psycopg3
db_url = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://").replace("postgres://", "postgresql+psycopg://")

# Shared model and database
model = OpenAIChat(
    id=MODEL_ID,
    base_url=OPENAI_BASE_URL,
    api_key=OPENAI_API_KEY
)

db = PostgresDb(db_url=db_url)

# CEO Agent 1 - The AI Phone Guy
alex = Agent(
    agent_id="aiphoneguy-ceo",
    name="Alex",
    model=model,
    db=db,
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
    add_history_to_context=True,
)

# CEO Agent 2 - Automotive Intelligence
michael_mata = Agent(
    agent_id="autointel-ceo",
    name="Michael Mata",
    model=model,
    db=db,
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
    add_history_to_context=True,
)

# CEO Agent 3 - Calling Digital
diana = Agent(
    agent_id="callingdigital-ceo",
    name="Diana",
    model=model,
    db=db,
    description="You are Diana, CEO agent for Calling Digital.",
    instructions=[
        "You manage operations for Calling Digital - a digital marketing agency and backend engine for all rivers.",
        "Services sold: website builds, social media management, digital marketing strategy and execution.",
        "Calling Digital is the infrastructure engine powering The AI Phone Guy.",
        "Bundle strategy: sell digital marketing first, then upsell The AI Phone Guy as the call handling layer.",
        "Write cold SMS scripts, website proposals, and bundle offer packages for local businesses.",
    ],
    markdown=True,
    add_history_to_context=True,
)

# Launch all CEO agents via AgentOS
agent_os = AgentOS(agents=[alex, michael_mata, diana])
app = agent_os.get_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 8000))
    )
