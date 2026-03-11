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
    _tmp_log.warning(f"[DB] psycopg2 import failed — Postgres disabled: {_psycopg_err}")
    psycopg = None  # type: ignore
    _PSYCOPG_OK = False


# ── Agent Imports ──────────────────────────────────────────────────────────────

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


# ── Config ─────────────────────────────────────────────────────────────────────

CST = pytz.timezone("America/Chicago")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

os.makedirs("logs", exist_ok=True)

API_KEYS = set(filter(None, os.getenv("API_KEYS", "").split(",")))

DATABASE_URL = os.getenv("DATABASE_URL", "")


# ── Database ───────────────────────────────────────────────────────────────────

def _db_url() -> str:
    """Normalize Railway's postgres:// URL to postgresql:// for psycopg3."""
    url = DATABASE_URL
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


@contextmanager
def _db():
    """Thread-safe single-use DB connection context manager.

    connect_timeout=5 ensures we fail fast if the DB socket isn't ready
    at boot time instead of hanging indefinitely and blocking the event loop.
    """
    if psycopg is None:
        raise RuntimeError("psycopg2 not available — Postgres disabled")
    conn = psycopg.connect(_db_url(), connect_timeout=5)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """Create agent_logs table and index if they don't exist."""
    if not DATABASE_URL:
        logging.warning("[DB] DATABASE_URL not set — Postgres logging disabled, using filesystem.")
        return
    try:
        with _db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS agent_logs (
                        id          SERIAL PRIMARY KEY,
                        agent_name  TEXT        NOT NULL,
                        log_type    TEXT        NOT NULL,
                        run_date    DATE        NOT NULL,
                        content     TEXT        NOT NULL,
                        created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    );
                    CREATE INDEX IF NOT EXISTS idx_agent_logs_lookup
                        ON agent_logs (agent_name, created_at DESC);
                """)
        logging.info("[DB] agent_logs table ready.")
    except Exception as e:
        logging.error(f"[DB] init_db failed: {e}")


def persist_log(agent_name: str, log_type: str, content: str):
    """Write an agent run result to Postgres (primary) and filesystem (backup)."""
    today = datetime.datetime.now(CST).strftime("%Y-%m-%d")
    run_date = datetime.date.fromisoformat(today)

    # ── Filesystem backup (always) ─────────────────────────────────────────────
    log_path = os.path.join("logs", f"{agent_name}_{log_type}_{today}.log")
    try:
        with open(log_path, "w") as f:
            f.write(content)
    except Exception as e:
        logging.warning(f"[FS] Could not write {log_path}: {e}")

    # ── Postgres primary ───────────────────────────────────────────────────────
    if not DATABASE_URL:
        return
    try:
        with _db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO agent_logs (agent_name, log_type, run_date, content) "
                    "VALUES (%s, %s, %s, %s)",
                    (agent_name, log_type, run_date, content),
                )
        logging.info(f"[DB] Persisted {agent_name}/{log_type} for {today}")
    except Exception as e:
        logging.error(f"[DB] persist_log failed for {agent_name}: {e}")


# ── Agent Registry ─────────────────────────────────────────────────────────────

AGENTS = {
    # The AI Phone Guy
    "alex": alex,
    "tyler": tyler,
    "zoe": zoe,
    "jennifer": jennifer,
    # Calling Digital
    "dek": dek,
    "marcus": marcus,
    "sofia": sofia,
    "carlos": carlos,
    "nova": nova,
    # Automotive Intelligence
    "michael_meta": michael_meta,
    "ryan_data": ryan_data,
    "chase": chase,
    "atlas": atlas,
    "phoenix": phoenix,
}

# Maps each agent to its log_type label (matches log file naming)
LOG_TYPES = {
    "alex":         "briefing",
    "dek":          "briefing",
    "michael_meta": "briefing",
    "tyler":        "prospecting",
    "marcus":       "prospecting",
    "ryan_data":    "prospecting",
    "zoe":          "content",
    "sofia":        "content",
    "chase":        "content",
    "jennifer":     "retention",
    "carlos":       "retention",
    "nova":         "intelligence",
    "atlas":        "intel",
    "phoenix":      "delivery",
}

BUSINESSES = {
    "aiphoneguy": {
        "name": "The AI Phone Guy",
        "agents": ["alex", "tyler", "zoe", "jennifer"],
    },
    "callingdigital": {
        "name": "Calling Digital",
        "agents": ["dek", "marcus", "sofia", "carlos", "nova"],
    },
    "autointelligence": {
        "name": "Automotive Intelligence",
        "agents": ["michael_meta", "ryan_data", "chase", "atlas", "phoenix"],
    },
}


# ── Scheduler ──────────────────────────────────────────────────────────────────

scheduler = BackgroundScheduler()


# ── CEO Briefings ── 8:00, 8:02, 8:04 CST ─────────────────────────────────────

def run_alex_daily_briefing():
    try:
        task = Task(
            description=(
                "Search for today's top news on AI receptionist technology, voice AI for small business, "
                "and DFW local service business trends. Search for competitor activity — any new launches, "
                "pricing changes, or marketing pushes from competing AI answering services. "
                "Identify the top 3 strategic opportunities or threats for The AI Phone Guy right now. "
                "End with one specific action item for the team today."
            ),
            expected_output=(
                "CEO daily briefing: (1) Top 3 industry headlines with strategic implications, "
                "(2) Competitor activity summary, (3) Top opportunity or threat, "
                "(4) One action item for today."
            ),
            agent=alex,
        )
        crew = Crew(agents=[alex], tasks=[task], process=Process.sequential, memory=False, verbose=False)
        result = crew.kickoff()
        persist_log("alex", "briefing", str(result))
        logging.info("[Scheduler] Alex briefing complete.")
    except Exception as e:
        logging.error(f"[Scheduler] Alex briefing failed: {type(e).__name__}: {e}")


def run_dek_daily_briefing():
    try:
        task = Task(
            description=(
                "Search for today's top news on digital marketing agency trends, AI implementation "
                "consulting, and small business tech adoption in Dallas. Search for competitor activity — "
                "other Dallas agencies pivoting to AI, new AI consulting offers, pricing changes. "
                "Identify the top 3 strategic opportunities or threats for Calling Digital right now. "
                "End with one specific action item for the team today."
            ),
            expected_output=(
                "CEO daily briefing: (1) Top 3 industry headlines with strategic implications, "
                "(2) Competitor agency activity summary, (3) Top opportunity or threat, "
                "(4) One action item for today."
            ),
            agent=dek,
        )
        crew = Crew(agents=[dek], tasks=[task], process=Process.sequential, memory=False, verbose=False)
        result = crew.kickoff()
        persist_log("dek", "briefing", str(result))
        logging.info("[Scheduler] Dek briefing complete.")
    except Exception as e:
        logging.error(f"[Scheduler] Dek briefing failed: {type(e).__name__}: {e}")


def run_michael_meta_daily_briefing():
    try:
        task = Task(
            description=(
                "Search for today's top news on AI in automotive retail, dealership technology trends, "
                "and DFW auto market activity. Search for competitor activity — other AI consultants "
                "or vendors targeting car dealerships. "
                "Identify the top 3 strategic opportunities or threats for Automotive Intelligence right now. "
                "End with one specific action item for the team today."
            ),
            expected_output=(
                "CEO daily briefing: (1) Top 3 auto industry AI headlines with implications, "
                "(2) Competitor vendor activity summary, (3) Top opportunity or threat, "
                "(4) One action item for today."
            ),
            agent=michael_meta,
        )
        crew = Crew(agents=[michael_meta], tasks=[task], process=Process.sequential, memory=False, verbose=False)
        result = crew.kickoff()
        persist_log("michael_meta", "briefing", str(result))
        logging.info("[Scheduler] Michael Meta briefing complete.")
    except Exception as e:
        logging.error(f"[Scheduler] Michael Meta briefing failed: {type(e).__name__}: {e}")


# ── Sales Prospecting ── 8:30, 8:32, 8:34 CST ─────────────────────────────────

def run_tyler_prospecting():
    try:
        task = Task(
            description=(
                "Search for local service businesses in Aubrey, Celina, Prosper, Pilot Point, "
                "and Little Elm TX — HVAC, plumbing, roofing, dental, and personal injury law. "
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
        persist_log("tyler", "prospecting", str(result))
        logging.info("[Scheduler] Tyler prospecting complete.")
    except Exception as e:
        logging.error(f"[Scheduler] Tyler prospecting failed: {type(e).__name__}: {e}")


def run_marcus_prospecting():
    try:
        task = Task(
            description=(
                "Search for small and mid-size businesses in Dallas that need digital marketing help — "
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


# ── Marketing Content ── 9:00, 9:02, 9:04 CST ─────────────────────────────────

def run_zoe_content():
    try:
        task = Task(
            description=(
                "Search for trending topics in local service business marketing, AI for small business, "
                "and DFW small business news today. Search for competitor content from other AI receptionist "
                "brands — what's performing well, what hooks are working. "
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
                "Search for trending AI and automotive retail news today — dealership technology stories, "
                "auto industry AI announcements, or DFW dealer news. "
                "Search for what automotive thought leaders are publishing on LinkedIn and in newsletters. "
                "Design 3 content pieces for Automotive Intelligence's full marketing funnel: "
                "one LinkedIn thought leadership post for Michael Meta's personal brand, "
                "one What The Prompt? newsletter section (educational, non-salesy), "
                "one cold email subject line and opener for Ryan Data's sequences. "
                "For each: hook, key insight, format, and CTA."
            ),
            expected_output=(
                "Daily content plan: (1) LinkedIn post ready to publish — hook, body, CTA. "
                "(2) Newsletter section — topic, angle, 3 key points. "
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


# ── Client Success ── 9:30, 9:32 CST ──────────────────────────────────────────────

def run_jennifer_retention():
    try:
        task = Task(
            description=(
                "Search for current best practices in client retention for SaaS and AI subscription services. "
                "Search for common objections and churn reasons for AI receptionist tools. "
                "Identify upsell and expansion triggers — what behaviors indicate a Starter client "
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


def run_carlos_retention():
    try:
        task = Task(
            description=(
                "Search for current best practices in digital marketing agency client retention "
                "and account management. Search for common reasons small businesses cancel marketing "
                "retainers and what successful agencies do to prevent it. "
                "Identify upsell triggers — what service results indicate a client is ready for "
                "AI consulting or additional Calling Digital services. "
                "Develop 3 proactive talking points for client check-ins today: "
                "one celebrating measurable results, one proactively addressing a potential concern, "
                "one positioning the AI consulting conversation."
            ),
            expected_output=(
                "Daily retention brief: (1) 3 proactive talking points for client calls. "
                "(2) GRR protection strategies and early warning signals. "
                "(3) Upsell opportunities to flag to Marcus and Dek. "
                "(4) One retention check-in message template ready to send."
            ),
            agent=carlos,
        )
        crew = Crew(agents=[carlos], tasks=[task], process=Process.sequential, memory=False, verbose=False)
        result = crew.kickoff()
        persist_log("carlos", "retention", str(result))
        logging.info("[Scheduler] Carlos retention complete.")
    except Exception as e:
        logging.error(f"[Scheduler] Carlos retention failed: {type(e).__name__}: {e}")


# ── Specialists ── 10:00, 10:02, 10:04 CST ────────────────────────────────────────────

def run_nova_intelligence():
    try:
        task = Task(
            description=(
                "Search for AI tools, platforms, and updates released or announced this week "
                "relevant to small and mid-size businesses: automation tools, AI assistants, "
                "workflow optimization, customer service AI, and marketing AI. "
                "Identify 3 specific implementation opportunities for Calling Digital's SMB clients: "
                "which tool, which type of client it's best for, what problem it solves, "
                "and how Calling Digital can deliver it as a billable service."
            ),
            expected_output=(
                "Weekly AI intelligence report: (1) Top 5 AI tool releases or updates relevant to SMBs. "
                "(2) 3 implementation opportunities with tool, client profile, problem solved, and service approach. "
                "(3) One AI trend that should inform Calling Digital's consulting offer this week."
            ),
            agent=nova,
        )
        crew = Crew(agents=[nova], tasks=[task], process=Process.sequential, memory=False, verbose=False)
        result = crew.kickoff()
        persist_log("nova", "intelligence", str(result))
        logging.info("[Scheduler] Nova intelligence complete.")
    except Exception as e:
        logging.error(f"[Scheduler] Nova intelligence failed: {type(e).__name__}: {e}")


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
                "(3) Competitive activity — other AI vendors approaching DFW dealerships."
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


# ── Register All 13 Scheduler Jobs ──────────────────────────────────────────────

# CEOs — 8:00, 8:02, 8:04
scheduler.add_job(run_alex_daily_briefing, CronTrigger(hour=8, minute=0, timezone=CST),
    id="alex_daily_briefing", name="Alex Daily Briefing",
    replace_existing=True, misfire_grace_time=3600)

scheduler.add_job(run_dek_daily_briefing, CronTrigger(hour=8, minute=2, timezone=CST),
    id="dek_daily_briefing", name="Dek Daily Briefing",
    replace_existing=True, misfire_grace_time=3600)

scheduler.add_job(run_michael_meta_daily_briefing, CronTrigger(hour=8, minute=4, timezone=CST),
    id="michael_meta_daily_briefing", name="Michael Meta Daily Briefing",
    replace_existing=True, misfire_grace_time=3600)

# Sales — 8:30, 8:32, 8:34
scheduler.add_job(run_tyler_prospecting, CronTrigger(hour=8, minute=30, timezone=CST),
    id="tyler_daily_prospecting", name="Tyler Daily Prospecting",
    replace_existing=True, misfire_grace_time=3600)

scheduler.add_job(run_marcus_prospecting, CronTrigger(hour=8, minute=32, timezone=CST),
    id="marcus_daily_prospecting", name="Marcus Daily Prospecting",
    replace_existing=True, misfire_grace_time=3600)

scheduler.add_job(run_ryan_data_prospecting, CronTrigger(hour=8, minute=34, timezone=CST),
    id="ryan_data_daily_prospecting", name="Ryan Data Daily Prospecting",
    replace_existing=True, misfire_grace_time=3600)

# Marketing — 9:00, 9:02, 9:04
scheduler.add_job(run_zoe_content, CronTrigger(hour=9, minute=0, timezone=CST),
    id="zoe_daily_content", name="Zoe Daily Content",
    replace_existing=True, misfire_grace_time=3600)

scheduler.add_job(run_sofia_content, CronTrigger(hour=9, minute=2, timezone=CST),
    id="sofia_daily_content", name="Sofia Daily Content",
    replace_existing=True, misfire_grace_time=3600)

scheduler.add_job(run_chase_content, CronTrigger(hour=9, minute=4, timezone=CST),
    id="chase_daily_content", name="Chase Daily Content",
    replace_existing=True, misfire_grace_time=3600)

# Client Success — 9:30, 9:32
scheduler.add_job(run_jennifer_retention, CronTrigger(hour=9, minute=30, timezone=CST),
    id="jennifer_daily_retention", name="Jennifer Daily Retention",
    replace_existing=True, misfire_grace_time=3600)

scheduler.add_job(run_carlos_retention, CronTrigger(hour=9, minute=32, timezone=CST),
    id="carlos_daily_retention", name="Carlos Daily Retention",
    replace_existing=True, misfire_grace_time=3600)

# Specialists — 10:00, 10:02, 10:04
scheduler.add_job(run_nova_intelligence, CronTrigger(hour=10, minute=0, timezone=CST),
    id="nova_daily_intelligence", name="Nova Daily Intelligence",
    replace_existing=True, misfire_grace_time=3600)

scheduler.add_job(run_atlas_intel, CronTrigger(hour=10, minute=2, timezone=CST),
    id="atlas_daily_intel", name="Atlas Daily Intel",
    replace_existing=True, misfire_grace_time=3600)

scheduler.add_job(run_phoenix_delivery, CronTrigger(hour=10, minute=4, timezone=CST),
    id="phoenix_daily_delivery", name="Phoenix Daily Delivery",
    replace_existing=True, misfire_grace_time=3600)


# ── FastAPI App ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── DB init — never crash startup if Postgres isn't ready ─────────────────
    try:
        init_db()
    except Exception as e:
        logging.warning(
            f"[DB] Startup init failed — app will run without Postgres: {e}"
        )

    # ── Scheduler — never crash startup if APScheduler misfires ──────────────────
    try:
        scheduler.start()
        logging.info("[Scheduler] Started — 13 agent jobs registered.")
    except Exception as e:
        logging.error(f"[Scheduler] Failed to start: {e}")

    yield

    # ── Shutdown ──────────────────────────────────────────────────────────────────
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


# ── Auth ─────────────────────────────────────────────────────────────────────────

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


# ── Routes ─────────────────────────────────────────────────────────────────────

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

    # ── Postgres primary ──────────────────────────────────────────────────────────────────
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

    # ── Filesystem fallback ────────────────────────────────────────────────────────────────────
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
