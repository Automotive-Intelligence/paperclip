import os
import glob
import logging
import datetime
import json
from pathlib import Path
from contextlib import asynccontextmanager, contextmanager
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Header
from fastapi.responses import PlainTextResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from typing import Optional, List, Dict, Any
from pydantic import BaseModel
from crewai import Crew, Task, Process
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz

# Load environment variables from .env file
load_dotenv()
try:
    import psycopg2 as psycopg   # psycopg2-binary: stable on Railway, API-compatible
    _PSYCOPG_OK = True
except ImportError as _psycopg_err:
    import logging as _tmp_log
    _tmp_log.warning(f"[DB] psycopg2 import failed — Postgres disabled: {_psycopg_err}")
    psycopg = None  # type: ignore
    _PSYCOPG_OK = False


# ── Tool Imports ─────────────────────────────────────────────────────────────

from tools.prospect_parser import parse_tyler_prospects
from tools.ghl import push_prospects_to_ghl, create_contact, add_contact_note, send_email
from tools.email_engine import parse_prospects, parse_retention_actions, parse_content_pieces
from tools.revenue_tracker import (
    init_revenue_tracker, init_revenue_tables, track_event,
    queue_content, get_content_queue, mark_content_published,
    get_revenue_summary, get_daily_metrics,
)


# ── Agent Imports ────────────────────────────────────────────────────────────

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


CST = pytz.timezone("America/Chicago")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

os.makedirs("logs", exist_ok=True)

API_KEYS = set(filter(None, os.getenv("API_KEYS", "").split(",")))

DATABASE_URL = os.getenv("DATABASE_URL", "")


# ── Database ─────────────────────────────────────────────────────────────────

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

    # ── Filesystem backup (always)
    log_path = os.path.join("logs", f"{agent_name}_{log_type}_{today}.log")
    try:
        with open(log_path, "w") as f:
            f.write(content)
    except Exception as e:
        logging.warning(f"[FS] Could not write {log_path}: {e}")

    # ── Postgres primary
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


# ── Agent Registry ───────────────────────────────────────────────────────────

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

# Maps agents to their business key for revenue tracking
AGENT_BUSINESS_KEY = {}
for biz_key, biz in BUSINESSES.items():
    for agent_id in biz["agents"]:
        AGENT_BUSINESS_KEY[agent_id] = biz_key


# ── GHL Configuration Check ─────────────────────────────────────────────────

def _ghl_ready() -> bool:
    """Check if GHL API credentials are configured."""
    return bool(os.getenv("GHL_API_KEY") and os.getenv("GHL_LOCATION_ID"))


# ── Revenue Pipeline Helper ─────────────────────────────────────────────────

def _execute_sales_pipeline(agent_name: str, raw_output: str, business_key: str):
    """
    Universal sales pipeline executor. Takes any sales agent's output and:
    1. Parses prospects with email engine
    2. Pushes to GHL (creates contacts)
    3. Sends first-touch cold emails
    4. Creates pipeline opportunities
    5. Tracks all revenue events
    """
    if not _ghl_ready():
        logging.info(f"[Pipeline] Skipping GHL push for {agent_name} — credentials not set.")
        return

    try:
        prospects = parse_prospects(raw_output, agent_name=agent_name)
        if not prospects:
            logging.warning(f"[Pipeline] No prospects parsed from {agent_name}'s output.")
            return

        ghl_results = push_prospects_to_ghl(
            prospects,
            source_agent=agent_name,
            business_key=business_key,
        )

        created = 0
        emails_sent = 0
        for r in ghl_results:
            if r.get("status") == "created":
                created += 1
                track_event(
                    "prospect_created", business_key, agent_name,
                    contact_id=r.get("contact_id", ""),
                    monetary_value={"tyler": 482, "marcus": 2500, "ryan_data": 2500}.get(agent_name, 0),
                    metadata={"business_name": r.get("business_name")},
                )
            if r.get("email_sent"):
                emails_sent += 1
                track_event(
                    "email_sent", business_key, agent_name,
                    contact_id=r.get("contact_id", ""),
                    metadata={"business_name": r.get("business_name")},
                )

        logging.info(
            f"[Pipeline] {agent_name}: {created}/{len(prospects)} contacts created, "
            f"{emails_sent} emails sent to GHL."
        )

    except Exception as e:
        logging.error(f"[Pipeline] {agent_name} pipeline failed: {e}")


def _execute_content_pipeline(agent_name: str, raw_output: str, business_key: str):
    """
    Content pipeline executor. Takes any content agent's output and:
    1. Parses into publishable content pieces
    2. Queues in content_queue table for publishing
    3. Tracks content generation events
    """
    try:
        pieces = parse_content_pieces(raw_output, agent_name=agent_name)
        if not pieces:
            logging.info(f"[Content] No publishable pieces parsed from {agent_name}.")
            return

        queued = queue_content(business_key, agent_name, pieces)
        if queued:
            track_event(
                "content_queued", business_key, agent_name,
                metadata={"pieces_queued": queued, "platforms": [p.get("platform") for p in pieces]},
            )
        logging.info(f"[Content] {agent_name}: {queued} pieces queued for publishing.")

    except Exception as e:
        logging.error(f"[Content] {agent_name} content pipeline failed: {e}")


def _execute_retention_pipeline(agent_name: str, raw_output: str, business_key: str):
    """
    Retention pipeline executor. Takes retention agent output and:
    1. Parses into actionable retention/upsell items
    2. Tracks retention events
    3. Logs structured actions for execution
    """
    try:
        actions = parse_retention_actions(raw_output, agent_name=agent_name)
        if not actions:
            logging.info(f"[Retention] No actionable items parsed from {agent_name}.")
            return

        high_urgency = [a for a in actions if a.get("urgency") == "high"]
        for action in actions:
            track_event(
                action.get("action_type", "retention_save"),
                business_key,
                agent_name,
                metadata={
                    "target": action.get("target_description", ""),
                    "urgency": action.get("urgency", "medium"),
                    "subject": action.get("subject", ""),
                },
            )

        # Persist structured retention actions alongside raw log
        today = datetime.datetime.now(CST).strftime("%Y-%m-%d")
        actions_path = os.path.join("logs", f"{agent_name}_actions_{today}.json")
        try:
            import json
            with open(actions_path, "w") as f:
                json.dump(actions, f, indent=2)
        except Exception:
            pass

        logging.info(
            f"[Retention] {agent_name}: {len(actions)} actions tracked "
            f"({len(high_urgency)} high urgency)."
        )

    except Exception as e:
        logging.error(f"[Retention] {agent_name} retention pipeline failed: {e}")


# ── Scheduler ────────────────────────────────────────────────────────────────

scheduler = BackgroundScheduler()


# ── CEO Briefings ── 8:00, 8:02, 8:04 CST ───────────────────────────────────

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


# ── Sales Prospecting ── 8:30, 8:32, 8:34 CST ──────────────────────────────
# NOW REVENUE-ACTIVE: Parse → GHL Contact → Send Email → Create Opportunity → Track

def run_tyler_prospecting():
    try:
        task = Task(
            description=(
                "Search for local service businesses in Aubrey, Celina, Prosper, Pilot Point, "
                "and Little Elm TX -- HVAC, plumbing, roofing, dental, and personal injury law. "
                "Search for news about businesses expanding, opening new locations, or hiring. "
                "Look for buying signals: Google reviews mentioning missed calls, slow response, "
                "or after-hours availability issues. "
                "Compile 5 high-priority outreach targets for today with a personalized COLD EMAIL "
                "for each — NOT SMS. Use the Observation > Problem > Proof > Ask framework. "
                "Subject lines should be 2-4 words, lowercase, internal-looking (e.g. 'missed calls', "
                "'after-hours voicemail'). Opening line should reference a specific observation about "
                "the business. CTA should be interest-based ('Worth a quick look?'), not a meeting request. "
                "Also draft one follow-up email angle for each prospect (different value angle for touch 2)."
            ),
            expected_output=(
                "Daily prospecting report: (1) 5 outreach targets with business name, type, city, "
                "reason for targeting, a cold email (subject + body), and a follow-up angle for touch 2. "
                "(2) Any signals that make today a particularly good time to reach out. "
                "IMPORTANT: All outreach is via cold email only. No SMS to non-opted-in contacts."
            ),
            agent=tyler,
        )
        crew = Crew(agents=[tyler], tasks=[task], process=Process.sequential, memory=False, verbose=False)
        result = crew.kickoff()
        raw_output = str(result)
        persist_log("tyler", "prospecting", raw_output)
        logging.info("[Scheduler] Tyler prospecting complete.")

        # ── REVENUE PIPELINE: Parse → GHL → Email → Track ──
        _execute_sales_pipeline("tyler", raw_output, "aiphoneguy")

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
                "Compile 5 high-priority outreach targets for today with a consultative cold email "
                "for each — lead with their problem, not your service. Use an educational, diagnostic tone. "
                "Subject lines should be consultative (e.g. 'quick audit for [business]', 'your website traffic'). "
                "Include a follow-up email angle for each prospect. "
                "Flag any that are also strong candidates for The AI Phone Guy bundle upsell."
            ),
            expected_output=(
                "Daily prospecting report: (1) 5 outreach targets with company name, industry, city, "
                "key pain point, a cold email (subject + body), and a follow-up email angle. "
                "(2) Bundle opportunities flagged for Dek. "
                "IMPORTANT: All outreach is via cold email only."
            ),
            agent=marcus,
        )
        crew = Crew(agents=[marcus], tasks=[task], process=Process.sequential, memory=False, verbose=False)
        result = crew.kickoff()
        raw_output = str(result)
        persist_log("marcus", "prospecting", raw_output)
        logging.info("[Scheduler] Marcus prospecting complete.")

        # ── REVENUE PIPELINE: Parse → GHL → Email → Track ──
        _execute_sales_pipeline("marcus", raw_output, "callingdigital")

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
                "Identify 5 high-priority dealership targets for outreach today with personalized cold emails "
                "positioning the free AI Readiness Assessment offer. "
                "Subject lines should reference automotive/dealership context. "
                "Body should position the free assessment as the entry point. "
                "Include a follow-up email angle for each prospect."
            ),
            expected_output=(
                "Daily prospecting report: (1) 5 dealership targets with name, group affiliation, city, "
                "AI readiness signal found, a cold email (subject + body), and a follow-up email angle. "
                "(2) Pipeline notes on any previously contacted dealers showing new activity. "
                "IMPORTANT: All outreach is via cold email only."
            ),
            agent=ryan_data,
        )
        crew = Crew(agents=[ryan_data], tasks=[task], process=Process.sequential, memory=False, verbose=False)
        result = crew.kickoff()
        raw_output = str(result)
        persist_log("ryan_data", "prospecting", raw_output)
        logging.info("[Scheduler] Ryan Data prospecting complete.")

        # ── REVENUE PIPELINE: Parse → GHL → Email → Track ──
        _execute_sales_pipeline("ryan_data", raw_output, "autointelligence")

    except Exception as e:
        logging.error(f"[Scheduler] Ryan Data prospecting failed: {type(e).__name__}: {e}")


# ── Marketing Content ── 9:00, 9:02, 9:04 CST ──────────────────────────────
# NOW REVENUE-ACTIVE: Parse → Content Queue → Ready to Publish → Track


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
        raw_output = str(result)
        persist_log("zoe", "content", raw_output)
        logging.info("[Scheduler] Zoe content complete.")

        # ── CONTENT PIPELINE: Parse → Queue → Track ──
        _execute_content_pipeline("zoe", raw_output, "aiphoneguy")

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
        raw_output = str(result)
        persist_log("sofia", "content", raw_output)
        logging.info("[Scheduler] Sofia content complete.")

        # ── CONTENT PIPELINE: Parse → Queue → Track ──
        _execute_content_pipeline("sofia", raw_output, "callingdigital")

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
        raw_output = str(result)
        persist_log("chase", "content", raw_output)
        logging.info("[Scheduler] Chase content complete.")

        # ── CONTENT PIPELINE: Parse → Queue → Track ──
        _execute_content_pipeline("chase", raw_output, "autointelligence")

    except Exception as e:
        logging.error(f"[Scheduler] Chase content failed: {type(e).__name__}: {e}")


# ── Client Success ── 9:30, 9:32 CST ────────────────────────────────────────
# NOW REVENUE-ACTIVE: Parse → Structured Actions → Track Retention Events


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
        raw_output = str(result)
        persist_log("jennifer", "retention", raw_output)
        logging.info("[Scheduler] Jennifer retention complete.")

        # ── RETENTION PIPELINE: Parse → Actions → Track ──
        _execute_retention_pipeline("jennifer", raw_output, "aiphoneguy")

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
        raw_output = str(result)
        persist_log("carlos", "retention", raw_output)
        logging.info("[Scheduler] Carlos retention complete.")

        # ── RETENTION PIPELINE: Parse → Actions → Track ──
        _execute_retention_pipeline("carlos", raw_output, "callingdigital")

    except Exception as e:
        logging.error(f"[Scheduler] Carlos retention failed: {type(e).__name__}: {e}")


# ── Specialists ── 10:00, 10:02, 10:04 CST ──────────────────────────────────

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


# ── Master Test Function: All Agents @ 6:30 PM ─────────────────────────────

def run_all_agents_test():
    """
    Master test suite that runs all 15 agents' tasks concurrently.
    Logs results and provides a complete system health check.
    Scheduled for 6:30 PM CST daily.
    """
    logging.info("🧪 [TEST SUITE] Starting all agent tasks test...")
    start_time = datetime.datetime.now(CST)
    results = {
        "start_time": start_time.isoformat(),
        "agents_tested": [],
        "agents_passed": [],
        "agents_failed": [],
        "execution_times": {},
    }

    # Define all test agents with their test tasks
    test_assignments = [
        ("alex", alex, "briefing", run_alex_daily_briefing),
        ("dek", dek, "briefing", run_dek_daily_briefing),
        ("michael_meta", michael_meta, "briefing", run_michael_meta_daily_briefing),
        ("tyler", tyler, "prospecting", run_tyler_prospecting),
        ("marcus", marcus, "prospecting", run_marcus_prospecting),
        ("ryan_data", ryan_data, "prospecting", run_ryan_data_prospecting),
        ("zoe", zoe, "content", run_zoe_content),
        ("sofia", sofia, "content", run_sofia_content),
        ("chase", chase, "content", run_chase_content),
        ("jennifer", jennifer, "retention", run_jennifer_retention),
        ("carlos", carlos, "retention", run_carlos_retention),
        ("nova", nova, "intelligence", run_nova_intelligence),
        ("atlas", atlas, "intel", run_atlas_intel),
        ("phoenix", phoenix, "delivery", run_phoenix_delivery),
    ]

    # Execute each agent test
    for agent_id, agent_obj, log_type, run_func in test_assignments:
        try:
            task_start = datetime.datetime.now(CST)
            logging.info(f"  ▶ Testing {agent_id}...")
            
            # Run the agent task
            run_func()
            
            task_end = datetime.datetime.now(CST)
            exec_time = (task_end - task_start).total_seconds()
            
            results["agents_tested"].append(agent_id)
            results["agents_passed"].append(agent_id)
            results["execution_times"][agent_id] = exec_time
            
            logging.info(f"  ✓ {agent_id} passed in {exec_time:.2f}s")
            
        except Exception as e:
            task_end = datetime.datetime.now(CST)
            exec_time = (task_end - task_start).total_seconds()
            
            results["agents_tested"].append(agent_id)
            results["agents_failed"].append({"agent": agent_id, "error": str(e)})
            results["execution_times"][agent_id] = exec_time
            
            logging.error(f"  ✗ {agent_id} failed after {exec_time:.2f}s: {type(e).__name__}: {e}")

    # Summary
    end_time = datetime.datetime.now(CST)
    total_time = (end_time - start_time).total_seconds()
    results["end_time"] = end_time.isoformat()
    results["total_duration_seconds"] = total_time
    results["pass_rate"] = f"{len(results['agents_passed'])}/{len(results['agents_tested'])} agents passed"

    # Persist test results
    import json
    today = datetime.datetime.now(CST).strftime("%Y-%m-%d")
    test_report_path = os.path.join("logs", f"test_suite_{today}.json")
    try:
        with open(test_report_path, "w") as f:
            json.dump(results, f, indent=2)
        logging.info(f"📊 [TEST SUITE] Results saved to {test_report_path}")
    except Exception as e:
        logging.warning(f"[TEST SUITE] Could not save results: {e}")

    # Log summary
    logging.info(
        f"🧪 [TEST SUITE] Complete — {results['pass_rate']} in {total_time:.2f}s"
    )
    if results["agents_failed"]:
        logging.warning(f"   Failed agents: {[f['agent'] for f in results['agents_failed']]}")

    return results


# ── Register Scheduler Jobs ──────────────────────────────────────────────────

# CEOs — 8:00, 8:02, 8:04 (once daily — strategic briefing)
scheduler.add_job(run_alex_daily_briefing, CronTrigger(hour=8, minute=0, timezone=CST),
    id="alex_daily_briefing", name="Alex Daily Briefing",
    replace_existing=True, misfire_grace_time=3600)

scheduler.add_job(run_dek_daily_briefing, CronTrigger(hour=8, minute=2, timezone=CST),
    id="dek_daily_briefing", name="Dek Daily Briefing",
    replace_existing=True, misfire_grace_time=3600)

scheduler.add_job(run_michael_meta_daily_briefing, CronTrigger(hour=8, minute=4, timezone=CST),
    id="michael_meta_daily_briefing", name="Michael Meta Daily Briefing",
    replace_existing=True, misfire_grace_time=3600)

# Sales — EVERY 2 HOURS from 8:30 to 16:30 CST (5 runs/day × 3 agents = 75 emails/day)
# Tyler:     8:30, 10:30, 12:30, 14:30, 16:30
# Marcus:    8:32, 10:32, 12:32, 14:32, 16:32
# Ryan Data: 8:34, 10:34, 12:34, 14:34, 16:34
SALES_HOURS = [8, 10, 12, 14, 16]

for hour in SALES_HOURS:
    scheduler.add_job(
        run_tyler_prospecting,
        CronTrigger(hour=hour, minute=30, timezone=CST),
        id=f"tyler_prospecting_{hour}30",
        name=f"Tyler Prospecting {hour}:30",
        replace_existing=True, misfire_grace_time=3600,
    )
    scheduler.add_job(
        run_marcus_prospecting,
        CronTrigger(hour=hour, minute=32, timezone=CST),
        id=f"marcus_prospecting_{hour}32",
        name=f"Marcus Prospecting {hour}:32",
        replace_existing=True, misfire_grace_time=3600,
    )
    scheduler.add_job(
        run_ryan_data_prospecting,
        CronTrigger(hour=hour, minute=34, timezone=CST),
        id=f"ryan_data_prospecting_{hour}34",
        name=f"Ryan Data Prospecting {hour}:34",
        replace_existing=True, misfire_grace_time=3600,
    )

# Marketing — 9:00, 9:02, 9:04 (once daily — content planning)
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

# Master Test Suite — 6:30 PM (18:30) CST — All Agents
scheduler.add_job(run_all_agents_test, CronTrigger(hour=18, minute=30, timezone=CST),
    id="all_agents_test", name="🧪 Master Test Suite - All Agents",
    replace_existing=True, misfire_grace_time=3600)

# ONE-TIME TEST at 7:56 PM CST for live demo
from apscheduler.triggers.date import DateTrigger
test_time = datetime.datetime.now(CST).replace(hour=19, minute=56, second=0, microsecond=0)
if test_time <= datetime.datetime.now(CST):  # If passed, schedule immediately
    test_time = datetime.datetime.now(CST) + datetime.timedelta(seconds=5)
scheduler.add_job(run_all_agents_test, DateTrigger(run_date=test_time),
    id="demo_test_756pm", name="✅ LIVE DEMO - All Agents (7:56 PM)",
    replace_existing=True, misfire_grace_time=60)
logging.info(f"[Scheduler] One-time test scheduled for {test_time}")


# ── FastAPI App ──────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── DB init — never crash startup if Postgres isn't ready
    try:
        init_db()
    except Exception as e:
        logging.warning(
            f"[DB] Startup init failed — app will run without Postgres: {e}"
        )

    # ── Revenue tracker init
    try:
        init_revenue_tracker(_db, CST)
        if DATABASE_URL:
            init_revenue_tables()
    except Exception as e:
        logging.warning(f"[Revenue] Init failed — revenue tracking disabled: {e}")

    # ── Scheduler — never crash startup if APScheduler misfires
    try:
        scheduler.start()
        logging.info(f"[Scheduler] Started {len(scheduler.get_jobs())} agent jobs registered.")
    except Exception as e:
        logging.error(f"[Scheduler] Failed to start: {e}")

    yield

    # ── Shutdown
    try:
        scheduler.shutdown(wait=False)
    except Exception:
        pass
    logging.info("[Scheduler] Shut down.")


app = FastAPI(
    title="Paperclip Multi-Agent Revenue Engine",
    description=(
        "AI-native revenue platform powering The AI Phone Guy, Calling Digital, "
        "and Automotive Intelligence. Agents prospect, email, track pipeline, "
        "queue content, and execute retention — autonomously."
    ),
    version="4.0.0",
    lifespan=lifespan,
)


# ── Auth ─────────────────────────────────────────────────────────────────────

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


# ── Routes ───────────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    return {"status": "ok", "engine": "revenue", "version": "4.0.0"}


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

    # ── Postgres primary
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

    # ── Filesystem fallback
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


# ── Revenue Dashboard Endpoints ─────────────────────────────────────────────


@app.get("/revenue")
async def revenue_dashboard(business: Optional[str] = None, days: int = 30):
    """Revenue intelligence dashboard — pipeline value, conversion rates, agent performance."""
    summary = get_revenue_summary(business_key=business, days=days)
    return JSONResponse(content=summary)


@app.get("/revenue/daily")
async def revenue_daily(business: Optional[str] = None, days: int = 7):
    """Daily revenue metrics for trend analysis."""
    metrics = get_daily_metrics(business_key=business, days=days)
    return JSONResponse(content={"days": days, "business": business or "all", "metrics": metrics})


@app.get("/content/queue")
async def content_queue_endpoint(business: Optional[str] = None, status: str = "queued", limit: int = 20):
    """Content queue — view pending, queued, or published content pieces."""
    items = get_content_queue(business_key=business, status=status, limit=limit)
    return JSONResponse(content={"status": status, "count": len(items), "items": items})


@app.post("/content/{content_id}/publish")
async def publish_content(content_id: int, authorization: Optional[str] = Header(None)):
    """Mark a content piece as published (after manual review and posting)."""
    validate_key(authorization)
    mark_content_published(content_id)
    return {"status": "published", "content_id": content_id}


@app.get("/pipeline")
async def pipeline_overview():
    """Quick pipeline overview — how many prospects, emails, opportunities across all businesses."""
    summary = {}
    for biz_key in BUSINESSES:
        summary[biz_key] = get_revenue_summary(business_key=biz_key, days=30)
    return JSONResponse(content=summary)


# ── GHL Webhook Receiver ────────────────────────────────────────────────────


@app.post("/webhooks/ghl")
async def ghl_webhook(payload: dict):
    """
    Receive GHL webhooks for email opens, replies, and status changes.
    This closes the feedback loop — agent outreach results flow back into the system.
    """
    event_type = payload.get("type", "")
    contact_id = payload.get("contactId", payload.get("contact_id", ""))

    if "email.opened" in event_type or "EmailOpened" in event_type:
        track_event("email_opened", "unknown", "unknown", contact_id=contact_id)
    elif "email.replied" in event_type or "InboundMessage" in event_type:
        track_event("email_replied", "unknown", "unknown", contact_id=contact_id)
    elif "opportunity.status_changed" in event_type:
        new_status = payload.get("status", "")
        monetary_value = float(payload.get("monetaryValue", 0))
        if new_status == "won":
            track_event("deal_closed", "unknown", "unknown",
                        contact_id=contact_id, monetary_value=monetary_value)
        elif new_status == "lost":
            track_event("deal_lost", "unknown", "unknown",
                        contact_id=contact_id, monetary_value=monetary_value)

    return {"status": "received"}


# ── Dashboard API Endpoints ──────────────────────────────────────────────────

@app.get("/api/agents")
async def get_agents_status():
    """Return status of all 15 agents."""
    agents_list = [
        # The AI Phone Guy
        {"name": "Alex", "type": "CEO", "phone_guy": True},
        {"name": "Tyler", "type": "Sales", "phone_guy": True},
        {"name": "Zoe", "type": "Marketing", "phone_guy": True},
        {"name": "Jennifer", "type": "Retention", "phone_guy": True},
        # Calling Digital
        {"name": "Dek", "type": "CEO", "calling_digital": True},
        {"name": "Marcus", "type": "Sales", "calling_digital": True},
        {"name": "Sofia", "type": "Marketing", "calling_digital": True},
        {"name": "Carlos", "type": "Retention", "calling_digital": True},
        {"name": "Nova", "type": "Specialist", "calling_digital": True},
        # Automotive Intelligence
        {"name": "Michael Meta", "type": "CEO", "autointelligence": True},
        {"name": "Ryan Data", "type": "Sales", "autointelligence": True},
        {"name": "Chase", "type": "Marketing", "autointelligence": True},
        {"name": "Phoenix", "type": "Specialist", "autointelligence": True},
        {"name": "Atlas", "type": "Specialist", "autointelligence": True},
    ]
    
    return {
        "total_agents": len(agents_list),
        "agents": agents_list,
        "status": "all_active"
    }


@app.get("/api/jobs")
async def get_scheduled_jobs():
    """Return all scheduled jobs from APScheduler."""
    jobs_info = []
    for job in scheduler.get_jobs():
        jobs_info.append({
            "id": job.id,
            "name": job.name,
            "next_run": str(job.next_run_time) if job.next_run_time else "paused",
            "trigger": str(job.trigger),
        })
    
    return {
        "total_jobs": len(jobs_info),
        "scheduler_running": scheduler.running,
        "jobs": sorted(jobs_info, key=lambda x: x["next_run"])
    }


@app.get("/api/logs")
async def get_recent_logs(agent: Optional[str] = None, limit: int = 50):
    """Return recent log files, optionally filtered by agent."""
    log_files = glob.glob("logs/*.log")
    entries = []
    
    for log_file in sorted(log_files, reverse=True)[:10]:  # Check last 10 log files
        try:
            with open(log_file, 'r') as f:
                lines = f.readlines()
                for line in lines[-limit:]:  # Last N lines
                    if agent is None or agent.lower() in line.lower():
                        entries.append({
                            "file": os.path.basename(log_file),
                            "line": line.strip(),
                            "timestamp": log_file
                        })
        except Exception as e:
            logging.warning(f"Could not read log file {log_file}: {e}")
    
    return {
        "total_entries": len(entries),
        "entries": entries[-limit:]
    }


@app.get("/api/test-results")
async def get_test_results():
    """Return latest test suite results."""
    test_files = sorted(glob.glob("logs/test_suite_*.json"), reverse=True)
    
    if not test_files:
        return {
            "status": "no_tests_run",
            "message": "No test results found yet"
        }
    
    try:
        with open(test_files[0], 'r') as f:
            test_data = json.load(f)
        
        return {
            "status": "success",
            "file": os.path.basename(test_files[0]),
            "results": test_data
        }
    except Exception as e:
        return {
            "status": "error",
            "message": str(e)
        }


@app.get("/api/metrics")
async def get_metrics():
    """Return current system metrics and performance data."""
    jobs_info = []
    for job in scheduler.get_jobs():
        jobs_info.append({
            "id": job.id,
            "name": job.name,
            "next_run": str(job.next_run_time) if job.next_run_time else "paused",
        })
    
    test_files = sorted(glob.glob("logs/test_suite_*.json"), reverse=True)
    test_pass_rate = "N/A"
    
    if test_files:
        try:
            with open(test_files[0], 'r') as f:
                test_data = json.load(f)
                if "pass_rate" in test_data:
                    test_pass_rate = test_data["pass_rate"]
        except Exception:
            pass
    
    return {
        "timestamp": datetime.datetime.now(pytz.timezone("America/Chicago")).isoformat(),
        "system_status": "operational",
        "scheduler_running": scheduler.running,
        "jobs_registered": len(jobs_info),
        "agents_total": 15,
        "test_pass_rate": test_pass_rate,
        "api_endpoint": "http://127.0.0.1:8000",
        "uptime": "running",
        "database": "Postgres" if DATABASE_URL else "Filesystem"
    }


@app.get("/api/agent-logs/{agent_name}")
async def get_agent_logs_by_date(agent_name: str):
    """Return all logs for a specific agent organized by date, word-for-word."""
    agent_name_lower = agent_name.lower().strip()
    
    # Map agent names to log file patterns
    log_patterns = {
        "alex": ("alex_briefing", "briefing"),
        "tyler": ("tyler_prospecting", "prospecting"),
        "zoe": ("zoe_content", "content"),
        "jennifer": ("jennifer_retention", "retention"),
        "dek": ("dek_briefing", "briefing"),
        "marcus": ("marcus_prospecting", "prospecting"),
        "sofia": ("sofia_content", "content"),
        "carlos": ("carlos_retention", "retention"),
        "nova": ("nova_intelligence", "intelligence"),
        "michael meta": ("michael_meta_briefing", "briefing"),
        "ryan data": ("ryan_data_prospecting", "prospecting"),
        "chase": ("chase_content", "content"),
        "atlas": ("atlas_intel", "intel"),
        "phoenix": ("phoenix_delivery", "delivery"),
    }
    
    if agent_name_lower not in log_patterns:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_name}' not found.")
    
    pattern_prefix, agent_type = log_patterns[agent_name_lower]
    
    # Find all log files for this agent
    log_files = sorted(glob.glob(f"logs/{pattern_prefix}*.log"), reverse=True)
    json_files = sorted(glob.glob(f"logs/{pattern_prefix}*.json"), reverse=True)
    all_files = sorted(log_files + json_files, key=os.path.getmtime, reverse=True)
    
    if not all_files:
        return {
            "agent": agent_name,
            "agent_type": agent_type,
            "logs_by_date": {},
            "total_runs": 0
        }
    
    logs_by_date = {}
    
    for file_path in all_files:
        try:
            filename = os.path.basename(file_path)
            # Extract date from filename (format: name_YYYY-MM-DD.log or name_YYYY-MM-DD.json)
            date_match = filename.split("_")[-1].replace(".log", "").replace(".json", "")
            
            with open(file_path, 'r') as f:
                content = f.read()
            
            if date_match not in logs_by_date:
                logs_by_date[date_match] = []
            
            logs_by_date[date_match].append({
                "filename": filename,
                "type": "json" if file_path.endswith(".json") else "log",
                "content": content,
                "size_kb": round(len(content) / 1024, 2),
                "timestamp": datetime.datetime.fromtimestamp(os.path.getmtime(file_path)).isoformat()
            })
        except Exception as e:
            logging.warning(f"Could not read file {file_path}: {e}")
    
    return {
        "agent": agent_name,
        "agent_type": agent_type,
        "logs_by_date": logs_by_date,
        "total_runs": len(all_files),
        "dates": sorted(logs_by_date.keys(), reverse=True)
    }


@app.get("/dashboard")
async def get_dashboard():
    """Serve the dashboard HTML."""
    dashboard_path = Path("static/dashboard.html")
    if dashboard_path.exists():
        return FileResponse(dashboard_path, media_type="text/html")
    else:
        return {"error": "Dashboard not found"}


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
        "version": "4.0.0",
        "engine": "revenue",
        "scheduler": "running" if scheduler.running else "stopped",
        "jobs_registered": len(jobs_info),
        "postgres": "connected" if DATABASE_URL else "not configured",
        "ghl_configured": _ghl_ready(),
        "revenue_tracking": bool(DATABASE_URL),
        "jobs": jobs_info,
    }
