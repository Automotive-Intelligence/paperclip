import os
import glob
import logging
import datetime
from contextlib import asynccontextmanager, contextmanager
from fastapi import FastAPI, HTTPException, Header
from fastapi.responses import PlainTextResponse, JSONResponse
from typing import Optional, List
from pydantic import BaseModel
from crewai import Crew, Task, Process
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz
try:
    import psycopg2 as psycopg   # psycopg2-binary: stable on Railway, API-compatible
    _PSYCOPG_OK = True
except ImportError as _psycopg_err:
    import logging as _tmp_log
    _tmp_log.warning(f"[DB] psycopg2 import failed â Postgres disabled: {_psycopg_err}")
    psycopg = None  # type: ignore
    _PSYCOPG_OK = False


# ââ Tool Imports âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

from tools.prospect_parser import parse_tyler_prospects
from tools.ghl import push_prospects_to_ghl


# ââ Agent Imports ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

# The AI Phone Guy
from agents.aiphoneguy.alex import alex
from agents.aiphoneguy.tyler import tyler
from agents.aiphoneguy.zoe import zoe
from agents.aiphoneguy.jennifer import jennifer

# Calling Digital
from agents.callingdigital.dek import dek
from agents.callingdigital.marcus import marcus
from agents.callingdigital.sofia import sofia
from agents.callingdigital.carlos import carlos
from agents.callingdigital.nova import nova

# Automotive Intelligence
from agents.autointelligence.michael_meta import michael_meta
from agents.autointelligence.ryan_data import ryan_data
from agents.autointelligence.chase import chase
from agents.autointelligence.atlas import atlas
from agents.autointelligence.phoenix import phoenix


def run_tyler_prospecting():
    try:
        task = Task(
            description=(
                "Search for local service businesses in Aubrey, Celina, Prosper, Pilot Point, "
                "and Little Elm TX -- HVAC, plumbing, roofing, dental, and personal injury law. "
                "Search for news about businesses expanding, opening new locations, or hiring. "
                "Look for buying signals: Google reviews mentioning missed calls, slow response, "
                "or after-hours availability issues. "
                "Compile 5 high-priority outreach targets for today with a personalized SMS hook for each."
            ),
            expected_output=(
                "Daily prospecting report: (1) 5 outreach targets with business name, type, city, "
                "reason for targeting, and a personalized cold SMS opening hook. "
                "(2) Any signals that make today a particularly good time to reach out."
            ),
            agent=tyler,
        )
        crew = Crew(agents=[tyler], tasks=[task], process=Process.sequential, memory=False, verbose=False)
        result = crew.kickoff()
        raw_output = str(result)
        persist_log("tyler", "prospecting", raw_output)
        logging.info("[Scheduler] Tyler prospecting complete.")
        if os.getenv("GHL_API_KEY") and os.getenv("GHL_LOCATION_ID"):
            try:
                prospects = parse_tyler_prospects(raw_output)
                if prospects:
                    ghl_results = push_prospects_to_ghl(prospects)
                    created = len([r for r in ghl_results if r.get("status") == "created"])
                    logging.info(f"[GHL] Tyler pushed {created}/{len(prospects)} prospects to GoHighLevel.")
                else:
                    logging.warning("[GHL] No prospects parsed from Tyler's output.")
            except Exception as ghl_err:
                logging.error(f"[GHL] Tyler->GHL push failed: {ghl_err}")
        else:
            logging.info("[GHL] Skipping GHL push -- GHL_API_KEY or GHL_LOCATION_ID not set.")
    except Exception as e:
        logging.error(f"[Scheduler] Tyler prospecting failed: {type(e).__name__}: {e}")


def run_marcus_prospecting():
    try:
        task = Task(
            description=(
                "Search for small and mid-size businesses in Dallas that need digital marketing help â "
                "businesses with outdated websites, weak social presence, no Google reviews strategy, "
                "or recent funding/expansion news. Look for buying signals: businesses posting about "
                "marketing struggles, hiring marketing roles, or launching new services. "
                "Compile 5 high-priority outreach targets for today with a consultative opening approach. "
                "Flag any that are also strong candidates for The AI Phone Guy bundle upsell."
            ),
            expected_output=(
                "Daily prospecting report: (1) 5 outreach targets with company name, industry, "
                "key pain point, and a consultative opening for outreach. "
                "(2) Bundle opportunities flagged for Dek."
            ),
            agent=marcus,
        )
        crew = Crew(agents=[marcus], tasks=[task], process=Process.sequential, memory=False, verbose=False)
        result = crew.kickoff()
        persist_log("marcus", "prospecting", str(result))
        logging.info("[Scheduler] Marcus prospecting complete.")
    except Exception as e:
        logging.error(f"[Scheduler] Marcus prospecting failed: {type(e).__name__}: {e}")


def run_ryan_data_prospecting():
    try:
        task = Task(
            description=(
                "Search for car dealerships in the Dallas-Fort Worth area showing AI readiness signals: "
                "job postings for digital transformation or BDC roles, news about expansion or new ownership, "
                "Google reviews mentioning slow response times, or recent tech vendor changes. "
                "Search for news about target dealership groups. "
                "Identify 5 high-priority dealership targets for outreach today with personalized context "
                "for the free AI Readiness Assessment offer."
            ),
            expected_output=(
                "Daily prospecting report: (1) 5 dealership targets with name, group affiliation, "
                "AI readiness signal found, and personalized assessment offer hook. "
                "(2) Pipeline notes on any previously contacted dealers showing new activity."
            ),
            agent=ryan_data,
        )
        crew = Crew(agents=[ryan_data], tasks=[task], process=Process.sequential, memory=False, verbose=False)
        result = crew.kickoff()
        persist_log("ryan_data", "prospecting", str(result))
        logging.info("[Scheduler] Ryan Data prospecting complete.")
    except Exception as e:
        logging.error(f"[Scheduler] Ryan Data prospecting failed: {type(e).__name__}: {e}")


# ââ Marketing Content ââ 9:00, 9:02, 9:04 CST âââââââââââââââââââââââââââââââââ


def run_zoe_content():
    try:
        task = Task(
            description=(
                "Search for trending topics in local service business marketing, AI for small business, "
                "and DFW small business news today. Search for competitor content from other AI receptionist "
                "brands â what's performing well, what hooks are working. "
                "Design 3 content pieces across the full marketing funnel: "
                "one AWARENESS piece (SEO blog or social), one CONSIDERATION piece (case study or "
                "objection-handler), one CONVERSION piece (offer or CTA-focused). "
                "For each: platform, hook, format, key message, and CTA. "
                "Include one social post ready to publish and top SEO/AEO keyword opportunities."
            ),
            expected_output=(
                "Daily content plan: (1) 3 fully detailed content ideas with platform/hook/format/message/CTA. "
                "(2) SEO/AEO keyword opportunities spotted today. "
                "(3) One social media post ready to publish with caption and hashtags."
            ),
            agent=zoe,
        )
        crew = Crew(agents=[zoe], tasks=[task], process=Process.sequential, memory=False, verbose=False)
        result = crew.kickoff()
        persist_log("zoe", "content", str(result))
        logging.info("[Scheduler] Zoe content complete.")
    except Exception as e:
        logging.error(f"[Scheduler] Zoe content failed: {type(e).__name__}: {e}")


def run_sofia_content():
    try:
        task = Task(
            description=(
                "Search for trending topics in digital marketing, AI for business, and Dallas business news. "
                "Search for what other marketing agencies are publishing and what content is performing well. "
                "Design 3 content pieces for Calling Digital's full-funnel strategy: "
                "one AWARENESS piece (thought leadership or educational), "
                "one CONSIDERATION piece (case study, comparison, or guide), "
                "one CONVERSION piece (offer or CTA). "
                "Also identify one AI education content angle that warms up existing clients "
                "for the Nova AI consulting upsell. "
                "For each: platform, hook, format, key message, and CTA."
            ),
            expected_output=(
                "Daily content plan: (1) 3 fully detailed content ideas with platform/hook/format/message/CTA. "
                "(2) One AI education piece idea for the consulting upsell pipeline. "
                "(3) One social post ready to publish for Calling Digital."
            ),
            agent=sofia,
        )
        crew = Crew(agents=[sofia], tasks=[task], process=Process.sequential, memory=False, verbose=False)
        result = crew.kickoff()
        persist_log("sofia", "content", str(result))
        logging.info("[Scheduler] Sofia content complete.")
    except Exception as e:
        logging.error(f"[Scheduler] Sofia content failed: {type(e).__name__}: {e}")


def run_chase_content():
    try:
        task = Task(
            description=(
                "Search for trending AI and automotive retail news today â dealership technology stories, "
                "auto industry AI announcements, or DFW dealer news. "
                "Search for what automotive thought leaders are publishing on LinkedIn and in newsletters. "
                "Design 3 content pieces for Automotive Intelligence's full marketing funnel: "
                "one LinkedIn thought leadership post for Michael Meta's personal brand, "
                "one What The Prompt? newsletter section (educational, non-salesy), "
                "one cold email subject line and opener for Ryan Data's sequences. "
                "For each: hook, key insight, format, and CTA."
            ),
            expected_output=(
                "Daily content plan: (1) LinkedIn post ready to publish â hook, body, CTA. "
                "(2) Newsletter section â topic, angle, 3 key points. "
                "(3) Cold email subject line + opener for dealer outreach. "
                "(4) SEO/AEO keyword opportunity in automotive AI space."
            ),
            agent=chase,
        )
        crew = Crew(agents=[chase], tasks=[task], process=Process.sequential, memory=False, verbose=False)
        result = crew.kickoff()
        persist_log("chase", "content", str(result))
        logging.info("[Scheduler] Chase content complete.")
    except Exception as e:
        logging.error(f"[Scheduler] Chase content failed: {type(e).__name__}: {e}")


# ââ Client Success ââ 9:30, 9:32 CST ââââââââââââââââââââââââââââââââââââââââââ


def run_jennifer_retention():
    try:
        task = Task(
            description=(
                "Search for current best practices in client retention for SaaS and AI subscription services. "
                "Search for common objections and churn reasons for AI receptionist tools. "
                "Identify upsell and expansion triggers â what behaviors indicate a Starter client "
                "is ready for Growing, or a Growing client is ready for Premium. "
                "Develop 3 proactive talking points for client check-in calls today: "
                "one celebrating a quick win, one addressing a common concern, one introducing an upsell opportunity."
            ),
            expected_output=(
                "Daily retention brief: (1) 3 proactive talking points for client calls. "
                "(2) Key objections to monitor and counter-messaging. "
                "(3) Upsell signals to look for in the current client base. "
                "(4) One retention message or email template ready to send."
            ),
            agent=jennifer,
        )
        crew = Crew(agents=[jennifer], tasks=[task], process=Process.sequential, memory=False, verbose=False)
        result = crew.kickoff()
        persist_log("jennifer", "retention", str(result))
        logging.info("[Scheduler] Jennifer retention complete.")
    except Exception as e:
        logging.error(f"[Scheduler] Jennifer retention failed: {type(e).__name__}: {e}")


def run_atlas_intel():
    try:
        task = Task(
            description=(
                "Search for DFW car dealership news today: new openings, closings, ownership changes, "
                "expansions, and personnel changes (GM hires, marketing director changes). "
                "Search for dealership Google reviews mentioning slow response, poor digital experience, "
                "or tech issues. Search for dealership job postings related to digital transformation, "
                "BDC, or technology roles. "
                "Compile 3 target dealer briefs: dealership name, group affiliation, AI readiness signal, "
                "and recommended outreach angle for Ryan Data."
            ),
            expected_output=(
                "Daily dealer intelligence report: (1) Top DFW dealership news and personnel changes. "
                "(2) 3 target dealer briefs with name, signal, and outreach recommendation. "
                "(3) Competitive activity â other AI vendors approaching DFW dealerships."
            ),
            agent=atlas,
        )
        crew = Crew(agents=[atlas], tasks=[task], process=Process.sequential, memory=False, verbose=False)
        result = crew.kickoff()
        persist_log("atlas", "intel", str(result))
        logging.info("[Scheduler] Atlas intel complete.")
    except Exception as e:
        logging.error(f"[Scheduler] Atlas intel failed: {type(e).__name__}: {e}")


def run_phoenix_delivery():
    try:
        task = Task(
            description=(
                "Search for AI implementation case studies and success stories from auto dealerships "
                "or similar B2B service businesses. Search for best practices in AI adoption, "
                "change management in dealerships, and onboarding frameworks for new technology. "
                "Identify one SOP improvement or delivery optimization for the Automotive Intelligence "
                "implementation playbook. Look for new tools or methods to improve implementation speed."
            ),
            expected_output=(
                "Daily delivery intelligence report: (1) Top implementation case study or best practice. "
                "(2) One SOP improvement recommendation with specific steps. "
                "(3) New tools or methods relevant to automotive AI implementation. "
                "(4) One client ROI metric or success framework worth incorporating."
            ),
            agent=phoenix,
        )
        crew = Crew(agents=[phoenix], tasks=[task], process=Process.sequential, memory=False, verbose=False)
        result = crew.kickoff()
        persist_log("phoenix", "delivery", str(result))
        logging.info("[Scheduler] Phoenix delivery complete.")
    except Exception as e:
        logging.error(f"[Scheduler] Phoenix delivery failed: {type(e).__name__}: {e}")


# ââ Register All 13 Scheduler Jobs ââââââââââââââââââââââââââââââââââââââââââââ

# CEOs â 8:00, 8:02, 8:04
scheduler.add_job(run_alex_daily_briefing, CronTrigger(hour=8, minute=0, timezone=CST),
    id="alex_daily_briefing", name="Alex Daily Briefing",
    replace_existing=True, misfire_grace_time=3600)

scheduler.add_job(run_dek_daily_briefing, CronTrigger(hour=8, minute=2, timezone=CST),
    id="dek_daily_briefing", name="Dek Daily Briefing",
    replace_existing=True, misfire_grace_time=3600)

scheduler.add_job(run_michael_meta_daily_briefing, CronTrigger(hour=8, minute=4, timezone=CST),
    id="michael_meta_daily_briefing", name="Michael Meta Daily Briefing",
    replace_existing=True, misfire_grace_time=3600)

# Sales â 8:30, 8:32, 8:34
scheduler.add_job(run_tyler_prospecting, CronTrigger(hour=8, minute=30, timezone=CST),
    id="tyler_daily_prospecting", name="Tyler Daily Prospecting",
    replace_existing=True, misfire_grace_time=3600)

scheduler.add_job(run_marcus_prospecting, CronTrigger(hour=8, minute=32, timezone=CST),
    id="marcus_daily_prospecting", name="Marcus Daily Prospecting",
    replace_existing=True, misfire_grace_time=3600)

scheduler.add_job(run_ryan_data_prospecting, CronTrigger(hour=8, minute=34, timezone=CST),
    id="ryan_data_daily_prospecting", name="Ryan Data Daily Prospecting",
    replace_existing=True, misfire_grace_time=3600)

# Marketing â 9:00, 9:02, 9:04
scheduler.add_job(run_zoe_content, CronTrigger(hour=9, minute=0, timezone=CST),
    id="zoe_daily_content", name="Zoe Daily Content",
    replace_existing=True, misfire_grace_time=3600)

scheduler.add_job(run_sofia_content, CronTrigger(hour=9, minute=2, timezone=CST),
    id="sofia_daily_content", name="Sofia Daily Content",
    replace_existing=True, misfire_grace_time=3600)

scheduler.add_job(run_chase_content, CronTrigger(hour=9, minute=4, timezone=CST),
    id="chase_daily_content", name="Chase Daily Content",
    replace_existing=True, misfire_grace_time=3600)

# Client Success â 9:30, 9:32
scheduler.add_job(run_jennifer_retention, CronTrigger(hour=9, minute=30, timezone=CST),
    id="jennifer_daily_retention", name="Jennifer Daily Retention",
    replace_existing=True, misfire_grace_time=3600)

scheduler.add_job(run_carlos_retention, CronTrigger(hour=9, minute=32, timezone=CST),
    id="carlos_daily_retention", name="Carlos Daily Retention",
    replace_existing=True, misfire_grace_time=3600)

# Specialists â 10:00, 10:02, 10:04
scheduler.add_job(run_nova_intelligence, CronTrigger(hour=10, minute=0, timezone=CST),
    id="nova_daily_intelligence", name="Nova Daily Intelligence",
    replace_existing=True, misfire_grace_time=3600)

scheduler.add_job(run_atlas_intel, CronTrigger(hour=10, minute=2, timezone=CST),
    id="atlas_daily_intel", name="Atlas Daily Intel",
    replace_existing=True, misfire_grace_time=3600)

scheduler.add_job(run_phoenix_delivery, CronTrigger(hour=10, minute=4, timezone=CST),
    id="phoenix_daily_delivery", name="Phoenix Daily Delivery",
    replace_existing=True, misfire_grace_time=3600)


# ââ FastAPI App ââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

@asynccontextmanager
async def lifespan(app: FastAPI):
    # ââ DB init â never crash startup if Postgres isn't ready âââââââââââââââââ
    try:
        init_db()
    except Exception as e:
        logging.warning(
            f"[DB] Startup init failed â app will run without Postgres: {e}"
        )

    # ââ Scheduler â never crash startup if APScheduler misfires ââââââââââââââ
    try:
        scheduler.start()
        logging.info("[Scheduler] Started â 13 agent jobs registered.")
    except Exception as e:
        logging.error(f"[Scheduler] Failed to start: {e}")

    yield

    # ââ Shutdown âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
    try:
        scheduler.shutdown(wait=False)
    except Exception:
        pass
    logging.info("[Scheduler] Shut down.")


app = FastAPI(
    title="Paperclip Multi-Agent API",
    description=(
        "AI agent platform powering The AI Phone Guy, Calling Digital, "
        "and Automotive Intelligence."
    ),
    version="3.0.0",
    lifespan=lifespan,
)


# ââ Auth âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

class AuthRequest(BaseModel):
    agent_id: str
    message: str


def validate_key(authorization: Optional[str] = Header(None)):
    if not API_KEYS:
        return True
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header.")
    token = authorization.split("Bearer ")[1].strip()
    if token not in API_KEYS:
        raise HTTPException(status_code=403, detail="Invalid API key.")
    return True


def get_agent_business(agent_id: str) -> str:
    for biz_key, biz in BUSINESSES.items():
        if agent_id in biz["agents"]:
            return biz["name"]
    return "Unknown Business"


# ââ Routes âââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââââ

@app.get("/")
async def root():
    return {"status": "ok"}


@app.post("/chat")
async def chat(request: AuthRequest, authorization: Optional[str] = Header(None)):
    validate_key(authorization)
    agent_id = request.agent_id.lower().strip()
    if agent_id not in AGENTS:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found.")
    agent = AGENTS[agent_id]
    business_name = get_agent_business(agent_id)
    try:
        task = Task(
            description=request.message,
            expected_output="A detailed, actionable response from the agent.",
            agent=agent,
        )
        crew = Crew(
            agents=[agent],
            tasks=[task],
            process=Process.sequential,
            memory=False,
            verbose=False,
        )
        result = crew.kickoff()
        return {
            "agent": agent_id,
            "business": business_name,
            "response": str(result),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Agent error: {type(e).__name__}: {e}")


@app.get("/logs/{agent_name}")
async def get_agent_log(agent_name: str):
    """Return the most recent scheduled run for the given agent (Postgres primary, filesystem fallback)."""
    agent_name = agent_name.lower().strip()
    if agent_name not in AGENTS:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_name}' not found.")

    # ââ Postgres primary ââââââââââââââââââââââââââââââââââââââââââââââââââââââââ
    if DATABASE_URL:
        try:
            with _db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT content, log_type, run_date, created_at "
                        "FROM agent_logs WHERE agent_name = %s "
                        "ORDER BY created_at DESC LIMIT 1",
                        (agent_name,),
                    )
                    row = cur.fetchone()
            if row:
                content, log_type, run_date, created_at = row
                return PlainTextResponse(
                    content=content,
                    media_type="text/plain",
                    headers={
                        "X-Log-Type": log_type,
                        "X-Run-Date": str(run_date),
                        "X-Created-At": str(created_at),
                    },
                )
        except Exception as e:
            logging.error(f"[DB] get_agent_log query failed: {e}")
            # fall through to filesystem

    # ââ Filesystem fallback ââââââââââââââââââââââââââââââââââââââââââââââââââââ
    pattern = os.path.join("logs", f"{agent_name}_*.log")
    matches = sorted(glob.glob(pattern), reverse=True)
    if not matches:
        raise HTTPException(status_code=404, detail=f"No logs found for agent '{agent_name}'.")
    with open(matches[0], "r") as f:
        content = f.read()
    return PlainTextResponse(content=content, media_type="text/plain")


@app.get("/logs/{agent_name}/history")
async def get_agent_log_history(agent_name: str, limit: int = 30):
    """Return metadata for the last N runs for the given agent (Postgres only)."""
    agent_name = agent_name.lower().strip()
    if agent_name not in AGENTS:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_name}' not found.")
    if not DATABASE_URL:
        raise HTTPException(status_code=503, detail="Postgres not configured.")
    try:
        with _db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, log_type, run_date, created_at, LEFT(content, 200) AS preview "
                    "FROM agent_logs WHERE agent_name = %s "
                    "ORDER BY created_at DESC LIMIT %s",
                    (agent_name, limit),
                )
                rows = cur.fetchall()
        return JSONResponse(content={
            "agent": agent_name,
            "runs": [
                {
                    "id": r[0],
                    "log_type": r[1],
                    "run_date": str(r[2]),
                    "created_at": str(r[3]),
                    "preview": r[4],
                }
                for r in rows
            ],
        })
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"DB error: {e}")


@app.get("/health")
async def health():
    jobs_info = []
    for job in scheduler.get_jobs():
        jobs_info.append({
            "id": job.id,
            "name": job.name,
            "next_run": str(job.next_run_time) if job.next_run_time else "paused",
        })
    return {
        "status": "ok",
        "version": "3.0.0",
        "scheduler": "running" if scheduler.running else "stopped",
        "jobs_registered": len(jobs_info),
        "postgres": "connected" if DATABASE_URL else "not configured",
        "jobs": jobs_info,
    }
