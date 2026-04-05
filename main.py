"""Project Paperclip — Main Entry Point.

Starts all schedulers, runs initial enrollment pass, serves health endpoint.
Deploy to Railway. $15K MRR. Michael shows up to close. Agents do everything else.
"""

import os
import sys
import threading
from contextlib import asynccontextmanager
from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI
from fastapi.responses import JSONResponse
import uvicorn

from core.logger import log_info
from core.scheduler import create_scheduler, register_all_jobs


# ─── Startup / Shutdown ───

@asynccontextmanager
async def lifespan(app: FastAPI):
    log_info("main", "=" * 60)
    log_info("main", "PROJECT PAPERCLIP — EMPIRE ONLINE")
    log_info("main", "=" * 60)
    log_info("main", "Rivers: AI Phone Guy | Calling Digital | Automotive Intelligence | Agent Empire | CustomerAdvocate")
    log_info("main", "Agents: Randy | Brenda | Darrell | Tammy | Wade | Debra | Clint | Sherry")
    log_info("main", "=" * 60)

    # Run HubSpot cleanup first
    _run_hubspot_cleanup()

    # Initial enrollment pass across all rivers
    _initial_enrollment()

    # Start scheduler
    scheduler = create_scheduler()
    register_all_jobs(scheduler)
    scheduler.start()
    log_info("main", "Scheduler started — all agents active")

    yield

    scheduler.shutdown()
    log_info("main", "Scheduler stopped — empire offline")


app = FastAPI(title="Project Paperclip", version="1.0.0", lifespan=lifespan)


# ─── Health Endpoint ───

@app.get("/")
async def root():
    return {"status": "online", "project": "paperclip", "rivers": 5, "agents": 8}


@app.get("/health")
async def health():
    from rivers.ai_phone_guy.workflow import get_stats as apg_stats
    from rivers.calling_digital.workflow import get_stats as cd_stats
    from rivers.automotive_intelligence.workflow import get_stats as ai_stats
    from rivers.agent_empire.workflow import get_stats as ae_stats

    return JSONResponse({
        "status": "healthy",
        "rivers": {
            "ai_phone_guy": apg_stats(),
            "calling_digital": cd_stats(),
            "automotive_intelligence": ai_stats(),
            "agent_empire": ae_stats(),
        },
    })


@app.get("/rivers")
async def rivers():
    return {
        "rivers": [
            {"name": "AI Phone Guy", "crm": "GoHighLevel", "agent": "Randy", "schedule": "every 4 hours"},
            {"name": "Calling Digital", "crm": "Attio", "agent": "Brenda", "schedule": "every 2 hours"},
            {"name": "Automotive Intelligence", "crm": "HubSpot", "agent": "Darrell", "schedule": "every 1 hour"},
            {"name": "Agent Empire", "platform": "Skool", "agents": ["Tammy", "Wade", "Debra"], "schedule": "Tammy 6hr / Wade Mon 9am"},
            {"name": "CustomerAdvocate", "components": ["VERA", "AATA", "The Exchange"], "agents": ["Clint", "Sherry"]},
        ]
    }


@app.post("/cleanup/hubspot")
async def trigger_cleanup():
    """Manually trigger HubSpot cleanup."""
    from rivers.automotive_intelligence.cleanup import run_cleanup
    results = run_cleanup()
    return results


@app.post("/run/{agent}")
async def trigger_agent(agent: str):
    """Manually trigger an agent run."""
    runners = {
        "randy": ("rivers.ai_phone_guy.workflow", "randy_run"),
        "brenda": ("rivers.calling_digital.workflow", "brenda_run"),
        "darrell": ("rivers.automotive_intelligence.workflow", "darrell_run"),
        "tammy": ("rivers.agent_empire.workflow", "tammy_run"),
        "wade": ("rivers.agent_empire.workflow", "wade_run"),
    }
    if agent not in runners:
        return JSONResponse({"error": f"Unknown agent: {agent}"}, status_code=404)

    mod_name, func_name = runners[agent]
    import importlib
    mod = importlib.import_module(mod_name)
    func = getattr(mod, func_name)
    func()
    return {"status": "completed", "agent": agent}


# ─── Startup Tasks ───

def _run_hubspot_cleanup():
    """Run HubSpot cleanup on startup — classify 690 contacts."""
    try:
        from rivers.automotive_intelligence.cleanup import run_cleanup
        log_info("main", "Running HubSpot cleanup...")
        results = run_cleanup()
        log_info("main", f"HubSpot cleanup: {results}")
    except Exception as e:
        log_info("main", f"HubSpot cleanup skipped: {e}")


def _initial_enrollment():
    """Run initial enrollment pass across all active rivers."""
    log_info("main", "Running initial enrollment pass...")

    try:
        from rivers.ai_phone_guy.workflow import randy_run
        randy_run()
    except Exception as e:
        log_info("main", f"Randy initial run skipped: {e}")

    try:
        from rivers.calling_digital.workflow import brenda_run
        brenda_run()
    except Exception as e:
        log_info("main", f"Brenda initial run skipped: {e}")

    try:
        from rivers.automotive_intelligence.workflow import darrell_run
        darrell_run()
    except Exception as e:
        log_info("main", f"Darrell initial run skipped: {e}")

    try:
        from rivers.agent_empire.workflow import tammy_run
        tammy_run()
    except Exception as e:
        log_info("main", f"Tammy initial run skipped: {e}")

    log_info("main", "Initial enrollment pass complete")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
