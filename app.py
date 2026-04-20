# AIBOS Operating Foundation
# ================================
# This system is built on servant leadership.
# Every agent exists to serve the human it works for.
# Every decision prioritizes people over profit.
# Every interaction is conducted with honesty,
# dignity, and genuine care for the other person.
# We build tools that give power back to the small
# business owner — not tools that extract from them.
# We operate with excellence because excellence
# honors the gifts we've been given.
# We do not deceive. We do not manipulate.
# We do not build features that harm the vulnerable.
# Profit is the outcome of service, not the purpose.
# ================================

import os
import glob
import logging
import sys
import datetime
import json
import asyncio
import uuid
import re
from importlib import import_module
from collections import deque
from pathlib import Path
from contextlib import asynccontextmanager, contextmanager
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.responses import PlainTextResponse, JSONResponse, FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from typing import Optional, List, Dict, Any
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
import pytz
from config.runtime import get_settings
from config.logging_setup import configure_logging, set_request_id
from config.principles import SYSTEM_IDENTITY, evaluate_action_morally
from services.database import execute_query, fetch_all
from services.http_client import request_with_retry
from services.errors import DatabaseError
from services.artifact import create_artifact, Artifact, ARTIFACT_TYPES, ARTIFACT_STATUSES
from services.approval_queue import (
    queue_artifact, get_pending, get_escalated, get_artifact_record,
    list_artifacts, approve_artifact, reject_artifact, persist_artifact,
)
from services.dispatch import dispatch_artifact
from services.delivery_receipt import get_receipts
from services.social_pipeline import run_zernio_social_pipeline, prepare_social_piece_with_creative_director

try:
    from crewai import Crew, Task, Process
    _CREWAI_OK = True
except ImportError as _crewai_err:
    logging.warning(f"[CrewAI] import failed — CrewAI-powered jobs disabled: {_crewai_err}")

    class _CrewAIUnavailable:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("CrewAI is not available in this environment.")

    class _ProcessUnavailable:
        sequential = None

    Crew = Task = _CrewAIUnavailable  # type: ignore
    Process = _ProcessUnavailable  # type: ignore
    _CREWAI_OK = False

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
from tools.ghl import (
    push_prospects_to_ghl,
    create_contact,
    add_contact_note,
    send_email,
    publish_content_to_ghl_site,
    ghl_site_publish_ready,
    publish_content_to_ghl_social,
    ghl_social_publish_ready,
)
from tools.ghost import publish_content_to_ghost, ghost_publish_ready
from tools.zernio import (
    zernio_ready,
    list_zernio_accounts,
    publish_to_zernio,
    get_zernio_profiles,
    publish_content_piece_to_zernio,
)
from tools.crm_router import push_prospects_to_crm, resolve_provider, provider_ready, crm_status_snapshot
from tools.hubspot import hubspot_email_ready
from tools.attio import attio_email_ready
from tools.outbound_email import email_delivery_mode, unified_email_ready
from tools.email_engine import parse_prospects, parse_retention_actions, parse_content_pieces

# EMAIL ARCHITECTURE — Phase 1
# ================================
# Outreach email routes through CRM only.
# APG → GoHighLevel workflows
# CD → Attio sequences  
# AI → HubSpot workflows
#
# Resend is NOT active in Phase 1.
# Do not implement direct email sending 
# from Paperclip agents at this time.
#
# FUTURE: Resend integration planned for 
# Phase 2 when AIBOS native email layer 
# is ready to build.
# See PHASE_ROADMAP.md for full details.
# ================================

# TODO Phase 2: Wire Resend send_email() tool
# to agent task output for autonomous sending
# outside of CRM workflow dependency.
# Domain verification status:
# theaiphoneguy.ai — verified in Resend
# callingdigital.com — verified in Resend  
# automotiveintelligence.io — pending 
# re-verification after DNS fix March 2026
from tools.contact_enricher import enrich_prospects
from tools.bloomberry import enrich_prospects_tech, bloomberry_ready
from tools.icp_guardrails import validate_and_filter_prospects
from tools.revenue_tracker import (
    init_revenue_tracker, init_revenue_tables, track_event,
    queue_content, get_content_queue, mark_content_published,
    get_revenue_summary, get_daily_metrics, get_email_template_report,
)


# ── Agent Imports ────────────────────────────────────────────────────────────

def _load_agent_symbol(module_path: str, symbol_name: str):
    try:
        module = import_module(module_path)
        return getattr(module, symbol_name)
    except Exception as exc:
        logging.warning(f"[Agents] {symbol_name} unavailable from {module_path}: {exc}")
        return None


def _load_agent_job(module_path: str, symbol_name: str):
    symbol = _load_agent_symbol(module_path, symbol_name)
    if symbol is not None:
        return symbol

    def _missing_agent_job(*args, **kwargs):
        raise RuntimeError(f"Agent job '{symbol_name}' is unavailable in this environment.")

    return _missing_agent_job

# The AI Phone Guy
alex = _load_agent_symbol("agents.aiphoneguy.alex", "alex")
tyler = _load_agent_symbol("agents.aiphoneguy.tyler", "tyler")
zoe = _load_agent_symbol("agents.aiphoneguy.zoe", "zoe")
jennifer = _load_agent_symbol("agents.aiphoneguy.jennifer", "jennifer")

# Calling Digital
dek = _load_agent_symbol("agents.callingdigital.dek", "dek")
marcus = _load_agent_symbol("agents.callingdigital.marcus", "marcus")
sofia = _load_agent_symbol("agents.callingdigital.sofia", "sofia")
carlos = _load_agent_symbol("agents.callingdigital.carlos", "carlos")
nova = _load_agent_symbol("agents.callingdigital.nova", "nova")

# Automotive Intelligence
michael_meta = _load_agent_symbol("agents.autointelligence.michael_meta", "michael_meta")
ryan_data = _load_agent_symbol("agents.autointelligence.ryan_data", "ryan_data")
chase = _load_agent_symbol("agents.autointelligence.chase", "chase")
atlas = _load_agent_symbol("agents.autointelligence.atlas", "atlas")
phoenix = _load_agent_symbol("agents.autointelligence.phoenix", "phoenix")
darrell = _load_agent_symbol("agents.autointelligence.darrell", "darrell")

# Agent Empire
debra = _load_agent_symbol("agents.agentempire.debra", "debra")
wade_agent = _load_agent_symbol("agents.agentempire.wade", "wade")
tammy_agent = _load_agent_symbol("agents.agentempire.tammy", "tammy")
sterling = _load_agent_symbol("agents.agentempire.sterling", "sterling")

# CustomerAdvocate
clint = _load_agent_symbol("agents.customeradvocate.clint", "clint")
sherry = _load_agent_symbol("agents.customeradvocate.sherry", "sherry")

# RevOps agents
randy = _load_agent_symbol("agents.aiphoneguy.randy", "randy")
brenda = _load_agent_symbol("agents.callingdigital.brenda", "brenda")

run_coo_command = _load_agent_job("agents.coo.coo_agent", "run_coo_command")


CST = pytz.timezone("America/Chicago")

configure_logging()

os.makedirs("logs", exist_ok=True)

SETTINGS = get_settings()
API_KEYS = set(SETTINGS.api_keys)
DATABASE_URL = SETTINGS.database_url

logger = logging.getLogger(__name__)
logger.info("AIBOS identity: %s", SYSTEM_IDENTITY)


# ── Database ─────────────────────────────────────────────────────────────────

def _db_url() -> str:
    """Return normalized Postgres URL from centralized runtime settings."""
    return DATABASE_URL


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
        execute_query("""
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
    except DatabaseError as e:
        logging.error(f"[DB] init_db failed: {e}")

    # ── Activation Layer tables ────────────────────────────────────────────
    try:
        execute_query("""
            CREATE TABLE IF NOT EXISTS artifacts (
                artifact_id             TEXT        PRIMARY KEY,
                agent_id                TEXT        NOT NULL,
                business_key            TEXT        NOT NULL,
                artifact_type           TEXT        NOT NULL,
                audience                TEXT        NOT NULL,
                intent                  TEXT        NOT NULL,
                content                 TEXT        NOT NULL,
                subject                 TEXT,
                channel_candidates      TEXT        NOT NULL DEFAULT '[]',
                confidence              REAL        NOT NULL DEFAULT 0.8,
                risk_level              TEXT        NOT NULL DEFAULT 'medium',
                requires_human_approval BOOLEAN     NOT NULL DEFAULT TRUE,
                metadata                TEXT        NOT NULL DEFAULT '{}',
                created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                status                  TEXT        NOT NULL DEFAULT 'pending_approval'
            );
            CREATE INDEX IF NOT EXISTS idx_artifacts_status
                ON artifacts (status, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_artifacts_business
                ON artifacts (business_key, created_at DESC);

            CREATE TABLE IF NOT EXISTS artifact_approvals (
                id          SERIAL      PRIMARY KEY,
                artifact_id TEXT        NOT NULL REFERENCES artifacts(artifact_id),
                decision    TEXT        NOT NULL,
                reviewer    TEXT        NOT NULL,
                reason      TEXT,
                decided_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS delivery_receipts (
                receipt_id        TEXT        PRIMARY KEY,
                artifact_id       TEXT        NOT NULL REFERENCES artifacts(artifact_id),
                channel           TEXT        NOT NULL,
                status            TEXT        NOT NULL,
                delivered_at      TIMESTAMPTZ,
                error             TEXT,
                provider_response TEXT        DEFAULT '{}',
                created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_receipts_artifact
                ON delivery_receipts (artifact_id, created_at DESC);

            CREATE TABLE IF NOT EXISTS quality_snapshots (
                snapshot_id         TEXT        PRIMARY KEY,
                window_hours        INTEGER     NOT NULL,
                total_runs          INTEGER     NOT NULL,
                active_agents       INTEGER     NOT NULL,
                availability_ratio  REAL        NOT NULL,
                short_outputs       INTEGER     NOT NULL,
                error_like_outputs  INTEGER     NOT NULL,
                delivered_artifacts INTEGER     NOT NULL,
                failed_artifacts    INTEGER     NOT NULL,
                delivery_ratio      REAL        NOT NULL,
                score               REAL        NOT NULL,
                details             TEXT        NOT NULL DEFAULT '{}',
                created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_quality_snapshots_created_at
                ON quality_snapshots (created_at DESC);
        """)
        logging.info("[DB] Activation Layer tables ready (artifacts, artifact_approvals, delivery_receipts, quality_snapshots).")
    except DatabaseError as e:
        logging.error(f"[DB] Activation Layer table init failed: {e}")

    # ── ICP Discards table ───────────────────────────────────────────────
    try:
        execute_query("""
            CREATE TABLE IF NOT EXISTS icp_discards (
                id              SERIAL PRIMARY KEY,
                agent_name      TEXT        NOT NULL,
                business_name   TEXT        NOT NULL,
                city            TEXT,
                business_type   TEXT,
                reason          TEXT        NOT NULL,
                created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_icp_discards_agent
                ON icp_discards (agent_name, created_at DESC);
        """)
        logging.info("[DB] icp_discards table ready.")
    except DatabaseError as e:
        logging.error(f"[DB] icp_discards table init failed: {e}")

    # ── CRM Push Logs table ──────────────────────────────────────────────
    try:
        execute_query("""
            CREATE TABLE IF NOT EXISTS crm_push_logs (
                id              SERIAL PRIMARY KEY,
                agent_name      TEXT        NOT NULL,
                crm_provider    TEXT        NOT NULL,
                business_name   TEXT        NOT NULL,
                status          TEXT        NOT NULL,
                created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_crm_push_logs_agent
                ON crm_push_logs (agent_name, created_at DESC);
        """)
        logging.info("[DB] crm_push_logs table ready.")
    except DatabaseError as e:
        logging.error(f"[DB] crm_push_logs table init failed: {e}")

    # ── AVO Intelligence Layer tables ─────────────────────────────────────
    try:
        execute_query("""
            CREATE TABLE IF NOT EXISTS agent_memory (
                id          SERIAL PRIMARY KEY,
                agent_name  TEXT        NOT NULL,
                river       TEXT        NOT NULL DEFAULT '',
                memory_type TEXT        NOT NULL,
                content     TEXT        NOT NULL,
                created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_agent_memory_lookup
                ON agent_memory (agent_name, memory_type, created_at DESC);
        """)
        logging.info("[DB] agent_memory table ready.")
    except DatabaseError as e:
        logging.error(f"[DB] agent_memory table init failed: {e}")

    try:
        execute_query("""
            CREATE TABLE IF NOT EXISTS agent_handoffs (
                id              SERIAL PRIMARY KEY,
                from_agent      TEXT        NOT NULL,
                to_agent        TEXT        NOT NULL,
                river           TEXT        NOT NULL DEFAULT '',
                handoff_type    TEXT        NOT NULL,
                payload         TEXT        NOT NULL DEFAULT '{}',
                priority        TEXT        NOT NULL DEFAULT 'medium',
                status          TEXT        NOT NULL DEFAULT 'pending',
                created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                picked_up_at    TIMESTAMPTZ,
                completed_at    TIMESTAMPTZ
            );
            CREATE INDEX IF NOT EXISTS idx_agent_handoffs_pending
                ON agent_handoffs (to_agent, status, created_at);
        """)
        logging.info("[DB] agent_handoffs table ready.")
    except DatabaseError as e:
        logging.error(f"[DB] agent_handoffs table init failed: {e}")

    try:
        execute_query("""
            CREATE TABLE IF NOT EXISTS axiom_directives (
                id              SERIAL PRIMARY KEY,
                target_agent    TEXT        NOT NULL,
                river           TEXT        NOT NULL DEFAULT '',
                directive       TEXT        NOT NULL,
                priority        TEXT        NOT NULL DEFAULT 'medium',
                triggered_by    TEXT        NOT NULL DEFAULT '',
                status          TEXT        NOT NULL DEFAULT 'pending',
                created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_axiom_directives_pending
                ON axiom_directives (target_agent, status, created_at);
        """)
        logging.info("[DB] axiom_directives table ready.")
    except DatabaseError as e:
        logging.error(f"[DB] axiom_directives table init failed: {e}")

    try:
        execute_query("""
            CREATE TABLE IF NOT EXISTS agent_run_costs (
                id                      SERIAL PRIMARY KEY,
                agent_name              TEXT        NOT NULL,
                river                   TEXT        NOT NULL DEFAULT '',
                model                   TEXT        NOT NULL DEFAULT '',
                input_tokens            INTEGER     NOT NULL DEFAULT 0,
                output_tokens           INTEGER     NOT NULL DEFAULT 0,
                cost_usd                REAL        NOT NULL DEFAULT 0,
                run_date                DATE        NOT NULL,
                run_duration_seconds    REAL        NOT NULL DEFAULT 0,
                created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            CREATE INDEX IF NOT EXISTS idx_agent_run_costs_lookup
                ON agent_run_costs (agent_name, run_date DESC);
        """)
        logging.info("[DB] agent_run_costs table ready.")
    except DatabaseError as e:
        logging.error(f"[DB] agent_run_costs table init failed: {e}")


def persist_log(agent_name: str, log_type: str, content: str):
    """Write an agent run result to Postgres (primary). Filesystem writes skipped on Railway."""
    today = datetime.datetime.now(CST).strftime("%Y-%m-%d")
    run_date = datetime.date.fromisoformat(today)

    # ── Postgres primary (Railway does not support persistent file writes)
    if not DATABASE_URL:
        logging.warning(f"[DB] DATABASE_URL not set — cannot persist logs for {agent_name}")
        return

    try:
        execute_query(
            "INSERT INTO agent_logs (agent_name, log_type, run_date, content) "
            "VALUES (%s, %s, %s, %s)",
            (agent_name, log_type, run_date, content),
        )
        logging.info(f"[DB] ✓ Persisted {agent_name}/{log_type} to Postgres ({len(content)} chars)")
    except DatabaseError as e:
        logging.error(f"[DB] ✗ persist_log FAILED for {agent_name}/{log_type}: {e}")


# ── AVO Intelligence Layer — Pre/Post Run Hooks ──────────────────────────────

def _avo_pre_run_context(agent_name: str) -> str:
    """Build the intelligence context injected BEFORE every agent's main task.

    Order: Memory → Directives → Handoffs
    Returns a string to prepend to the agent's task description.
    """
    parts = []
    try:
        from core.memory import build_memory_context
        mem = build_memory_context(agent_name)
        if mem:
            parts.append(mem)
    except Exception as e:
        logging.debug("[AVO] Memory injection skipped for %s: %s", agent_name, e)

    try:
        from agents.ceo.axiom import build_directive_context
        directives = build_directive_context(agent_name)
        if directives:
            parts.append(directives)
    except Exception as e:
        logging.debug("[AVO] Directive injection skipped for %s: %s", agent_name, e)

    try:
        from core.handoff import build_handoff_context
        handoffs = build_handoff_context(agent_name)
        if handoffs:
            parts.append(handoffs)
    except Exception as e:
        logging.debug("[AVO] Handoff injection skipped for %s: %s", agent_name, e)

    return "\n\n".join(parts)


def _avo_post_run(agent_name: str, river: str, raw_output: str, duration_seconds: float = 0.0):
    """Post-run hooks: save memory + persist log + check handoffs + track cost.

    Called AFTER every agent run completes.
    """
    # Save daily summary memory
    try:
        from core.memory import save_memory
        summary = raw_output[:500] if raw_output else "Run completed with no output"
        save_memory(agent_name, "daily_summary", {
            "summary": summary,
            "output_length": len(raw_output) if raw_output else 0,
        }, river=river)
    except Exception as e:
        logging.debug("[AVO] Memory save skipped for %s: %s", agent_name, e)

    # Persist to agent_logs so COO can see this run.
    # Original 14 agents already call persist_log inside their run functions.
    # The 9 new AVO agents (Randy/Brenda/Darrell/Tammy/Wade/Debra/Sterling/
    # Clint/Sherry) and Axiom rely entirely on this call to be visible.
    try:
        log_type = LOG_TYPES.get(agent_name, "avo_run")
        content_to_log = raw_output if raw_output else f"{agent_name} run completed at {datetime.datetime.now(CST).isoformat()}"
        persist_log(agent_name, log_type, content_to_log)
    except Exception as e:
        # Bumped from debug to warning so failures are visible in Railway logs
        logging.warning("[AVO] persist_log call failed for %s: %s", agent_name, e)

    # Check for handoff triggers based on agent output
    try:
        _check_handoff_triggers(agent_name, river, raw_output)
    except Exception as e:
        logging.debug("[AVO] Handoff check skipped for %s: %s", agent_name, e)

    # Estimate and log cost (rough estimate based on output length)
    try:
        from core.cost_tracker import log_run_cost
        # Estimate tokens: ~4 chars per token for English text
        estimated_output_tokens = len(raw_output) // 4 if raw_output else 0
        estimated_input_tokens = estimated_output_tokens * 2  # Rough: input is usually ~2x output
        model = os.environ.get("LLM_MODEL", "claude-sonnet-4-6")
        log_run_cost(
            agent_name=agent_name,
            river=river,
            model=model,
            input_tokens=estimated_input_tokens,
            output_tokens=estimated_output_tokens,
            run_duration_seconds=duration_seconds,
        )
    except Exception as e:
        logging.debug("[AVO] Cost tracking skipped for %s: %s", agent_name, e)


def _check_handoff_triggers(agent_name: str, river: str, raw_output: str):
    """Check if an agent's output should trigger a handoff to another agent.

    Wired handoff rules:
      Atlas → Ryan Data (dealer brief completed)
      Zoe → Review Queue (content produced, needs review)
      Debra → Wade (content calendar with tools mentioned)
      Clint → Sherry (VERA component completed)
      Tyler → Jennifer (3+ new prospects)
    """
    from core.handoff import create_handoff

    output_lower = (raw_output or "").lower()

    if agent_name == "atlas" and any(kw in output_lower for kw in ["dealer brief", "dealership", "profile"]):
        create_handoff("atlas", "ryan_data", river, "dealer_brief",
                       {"summary": raw_output[:300]}, priority="high")

    elif agent_name == "zoe" and any(kw in output_lower for kw in ["content", "blog", "post", "article"]):
        create_handoff("zoe", "zoe", river, "content_review",
                       {"content_preview": raw_output[:300], "status": "pending_review"}, priority="medium")

    elif agent_name == "debra" and any(kw in output_lower for kw in ["outline", "calendar", "video"]):
        create_handoff("debra", "wade_ae", river, "sponsor_check",
                       {"tools_mentioned": raw_output[:300]}, priority="medium")

    elif agent_name == "clint" and any(kw in output_lower for kw in ["vera", "scoring", "component", "build"]):
        create_handoff("clint", "sherry", river, "ui_component",
                       {"technical_spec": raw_output[:300]}, priority="high")

    elif agent_name == "tyler" and output_lower.count("prospect") >= 3:
        create_handoff("tyler", "jennifer", river, "prospect_alert",
                       {"prospect_summary": raw_output[:300],
                        "message": "Prepare onboarding context for these verticals in case they convert"},
                       priority="medium")


def _avo_wrap_run(agent_name: str, river: str, run_fn):
    """Universal intelligence wrapper for any agent run function.

    Injects memory + directives + handoffs before the run,
    saves memory + estimates cost after.
    """
    import time as _time
    # Pre-run: build intelligence context (logged but not injected into CrewAI
    # task descriptions — that would require refactoring each run function.
    # Instead, the context is logged and available for Axiom's nightly analysis.)
    try:
        ctx = _avo_pre_run_context(agent_name)
        if ctx:
            logging.info("[AVO] %s pre-run context: %s", agent_name, ctx[:200])
    except Exception:
        pass

    start = _time.time()
    result = run_fn()
    duration = _time.time() - start

    # Post-run: save memory + track cost
    raw_output = ""
    if isinstance(result, dict):
        raw_output = json.dumps(result, default=str)[:2000]
    elif isinstance(result, str):
        raw_output = result[:2000]
    _avo_post_run(agent_name, river, raw_output, duration)

    return result


def _build_ceo_kpi_context(business_key: str) -> str:
    """Build a deterministic KPI snapshot for CEO operating briefs."""
    try:
        summary_30 = get_revenue_summary(business_key=business_key, days=30)
        daily_7 = get_daily_metrics(business_key=business_key, days=7)
        context = {
            "business_key": business_key,
            "summary_30d": summary_30,
            "daily_metrics_7d": daily_7,
        }
        return json.dumps(context, indent=2)
    except Exception as e:
        logging.error(f"[Guardrail] KPI context build failed for {business_key}: {e}")
        return json.dumps({"business_key": business_key, "error": str(e)}, indent=2)


def _enforce_ceo_operating_brief(agent_name: str, output_text: str) -> str:
    """Reject briefs that are not CEO operating scorecards.

    We require business-operating sections to prevent generic industry-news briefs.
    """
    lower = (output_text or "").lower()
    required_signals = [
        "revenue",
        "pipeline",
        "customer",
        "team",
        "brand",
        "priority",
        "today",
        "message to founder",
        "physical-world",
    ]
    missing = [token for token in required_signals if token not in lower]

    if not missing:
        return output_text

    logging.error(
        f"[Guardrail] {agent_name} briefing rejected: missing operating sections {missing}."
    )
    return (
        "OPERATING BRIEF FAILURE: Briefing blocked because it was not CEO-operations focused.\n\n"
        f"Missing sections: {', '.join(missing)}\n\n"
        "Required sections for future runs:\n"
        "1) Revenue and cash-impact actions\n"
        "2) Pipeline build + conversion blockers\n"
        "3) Customer satisfaction + retention actions\n"
        "4) Team execution + accountability\n"
        "5) Brand awareness/consideration/conversion actions\n"
        "6) Top 3 priorities for today with owners and deadlines\n"
        "7) Message to Founder (plain-English update + decisions needed)\n"
        "8) Physical-world CEO actions (in-person, events, partnerships, recruiting, client visits)\n"
        "Do not default to generic industry headlines.\n"
    )


# ── Agent Registry ───────────────────────────────────────────────────────────

AGENTS = {
    # The AI Phone Guy
    "alex": alex,
    "tyler": tyler,
    "zoe": zoe,
    "jennifer": jennifer,
    "randy": randy,
    # Calling Digital
    "dek": dek,
    "marcus": marcus,
    "sofia": sofia,
    "carlos": carlos,
    "nova": nova,
    "brenda": brenda,
    # Automotive Intelligence
    "michael_meta": michael_meta,
    "ryan_data": ryan_data,
    "chase": chase,
    "atlas": atlas,
    "phoenix": phoenix,
    "darrell": darrell,
    # Agent Empire
    "debra": debra,
    "wade_ae": wade_agent,
    "tammy_ae": tammy_agent,
    "sterling": sterling,
    # CustomerAdvocate
    "clint": clint,
    "sherry": sherry,
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
    "randy":        "revops",
    "brenda":       "revops",
    "darrell":      "revops",
    "debra":        "content",
    "wade_ae":      "bizdev",
    "tammy_ae":     "community",
    "clint":        "build",
    "sherry":       "design",
    "sterling":     "web",
    "axiom":        "ceo_orchestration",
}

BUSINESSES = {
    "aiphoneguy": {
        "name": "The AI Phone Guy",
        "agents": ["alex", "tyler", "zoe", "jennifer", "randy"],
    },
    "callingdigital": {
        "name": "Calling Digital",
        "agents": ["dek", "marcus", "sofia", "carlos", "nova", "brenda"],
    },
    "autointelligence": {
        "name": "Automotive Intelligence",
        "agents": ["michael_meta", "ryan_data", "chase", "atlas", "phoenix", "darrell"],
    },
    "agentempire": {
        "name": "Agent Empire",
        "agents": ["debra", "wade_ae", "tammy_ae", "sterling"],
    },
    "customeradvocate": {
        "name": "CustomerAdvocate",
        "agents": ["clint", "sherry"],
    },
}

# Maps agents to their business key for revenue tracking
AGENT_BUSINESS_KEY = {}
for biz_key, biz in BUSINESSES.items():
    for agent_id in biz["agents"]:
        AGENT_BUSINESS_KEY[agent_id] = biz_key


# ── GHL Configuration Check ─────────────────────────────────────────────────

def _crm_ready_for(business_key: str, agent_name: str) -> bool:
    """Check if mapped CRM provider has required credentials."""
    provider = resolve_provider(business_key=business_key, agent_name=agent_name)
    return provider_ready(provider)


def _provider_email_capability(provider: str) -> Dict[str, Any]:
    """Return whether this provider supports outbound email in current codebase."""
    p = (provider or "").strip().lower()
    mode = email_delivery_mode()
    if mode == "unified":
        # In unified mode each provider maps to a specific business key for per-business
        # MAIL_FROM_<SUFFIX> / RESEND_API_KEY_<SUFFIX> lookups.
        _provider_business_map = {
            "ghl": "aiphoneguy",
            "hubspot": "autointelligence",
            "attio": "callingdigital",
        }
        bk = _provider_business_map.get(p, "")
        ready = unified_email_ready(bk)
        return {
            "provider": p or "unknown",
            "email_supported": True,
            "email_send_ready": ready,
            "missing_requirements": [] if ready else [
                "RESEND_API_KEY",
                f"MAIL_FROM_{bk.upper() if bk else '<BUSINESSKEY>'} (verified sender address)",
            ],
            "notes": "Unified delivery mode is enabled. All providers send via Resend HTTP API.",
        }
    if p == "ghl":
        return {
            "provider": "ghl",
            "email_supported": True,
            "email_send_ready": SETTINGS.ghl_ready,
            "missing_requirements": [] if SETTINGS.ghl_ready else ["GHL_API_KEY", "GHL_LOCATION_ID"],
            "notes": "Requires a connected sending mailbox inside GHL; API cannot verify mailbox state.",
        }
    if p == "hubspot":
        hubspot_ready_for_email = hubspot_email_ready()
        return {
            "provider": "hubspot",
            "email_supported": hubspot_ready_for_email,
            "email_send_ready": hubspot_ready_for_email,
            "missing_requirements": [] if hubspot_ready_for_email else ["HUBSPOT_TRANSACTIONAL_EMAIL_ID"],
            "notes": (
                "HubSpot transactional first-touch email is enabled when HUBSPOT_TRANSACTIONAL_EMAIL_ID is set "
                "and the template supports customProperties (subject_line, body_copy, business_name)."
            ),
        }
    if p == "attio":
        attio_ready_for_email = attio_email_ready()
        return {
            "provider": "attio",
            "email_supported": attio_ready_for_email,
            "email_send_ready": attio_ready_for_email,
            "missing_requirements": [] if attio_ready_for_email else [
                "ATTIO_SMTP_HOST",
                "ATTIO_SMTP_PORT",
                "ATTIO_SMTP_USERNAME",
                "ATTIO_SMTP_PASSWORD",
                "ATTIO_SMTP_FROM",
            ],
            "notes": "Attio first-touch email is enabled via SMTP when ATTIO_SMTP_* settings are configured.",
        }
    return {
        "provider": p or "unknown",
        "email_supported": False,
        "email_send_ready": False,
        "missing_requirements": ["Unsupported provider mapping"],
        "notes": "Provider is not recognized by the CRM router.",
    }


def _sales_preflight_report() -> Dict[str, Any]:
    """Build provider + agent-level readiness report for sales execution."""
    providers = ("ghl", "hubspot", "attio")
    by_provider: Dict[str, Any] = {}
    mode = email_delivery_mode()
    overall_ready = True

    for provider in providers:
        creds_ready = provider_ready(provider)
        email_cap = _provider_email_capability(provider)
        missing = []
        if not creds_ready:
            if provider == "ghl":
                missing.extend(["GHL_API_KEY", "GHL_LOCATION_ID"])
            elif provider == "hubspot":
                missing.extend(["HUBSPOT_API_KEY or HUBSPOT_ACCESS_TOKEN"])
            elif provider == "attio":
                missing.extend(["ATTIO_API_KEY"])
        for item in email_cap.get("missing_requirements", []):
            if item not in missing:
                missing.append(item)

        ready = bool(creds_ready and email_cap.get("email_send_ready", False))
        overall_ready = overall_ready and ready
        by_provider[provider] = {
            "credentials_ready": creds_ready,
            "email_supported": email_cap.get("email_supported", False),
            "email_send_ready": email_cap.get("email_send_ready", False),
            "missing_requirements": missing,
            "notes": email_cap.get("notes", ""),
        }

    sales_agents = []
    for agent_id in ("tyler", "marcus", "ryan_data"):
        business_key = AGENT_BUSINESS_KEY.get(agent_id, "")
        provider = resolve_provider(business_key=business_key, agent_name=agent_id)
        provider_row = by_provider.get(provider, _provider_email_capability(provider))
        sales_agents.append(
            {
                "agent_id": agent_id,
                "business_key": business_key,
                "routed_provider": provider,
                "provider_ready": provider_row.get("credentials_ready", False),
                "email_ready": provider_row.get("email_send_ready", False),
            }
        )

    unified_sender = {
        "email_delivery_mode": mode,
        "ready": unified_email_ready() if mode == "unified" else None,
        "required_vars": [
            "RESEND_API_KEY",
            "MAIL_FROM (or MAIL_FROM_<BUSINESSKEY> per business)",
        ],
    }

    return {
        "overall_ready": overall_ready,
        "email_delivery_mode": mode,
        "unified_sender": unified_sender,
        "by_provider": by_provider,
        "sales_agents": sales_agents,
    }


# ── Pit Wall Telemetry ───────────────────────────────────────────────────────

PITWALL_AGENT_META: Dict[str, Dict[str, str]] = {
    "alex": {"name": "Alex", "role": "CEO", "lane": "Executive Control", "team_id": "aiphoneguy"},
    "tyler": {"name": "Tyler", "role": "Head of Sales", "lane": "Pipeline", "team_id": "aiphoneguy"},
    "zoe": {"name": "Zoe", "role": "Head of Marketing", "lane": "Demand Engine", "team_id": "aiphoneguy"},
    "jennifer": {"name": "Jennifer", "role": "Head of Client Success", "lane": "Retention", "team_id": "aiphoneguy"},
    "michael_meta": {"name": "Michael Meta", "role": "CEO", "lane": "Executive Control", "team_id": "autointelligence"},
    "chase": {"name": "Chase", "role": "Chief Revenue Officer", "lane": "Revenue", "team_id": "autointelligence"},
    "atlas": {"name": "Atlas", "role": "Head of Marketing", "lane": "Demand Engine", "team_id": "autointelligence"},
    "ryan_data": {"name": "Ryan", "role": "Research Analyst", "lane": "Intelligence", "team_id": "autointelligence"},
    "phoenix": {"name": "Phoenix", "role": "Implementation Lead", "lane": "Delivery", "team_id": "autointelligence"},
    "dek": {"name": "Dek", "role": "CEO", "lane": "Executive Control", "team_id": "callingdigital"},
    "marcus": {"name": "Marcus", "role": "Head of Sales", "lane": "Pipeline", "team_id": "callingdigital"},
    "carlos": {"name": "Carlos", "role": "Head of Content and Creative", "lane": "Creative", "team_id": "callingdigital"},
    "sofia": {"name": "Sofia", "role": "Head of Client Success", "lane": "Retention", "team_id": "callingdigital"},
    "nova": {"name": "Nova", "role": "Implementation Director", "lane": "Systems", "team_id": "callingdigital"},
    "randy": {"name": "Randy", "role": "RevOps Agent", "lane": "Revenue Operations", "team_id": "aiphoneguy"},
    "brenda": {"name": "Brenda", "role": "RevOps Agent", "lane": "Revenue Operations", "team_id": "callingdigital"},
    "darrell": {"name": "Darrell", "role": "RevOps Agent", "lane": "Revenue Operations", "team_id": "autointelligence"},
    "debra": {"name": "Debra", "role": "Producer Agent", "lane": "Content", "team_id": "agentempire"},
    "wade_ae": {"name": "Wade", "role": "Biz Dev Agent", "lane": "Sponsor Outreach", "team_id": "agentempire"},
    "tammy_ae": {"name": "Tammy", "role": "Community Agent", "lane": "Engagement", "team_id": "agentempire"},
    "sterling": {"name": "Sterling", "role": "Web Agent", "lane": "Web", "team_id": "agentempire"},
    "clint": {"name": "Clint", "role": "Technical Builder", "lane": "Engineering", "team_id": "customeradvocate"},
    "sherry": {"name": "Sherry", "role": "Web Design Agent", "lane": "Design", "team_id": "customeradvocate"},
}


def _pitwall_team_ids() -> List[str]:
    return ["aiphoneguy", "autointelligence", "callingdigital", "agentempire", "customeradvocate"]


def _pitwall_display_name(agent_id: str) -> str:
    meta = PITWALL_AGENT_META.get(agent_id, {})
    return meta.get("name", agent_id.replace("_", " ").title())


def _iso_now() -> str:
    return datetime.datetime.now(CST).isoformat()


def _safe_ratio(numerator: float, denominator: float) -> float:
    if denominator <= 0:
        return 0.0
    return round((numerator / denominator) * 100, 2)


def _fetch_recent_runs_by_agent() -> Dict[str, str]:
    if not DATABASE_URL:
        return {}
    try:
        rows = fetch_all(
            """
            SELECT agent_name, MAX(created_at)
            FROM agent_logs
            GROUP BY agent_name
            """
        )
    except Exception:
        return {}
    output: Dict[str, str] = {}
    for row in rows:
        agent_name, created_at = row
        if agent_name:
            output[str(agent_name)] = str(created_at)
    return output


def _latest_log_for_agent(agent_id: str) -> Optional[str]:
    if not DATABASE_URL:
        return None
    try:
        rows = fetch_all(
            """
            SELECT content
            FROM agent_logs
            WHERE agent_name = %s
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (agent_id,),
        )
    except Exception:
        return None
    if not rows:
        return None
    return str(rows[0][0] or "")


def _extract_signal_lines(text: Optional[str], limit: int = 4) -> List[str]:
    if not text:
        return []
    lines: List[str] = []
    for raw in text.splitlines():
        line = raw.strip().lstrip("-*").strip()
        if not line:
            continue
        if line.lower().startswith(("#", "http", "agent:", "role:")):
            continue
        if len(line) < 16:
            continue
        lines.append(line)
        if len(lines) >= limit:
            break
    return lines


def _derive_status_from_last_run(last_run_iso: Optional[str]) -> str:
    if not last_run_iso:
        return "red"
    try:
        run_dt = datetime.datetime.fromisoformat(last_run_iso.replace("Z", "+00:00"))
        if run_dt.tzinfo is None:
            run_dt = CST.localize(run_dt)
    except Exception:
        return "amber"
    delta = datetime.datetime.now(pytz.utc) - run_dt.astimezone(pytz.utc)
    hours = delta.total_seconds() / 3600.0
    if hours <= 4:
        return "green"
    if hours <= 24:
        return "amber"
    return "red"


def _artifact_pipeline_counts() -> Dict[str, int]:
    if not DATABASE_URL:
        return {
            "artifact_created": 0,
            "risk_gate": 0,
            "approval_queue": 0,
            "dispatch": 0,
        }
    try:
        rows = fetch_all(
            """
            SELECT status, COUNT(*)
            FROM artifacts
            GROUP BY status
            """
        )
    except Exception:
        return {
            "artifact_created": 0,
            "risk_gate": 0,
            "approval_queue": 0,
            "dispatch": 0,
        }

    counts = {str(status): int(count) for status, count in rows}
    dispatch_total = counts.get("delivered", 0) + counts.get("dispatch_failed", 0) + counts.get("failed", 0)
    approval_total = counts.get("pending_approval", 0) + counts.get("escalated", 0)
    return {
        "artifact_created": sum(counts.values()),
        "risk_gate": counts.get("auto_approved", 0) + approval_total,
        "approval_queue": approval_total,
        "dispatch": dispatch_total,
    }


def _team_revenue_kpis(team_id: str) -> Dict[str, Any]:
    summary = get_revenue_summary(business_key=team_id, days=30) or {}
    daily = get_daily_metrics(business_key=team_id, days=7) or []

    prospects = int(summary.get("prospect_created", 0) or 0)
    emails = int(summary.get("email_sent", 0) or 0)
    replies = int(summary.get("email_replied", 0) or 0)
    demos = int(summary.get("demo_booked", 0) or 0)
    closed = int(summary.get("deal_closed", 0) or 0)
    open_opps = max(demos - closed, 0)

    return {
        "open_opps": open_opps,
        "reply_rate": _safe_ratio(replies, emails),
        "win_rate": _safe_ratio(closed, demos),
        "raw": {
            "prospects": prospects,
            "emails": emails,
            "replies": replies,
            "demos": demos,
            "closed": closed,
        },
        "pipeline_activity": daily,
    }


def _fetch_railway_status() -> Dict[str, Any]:
    public_domain = (os.getenv("RAILWAY_PUBLIC_DOMAIN") or "").strip()
    app_url = f"https://{public_domain}" if public_domain else ""
    health_url = f"{app_url}/health" if app_url else ""

    health_ok = False
    health_payload: Dict[str, Any] = {}
    if health_url:
        resp = request_with_retry(
            provider="railway",
            operation="health",
            method="GET",
            url=health_url,
            timeout=8,
            max_attempts=2,
        )
        if resp.ok and isinstance(resp.data, dict):
            health_ok = True
            health_payload = resp.data

    return {
        "project": os.getenv("RAILWAY_PROJECT_NAME", ""),
        "environment": os.getenv("RAILWAY_ENVIRONMENT_NAME", os.getenv("RAILWAY_ENVIRONMENT", "")),
        "service": os.getenv("RAILWAY_SERVICE_NAME", ""),
        "public_domain": public_domain,
        "service_url": os.getenv("RAILWAY_SERVICE_PAPERCLIP_URL", "") or app_url,
        "health_ok": health_ok,
        "health": health_payload,
        "last_checked_at": _iso_now(),
    }


def _ghl_live_metrics() -> Dict[str, Any]:
    if not SETTINGS.ghl_ready:
        return {"available": False, "reason": "credentials_missing"}

    headers = {
        "Authorization": f"Bearer {os.getenv('GHL_API_KEY', '').strip()}",
        "Content-Type": "application/json",
        "Version": "2021-07-28",
    }
    location_id = os.getenv("GHL_LOCATION_ID", "").strip()
    pipeline_id = os.getenv("GHL_PIPELINE_ID", "").strip()
    base = "https://services.leadconnectorhq.com"

    contacts_total: Optional[int] = None
    opportunities_total: Optional[int] = None
    workflows_active: Optional[int] = None
    tags_seen: Optional[int] = None

    contacts_resp = request_with_retry(
        provider="ghl",
        operation="contacts_count",
        method="GET",
        url=f"{base}/contacts/",
        headers=headers,
        params={"locationId": location_id, "limit": 100},
        timeout=10,
        max_attempts=2,
    )
    if contacts_resp.ok and isinstance(contacts_resp.data, dict):
        contacts = contacts_resp.data.get("contacts", []) or []
        contacts_total = int(contacts_resp.data.get("total", len(contacts)) or len(contacts))
        tag_set = set()
        for c in contacts:
            for t in c.get("tags", []) or []:
                if isinstance(t, str) and t.strip():
                    tag_set.add(t.strip().lower())
        tags_seen = len(tag_set)

    if pipeline_id:
        opp_resp = request_with_retry(
            provider="ghl",
            operation="opportunities",
            method="GET",
            url=f"{base}/opportunities/search",
            headers=headers,
            params={"location_id": location_id, "pipeline_id": pipeline_id},
            timeout=10,
            max_attempts=2,
        )
        if opp_resp.ok and isinstance(opp_resp.data, dict):
            opportunities = opp_resp.data.get("opportunities", []) or []
            opportunities_total = len(opportunities)

    wf_resp = request_with_retry(
        provider="ghl",
        operation="workflows",
        method="GET",
        url=f"{base}/workflows/",
        headers=headers,
        params={"locationId": location_id},
        timeout=10,
        max_attempts=2,
    )
    if wf_resp.ok and isinstance(wf_resp.data, dict):
        workflows = wf_resp.data.get("workflows", []) or wf_resp.data.get("data", []) or []
        workflows_active = len(workflows)

    rev = _team_revenue_kpis("aiphoneguy")
    return {
        "available": True,
        "contacts_enrolled": contacts_total,
        "active_workflows": workflows_active,
        "reply_rate": rev["reply_rate"],
        "tag_count_seen": tags_seen,
        "pipeline_open": opportunities_total,
    }


def _hubspot_live_metrics() -> Dict[str, Any]:
    token = (os.getenv("HUBSPOT_API_KEY") or os.getenv("HUBSPOT_ACCESS_TOKEN") or "").strip()
    if not token:
        return {"available": False, "reason": "credentials_missing"}

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    base = "https://api.hubapi.com"

    deals_open: Optional[int] = None
    contacts_recent: Optional[int] = None
    stage_counts: Dict[str, int] = {}

    deals_resp = request_with_retry(
        provider="hubspot",
        operation="deals",
        method="POST",
        url=f"{base}/crm/v3/objects/deals/search",
        headers=headers,
        json_body={"properties": ["dealstage"], "limit": 100},
        timeout=10,
        max_attempts=2,
    )
    if deals_resp.ok and isinstance(deals_resp.data, dict):
        deals = deals_resp.data.get("results", []) or []
        open_count = 0
        for d in deals:
            stage = str((d.get("properties") or {}).get("dealstage", "")).strip().lower()
            if stage:
                stage_counts[stage] = stage_counts.get(stage, 0) + 1
            if stage not in {"closedwon", "closedlost"}:
                open_count += 1
        deals_open = open_count

    dt_30d = datetime.datetime.utcnow() - datetime.timedelta(days=30)
    contact_resp = request_with_retry(
        provider="hubspot",
        operation="contacts_recent",
        method="POST",
        url=f"{base}/crm/v3/objects/contacts/search",
        headers=headers,
        json_body={
            "filterGroups": [
                {
                    "filters": [
                        {
                            "propertyName": "lastmodifieddate",
                            "operator": "GTE",
                            "value": str(int(dt_30d.timestamp() * 1000)),
                        }
                    ]
                }
            ],
            "limit": 100,
        },
        timeout=10,
        max_attempts=2,
    )
    if contact_resp.ok and isinstance(contact_resp.data, dict):
        contacts_recent = len(contact_resp.data.get("results", []) or [])

    rev = _team_revenue_kpis("autointelligence")
    return {
        "available": True,
        "open_deals": deals_open,
        "contact_activity_30d": contacts_recent,
        "pipeline_stage_counts": stage_counts,
        "win_rate": rev["win_rate"],
    }


def _attio_live_metrics() -> Dict[str, Any]:
    token = (os.getenv("ATTIO_API_KEY") or "").strip()
    if not token:
        return {"available": False, "reason": "credentials_missing"}

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    base = "https://api.attio.com/v2"

    company_count: Optional[int] = None
    person_recent: Optional[int] = None

    company_resp = request_with_retry(
        provider="attio",
        operation="companies",
        method="POST",
        url=f"{base}/objects/companies/records/query",
        headers=headers,
        json_body={"limit": 100},
        timeout=10,
        max_attempts=2,
    )
    if company_resp.ok and isinstance(company_resp.data, dict):
        companies = company_resp.data.get("data", []) or []
        company_count = len(companies)

    person_resp = request_with_retry(
        provider="attio",
        operation="people_recent",
        method="POST",
        url=f"{base}/objects/people/records/query",
        headers=headers,
        json_body={"limit": 100},
        timeout=10,
        max_attempts=2,
    )
    if person_resp.ok and isinstance(person_resp.data, dict):
        people = person_resp.data.get("data", []) or []
        person_recent = len(people)

    rev = _team_revenue_kpis("callingdigital")
    return {
        "available": True,
        "client_records": company_count,
        "activity_feed_30d": person_recent,
        "pipeline_health": rev["raw"],
    }


def _crm_live_metrics(team_id: str, provider: str) -> Dict[str, Any]:
    p = (provider or "").strip().lower()
    if p == "ghl":
        return _ghl_live_metrics()
    if p == "hubspot":
        return _hubspot_live_metrics()
    if p == "attio":
        return _attio_live_metrics()
    return {"available": False, "reason": f"unsupported_provider:{p}"}


def _team_bottlenecks(team_id: str, provider: str) -> List[Dict[str, str]]:
    preflight = _sales_preflight_report()
    provider_row = (preflight.get("by_provider") or {}).get(provider, {})
    rows: List[Dict[str, str]] = []

    for missing in provider_row.get("missing_requirements", []) or []:
        rows.append({"level": "red", "message": f"Missing credential or scope: {missing}"})

    artifacts = _artifact_pipeline_counts()
    if artifacts["approval_queue"] > 0:
        rows.append({"level": "amber", "message": f"{artifacts['approval_queue']} artifacts waiting in approval queue"})

    if not rows:
        rows.append({"level": "green", "message": "No active blockers detected from live telemetry."})
    return rows


def _agent_artifact_risk_summary(agent_id: str) -> Dict[str, int]:
    """Return artifact risk counts for a specific agent from persisted artifacts."""
    if not DATABASE_URL:
        return {"pending": 0, "escalated": 0, "failed": 0}
    try:
        rows = fetch_all(
            """
            SELECT status, COUNT(*)
            FROM artifacts
            WHERE agent_id = %s
            GROUP BY status
            """,
            (agent_id,),
        )
    except Exception:
        return {"pending": 0, "escalated": 0, "failed": 0}

    counts = {str(status): int(count) for status, count in rows}
    return {
        "pending": counts.get("pending_approval", 0),
        "escalated": counts.get("escalated", 0),
        "failed": counts.get("failed", 0) + counts.get("dispatch_failed", 0),
    }


def _agent_risk_flags(team_id: str, agent_id: str, last_run: Optional[str]) -> List[str]:
    """Compute deterministic risk flags from live system telemetry."""
    flags: List[str] = []

    team_agents = BUSINESSES.get(team_id, {}).get("agents", [])
    provider = resolve_provider(team_id, agent_id if agent_id in team_agents else (team_agents[0] if team_agents else ""))
    preflight = _sales_preflight_report()
    provider_row = (preflight.get("by_provider") or {}).get(provider, {})

    for missing in provider_row.get("missing_requirements", []) or []:
        flags.append(f"Provider readiness issue: missing {missing}")

    risk_counts = _agent_artifact_risk_summary(agent_id)
    if risk_counts["escalated"] > 0:
        flags.append(f"{risk_counts['escalated']} escalated artifacts require explicit sign-off")
    if risk_counts["pending"] > 0:
        flags.append(f"{risk_counts['pending']} artifacts are waiting in approval queue")
    if risk_counts["failed"] > 0:
        flags.append(f"{risk_counts['failed']} artifact dispatch failures detected")

    if _derive_status_from_last_run(last_run) == "red":
        flags.append("Agent run freshness is stale (>24h) based on latest run telemetry")

    if not flags:
        flags.append("No active risk flags from live telemetry.")
    return flags


def _agent_grounded_focus_and_actions(
    team_id: str,
    agent_id: str,
    last_run: Optional[str],
) -> Dict[str, List[str]]:
    """Build focus/actions from measurable sources only (no narrative log parsing)."""
    team_kpis = _team_revenue_kpis(team_id)
    raw = team_kpis.get("raw", {})
    artifacts = _agent_artifact_risk_summary(agent_id)
    pipeline = _artifact_pipeline_counts()
    role = PITWALL_AGENT_META.get(agent_id, {}).get("role", "")
    status = _derive_status_from_last_run(last_run)

    team_agents = BUSINESSES.get(team_id, {}).get("agents", [])
    provider = resolve_provider(team_id, agent_id if agent_id in team_agents else (team_agents[0] if team_agents else ""))
    preflight = _sales_preflight_report()
    provider_row = (preflight.get("by_provider") or {}).get(provider, {})
    missing = provider_row.get("missing_requirements", []) or []

    focus: List[str] = [
        f"Open opportunities: {team_kpis.get('open_opps', 0)} (30d).",
        (
            f"Reply rate: {team_kpis.get('reply_rate', 0.0)}% "
            f"from {raw.get('replies', 0)} replies / {raw.get('emails', 0)} emails."
        ),
        (
            f"Win rate: {team_kpis.get('win_rate', 0.0)}% "
            f"from {raw.get('closed', 0)} closed / {raw.get('demos', 0)} demos."
        ),
        (
            f"Agent artifact load: pending {artifacts['pending']}, "
            f"escalated {artifacts['escalated']}, failed {artifacts['failed']}."
        ),
    ]

    if "Sales" in role or "Revenue" in role or role == "CEO":
        focus.append(f"Prospecting volume (30d): {raw.get('prospects', 0)} prospects created.")
    if "Marketing" in role:
        focus.append(f"Email output (30d): {raw.get('emails', 0)} sends tracked.")
    if "Client Success" in role or "Implementation" in role:
        focus.append(f"Approval queue pressure: {pipeline.get('approval_queue', 0)} items system-wide.")

    actions: List[str] = []
    if missing:
        actions.extend([f"Resolve provider requirement: {item}." for item in missing[:3]])
    if artifacts["escalated"] > 0:
        actions.append(f"Review and sign off {artifacts['escalated']} escalated artifacts.")
    if artifacts["pending"] > 0:
        actions.append(f"Process {artifacts['pending']} pending approval artifacts.")
    if artifacts["failed"] > 0:
        actions.append(f"Investigate {artifacts['failed']} failed dispatch events.")
    if status == "red":
        actions.append("Trigger immediate run-now for this lane (last run stale >24h).")

    if not actions:
        actions = [
            (
                f"Maintain cadence: keep reply rate >= {team_kpis.get('reply_rate', 0.0)}% "
                f"with current tracked volume ({raw.get('emails', 0)} emails)."
            ),
            "No urgent remediation actions required from live telemetry.",
        ]

    return {
        "focus": focus[:5],
        "next_actions": actions[:5],
    }



# ── Revenue Pipeline Helper ─────────────────────────────────────────────────

def _execute_sales_pipeline(agent_name: str, raw_output: str, business_key: str):
    """
    Universal sales pipeline executor. Takes any sales agent's output and:
    1. Parses prospects with email engine
    2. Routes to mapped CRM (GHL / HubSpot / Attio)
    3. Sends first-touch cold emails when CRM/provider supports it
    4. Creates opportunities/pipeline records when CRM/provider supports it
    5. Tracks all revenue events
    """
    provider = resolve_provider(business_key=business_key, agent_name=agent_name)
    if not _crm_ready_for(business_key, agent_name):
        logging.info(
            f"[Pipeline] Skipping CRM push for {agent_name} — provider={provider} not configured."
        )
        return {
            "status": "skipped",
            "reason": "crm_not_configured",
            "crm_provider": provider,
            "parsed_prospects": 0,
            "crm_created": 0,
            "duplicate_skipped": 0,
            "ghl_created": 0,
            "emails_attempted": 0,
            "emails_sent": 0,
            "emails_failed": 0,
            "provider_not_email_capable": 0,
            "failed": 0,
        }

    try:
        prospects = parse_prospects(raw_output, agent_name=agent_name)
        raw_preview = (raw_output or "")[:400].replace("\n", " ")
        if not prospects:
            logging.warning(f"[Pipeline] No prospects parsed from {agent_name}'s output.")
            return {
                "status": "ok",
                "parsed_prospects": 0,
                "crm_provider": provider,
                "crm_created": 0,
                "duplicate_skipped": 0,
                "ghl_created": 0,
                "emails_attempted": 0,
                "emails_sent": 0,
                "emails_failed": 0,
                "provider_not_email_capable": 0,
                "failed": 0,
                "raw_preview": raw_preview,
            }

        # ── ICP validation: discard off-profile prospects ──
        prospects, icp_discarded = validate_and_filter_prospects(prospects, agent_name)
        if not prospects:
            logging.warning(f"[Pipeline] All prospects discarded by ICP for {agent_name}.")
            return {
                "status": "ok",
                "parsed_prospects": len(icp_discarded),
                "icp_discarded": len(icp_discarded),
                "crm_provider": provider,
                "crm_created": 0,
                "duplicate_skipped": 0,
                "ghl_created": 0,
                "emails_attempted": 0,
                "emails_sent": 0,
                "emails_failed": 0,
                "provider_not_email_capable": 0,
                "failed": 0,
                "raw_preview": raw_preview,
            }

        # ── Contact enrichment: fill gaps in email/phone/name via web search ──
        prospects = enrich_prospects(prospects, only_missing_email=False)

        # ── Tech stack enrichment: score AI-readiness via Bloomberry (Ryan only) ──
        if agent_name == "ryan_data" and bloomberry_ready():
            prospects = enrich_prospects_tech(prospects)
            # Filter out dealerships already using AI (Bloomberry verdict = "exclude")
            before_count = len(prospects)
            excluded = [p for p in prospects if p.get("tech_verdict") == "exclude"]
            prospects = [p for p in prospects if p.get("tech_verdict") != "exclude"]
            if excluded:
                logging.info(
                    "[Bloomberry] Filtered %d/%d prospects (already using AI): %s",
                    len(excluded), before_count,
                    ", ".join(p.get("business_name", "") for p in excluded),
                )
            # Inject Bloomberry pain points as trigger_event for outreach
            for p in prospects:
                pain = p.get("tech_pain_points", [])
                if pain and not p.get("trigger_event"):
                    p["trigger_event"] = ". ".join(pain[:2])
                verdict = p.get("tech_verdict", "")
                if verdict in ("prime", "strong") and not p.get("verified_fact"):
                    p["verified_fact"] = p.get("tech_reason", "")

        crm_provider, crm_results = push_prospects_to_crm(
            prospects,
            source_agent=agent_name,
            business_key=business_key,
        )

        created = 0
        duplicate_skipped = 0
        emails_attempted = 0
        emails_sent = 0
        emails_failed = 0
        workflow_touched = 0
        provider_not_email_capable = 0
        email_capability_reason = ""
        email_capability = _provider_email_capability(crm_provider)
        if not email_capability.get("email_supported", False):
            provider_not_email_capable = len(crm_results)
            email_capability_reason = f"email_not_supported_provider:{crm_provider}"

        failed = 0
        failure_samples = []
        for r in crm_results:
            if r.get("status") == "created":
                created += 1
                track_event(
                    "prospect_created", business_key, agent_name,
                    contact_id=r.get("contact_id", ""),
                    monetary_value={"tyler": 482, "marcus": 2500, "ryan_data": 2500}.get(agent_name, 0),
                    metadata={"business_name": r.get("business_name")},
                )
                if r.get("template_key"):
                    track_event(
                        "email_template_applied", business_key, agent_name,
                        contact_id=r.get("contact_id", ""),
                        metadata={
                            "business_name": r.get("business_name"),
                            "template_key": r.get("template_key", ""),
                            "template_valid": bool(r.get("template_valid", True)),
                            "template_issues": r.get("template_issues", []),
                        },
                    )
            if r.get("status") == "duplicate_skipped":
                duplicate_skipped += 1
            if r.get("workflow_touched"):
                workflow_touched += 1
            if r.get("email_attempted"):
                emails_attempted += 1
            if r.get("email_sent"):
                emails_sent += 1
                track_event(
                    "email_sent", business_key, agent_name,
                    contact_id=r.get("contact_id", ""),
                    metadata={
                        "business_name": r.get("business_name"),
                        "email": (r.get("contact_email") or "").lower().strip(),
                        "template_key": r.get("template_key", ""),
                    },
                )
            if r.get("email_attempted") and not r.get("email_sent"):
                emails_failed += 1
            if r.get("status") == "failed":
                failed += 1
                if len(failure_samples) < 3:
                    failure_samples.append(
                        {
                            "business_name": r.get("business_name", ""),
                            "error": r.get("error", ""),
                        }
                    )

        logging.info(
            f"[Pipeline] {agent_name}: {created}/{len(prospects)} records created in {crm_provider}, "
            f"{workflow_touched} workflow-ready, {emails_sent} emails sent."
        )
        return {
            "status": "ok",
            "parsed_prospects": len(prospects),
            "icp_discarded": len(icp_discarded),
            "crm_provider": crm_provider,
            "crm_created": created,
            "workflow_touched": workflow_touched,
            "duplicate_skipped": duplicate_skipped,
            "ghl_created": created if crm_provider == "ghl" else 0,
            "emails_attempted": emails_attempted,
            "emails_sent": emails_sent,
            "emails_failed": emails_failed,
            "provider_not_email_capable": provider_not_email_capable,
            "email_capability_reason": email_capability_reason,
            "failed": failed,
            "failure_samples": failure_samples,
            "raw_preview": (raw_output or "")[:400].replace("\n", " "),
        }

    except Exception as e:
        logging.error(f"[Pipeline] {agent_name} pipeline failed: {e}")
        return {
            "status": "error",
            "error": f"{type(e).__name__}: {e}",
            "parsed_prospects": 0,
            "crm_provider": provider,
            "crm_created": 0,
            "workflow_touched": 0,
            "duplicate_skipped": 0,
            "ghl_created": 0,
            "emails_attempted": 0,
            "emails_sent": 0,
            "emails_failed": 0,
            "provider_not_email_capable": 0,
            "failed": 0,
        }


def _default_content_cta_url(business_key: str) -> str:
    env_key = f"{(business_key or '').strip().upper()}_PRIMARY_CTA_URL"
    configured = (os.getenv(env_key) or "").strip()
    if configured:
        return configured

    defaults = {
        "callingdigital": "https://calling.digital",
        "autointelligence": "https://automotiveintelligence.io",
        "aiphoneguy": "https://theaiphoneguy.ai",
    }
    return defaults.get((business_key or "").strip().lower(), "")


def _normalize_content_pieces(pieces: List[Dict[str, Any]], business_key: str) -> List[Dict[str, Any]]:
    normalized: List[Dict[str, Any]] = []
    business_key = (business_key or "").strip().lower()
    cta_url = _default_content_cta_url(business_key)

    for piece in pieces:
        current = dict(piece)
        body = (current.get("body") or "").strip()
        cta = (current.get("cta") or "").strip()
        title = (current.get("title") or "").strip()

        if business_key == "callingdigital":
            # Nova is an internal operator, not the public-facing brand.
            body = body.replace("Nova AI Consulting", "Calling Digital")
            body = body.replace("Nova AI", "Calling Digital")
            cta = cta.replace("Nova AI Consulting", "Calling Digital")
            cta = cta.replace("Nova AI", "Calling Digital")
            title = title.replace("Nova AI Consulting", "Calling Digital")
            title = title.replace("Nova AI", "Calling Digital")

        if cta_url:
            for placeholder in ("[Link]", "[link]", "(link)", "(Link)"):
                body = body.replace(placeholder, cta_url)
                cta = cta.replace(placeholder, cta_url)

        if business_key == "callingdigital" and not cta and cta_url:
            cta = f"Book a free strategy call → {cta_url}"

        current["title"] = title
        current["body"] = body
        current["cta"] = cta
        normalized.append(current)

    return normalized


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
            return {
                "status": "ok",
                "parsed_pieces": 0,
                "queued": 0,
            }

        pieces = _normalize_content_pieces(pieces, business_key)
        queued = queue_content(business_key, agent_name, pieces)
        if queued:
            track_event(
                "content_queued", business_key, agent_name,
                metadata={"pieces_queued": queued, "platforms": [p.get("platform") for p in pieces]},
            )
        logging.info(f"[Content] {agent_name}: {queued} pieces queued for publishing.")
        return {
            "status": "ok",
            "parsed_pieces": len(pieces),
            "queued": queued,
        }

    except Exception as e:
        logging.error(f"[Content] {agent_name} content pipeline failed: {e}")
        return {
            "status": "error",
            "error": f"{type(e).__name__}: {e}",
            "parsed_pieces": 0,
            "queued": 0,
        }


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


def _job_next_run_str(job) -> str:
    """Safely serialize job next-run time across APScheduler versions."""
    next_run = getattr(job, "next_run_time", None)
    return str(next_run) if next_run else "paused"


QUALITY_SHORT_OUTPUT_CHARS = 80
QUALITY_ERROR_TOKENS = ("error", "exception", "traceback", "failed")


def _compute_quality_snapshot(window_hours: int = 24) -> Dict[str, Any]:
    """Compute a quality snapshot from recent logs + artifact delivery outcomes."""
    if window_hours < 1:
        window_hours = 1

    if not DATABASE_URL:
        return {
            "status": "no_database",
            "message": "DATABASE_URL not configured.",
            "window_hours": window_hours,
        }

    try:
        log_rows = fetch_all(
            """
            SELECT agent_name, COALESCE(content, '')
            FROM agent_logs
            WHERE created_at >= NOW() - make_interval(hours => %s)
            """,
            (window_hours,),
        )

        total_runs = len(log_rows)
        active_agents = len({row[0] for row in log_rows})
        short_outputs = 0
        error_like_outputs = 0

        for _, content in log_rows:
            text = (content or "").strip()
            lower = text.lower()
            if len(text) < QUALITY_SHORT_OUTPUT_CHARS:
                short_outputs += 1
            if any(token in lower for token in QUALITY_ERROR_TOKENS):
                error_like_outputs += 1

        artifact_rows = fetch_all(
            """
            SELECT status, COUNT(*)
            FROM artifacts
            WHERE created_at >= NOW() - make_interval(hours => %s)
            GROUP BY status
            """,
            (window_hours,),
        )
        by_status = {status: int(count) for status, count in artifact_rows}
        delivered = by_status.get("delivered", 0)
        failed = by_status.get("failed", 0)

        agent_count = max(len(AGENTS), 1)
        availability_ratio = min(1.0, active_agents / float(agent_count))
        usable_ratio = 1.0
        if total_runs > 0:
            usable_ratio = max(0.0, (total_runs - short_outputs - error_like_outputs) / float(total_runs))
        delivery_denominator = delivered + failed
        delivery_ratio = (delivered / float(delivery_denominator)) if delivery_denominator > 0 else 1.0

        # Weighted score emphasizes output quality + reliability before scale.
        score = round(
            100.0 * (
                (0.4 * availability_ratio) +
                (0.4 * usable_ratio) +
                (0.2 * delivery_ratio)
            ),
            1,
        )

        return {
            "status": "ok",
            "created_at": datetime.datetime.now(datetime.UTC).isoformat(),
            "window_hours": window_hours,
            "score": score,
            "total_runs": total_runs,
            "active_agents": active_agents,
            "availability_ratio": round(availability_ratio, 4),
            "short_outputs": short_outputs,
            "error_like_outputs": error_like_outputs,
            "delivered_artifacts": delivered,
            "failed_artifacts": failed,
            "delivery_ratio": round(delivery_ratio, 4),
            "details": {
                "short_output_threshold_chars": QUALITY_SHORT_OUTPUT_CHARS,
                "artifact_status_counts": by_status,
            },
        }
    except Exception as e:
        logging.error(f"[Quality] snapshot computation failed: {e}")
        return {
            "status": "error",
            "message": f"{type(e).__name__}: {e}",
            "window_hours": window_hours,
        }


def _persist_quality_snapshot(snapshot: Dict[str, Any]) -> Optional[str]:
    """Persist computed quality snapshot for historical tracking."""
    if snapshot.get("status") != "ok":
        return None

    snapshot_id = str(uuid.uuid4())
    execute_query(
        """
        INSERT INTO quality_snapshots (
            snapshot_id, window_hours, total_runs, active_agents, availability_ratio,
            short_outputs, error_like_outputs, delivered_artifacts, failed_artifacts,
            delivery_ratio, score, details
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            snapshot_id,
            int(snapshot["window_hours"]),
            int(snapshot["total_runs"]),
            int(snapshot["active_agents"]),
            float(snapshot["availability_ratio"]),
            int(snapshot["short_outputs"]),
            int(snapshot["error_like_outputs"]),
            int(snapshot["delivered_artifacts"]),
            int(snapshot["failed_artifacts"]),
            float(snapshot["delivery_ratio"]),
            float(snapshot["score"]),
            json.dumps(snapshot.get("details", {})),
        ),
    )
    return snapshot_id


def run_quality_snapshot_daily():
    """Scheduled daily quality snapshot to create a durable operating history."""
    try:
        snapshot = _compute_quality_snapshot(window_hours=24)
        if snapshot.get("status") != "ok":
            logging.warning("[Quality] daily snapshot skipped: %s", snapshot)
            return
        snapshot_id = _persist_quality_snapshot(snapshot)
        logging.info("[Quality] daily snapshot saved id=%s score=%s", snapshot_id, snapshot.get("score"))
    except Exception as e:
        logging.error(f"[Quality] daily snapshot failed: {type(e).__name__}: {e}")


# ── CEO Briefings ── 8:00, 8:02, 8:04 CST ───────────────────────────────────

def run_alex_daily_briefing():
    try:
        kpi_context = _build_ceo_kpi_context("aiphoneguy")
        task = Task(
            description=(
                "Create a CEO OPERATING BRIEF for today for The AI Phone Guy. "
                "Focus on execution, revenue, team accountability, customer outcomes, and pipeline growth. "
                "Do NOT produce an industry-news briefing unless it directly affects today's execution plan. "
                "Use the internal KPI snapshot below as the source of truth for metrics and trends. "
                "If a metric is missing, say 'DATA NOT AVAILABLE' instead of guessing.\n\n"
                f"INTERNAL KPI SNAPSHOT:\n{kpi_context}\n\n"
                "Output must include: revenue status, pipeline status, customer satisfaction/retention status, "
                "team execution status, brand funnel actions (awareness/consideration/conversion), "
                "top 3 priorities for today with owner + deadline + expected outcome, "
                "a section titled 'Message to Founder' written directly to Michael, "
                "and a section titled 'Physical-World CEO Actions' with concrete in-person actions for today."
            ),
            expected_output=(
                "CEO Operating Brief: "
                "(1) Revenue Scorecard (current numbers + target gap), "
                "(2) Pipeline Scorecard (new prospects, outreach volume, conversion blockers), "
                "(3) Customer Health (retention/churn risk/actions), "
                "(4) Team Execution (who owns what today), "
                "(5) Brand Funnel Plan (awareness, consideration, conversion actions today), "
                "(6) Top 3 CEO priorities for today with owner, deadline, and expected business impact, "
                "(7) Message to Founder (directly to Michael), "
                "(8) Physical-World CEO Actions (in-person meetings/events/partnerships/recruiting/client visits)."
            ),
            agent=alex,
        )
        crew = Crew(agents=[alex], tasks=[task], process=Process.sequential, memory=False, verbose=False)
        result = crew.kickoff()
        guarded_output = _enforce_ceo_operating_brief("alex", str(result))
        persist_log("alex", "briefing", guarded_output)
        logging.info("[Scheduler] Alex briefing complete.")
    except Exception as e:
        logging.error(f"[Scheduler] Alex briefing failed: {type(e).__name__}: {e}")


def run_dek_daily_briefing():
    try:
        kpi_context = _build_ceo_kpi_context("callingdigital")
        task = Task(
            description=(
                "Create a CEO OPERATING BRIEF for today for Calling Digital. "
                "Focus on execution, revenue, team accountability, customer outcomes, and pipeline growth. "
                "Do NOT produce an industry-news briefing unless it directly affects today's execution plan. "
                "Use the internal KPI snapshot below as the source of truth for metrics and trends. "
                "If a metric is missing, say 'DATA NOT AVAILABLE' instead of guessing.\n\n"
                f"INTERNAL KPI SNAPSHOT:\n{kpi_context}\n\n"
                "Output must include: revenue status, pipeline status, customer satisfaction/retention status, "
                "team execution status, brand funnel actions (awareness/consideration/conversion), "
                "top 3 priorities for today with owner + deadline + expected outcome, "
                "a section titled 'Message to Founder' written directly to Michael, "
                "and a section titled 'Physical-World CEO Actions' with concrete in-person actions for today."
            ),
            expected_output=(
                "CEO Operating Brief: "
                "(1) Revenue Scorecard (current numbers + target gap), "
                "(2) Pipeline Scorecard (new prospects, outreach volume, conversion blockers), "
                "(3) Customer Health (retention/churn risk/actions), "
                "(4) Team Execution (who owns what today), "
                "(5) Brand Funnel Plan (awareness, consideration, conversion actions today), "
                "(6) Top 3 CEO priorities for today with owner, deadline, and expected business impact, "
                "(7) Message to Founder (directly to Michael), "
                "(8) Physical-World CEO Actions (in-person meetings/events/partnerships/recruiting/client visits)."
            ),
            agent=dek,
        )
        crew = Crew(agents=[dek], tasks=[task], process=Process.sequential, memory=False, verbose=False)
        result = crew.kickoff()
        guarded_output = _enforce_ceo_operating_brief("dek", str(result))
        persist_log("dek", "briefing", guarded_output)
        logging.info("[Scheduler] Dek briefing complete.")
    except Exception as e:
        logging.error(f"[Scheduler] Dek briefing failed: {type(e).__name__}: {e}")


def run_michael_meta_daily_briefing():
    try:
        kpi_context = _build_ceo_kpi_context("autointelligence")
        task = Task(
            description=(
                "Create a CEO OPERATING BRIEF for today for Automotive Intelligence. "
                "Focus on execution, revenue, team accountability, customer outcomes, and pipeline growth. "
                "Do NOT produce an industry-news briefing unless it directly affects today's execution plan. "
                "Use the internal KPI snapshot below as the source of truth for metrics and trends. "
                "If a metric is missing, say 'DATA NOT AVAILABLE' instead of guessing.\n\n"
                f"INTERNAL KPI SNAPSHOT:\n{kpi_context}\n\n"
                "Output must include: revenue status, pipeline status, customer satisfaction/retention status, "
                "team execution status, brand funnel actions (awareness/consideration/conversion), "
                "top 3 priorities for today with owner + deadline + expected outcome, "
                "a section titled 'Message to Founder' written directly to Michael, "
                "and a section titled 'Physical-World CEO Actions' with concrete in-person actions for today."
            ),
            expected_output=(
                "CEO Operating Brief: "
                "(1) Revenue Scorecard (current numbers + target gap), "
                "(2) Pipeline Scorecard (new prospects, outreach volume, conversion blockers), "
                "(3) Customer Health (retention/churn risk/actions), "
                "(4) Team Execution (who owns what today), "
                "(5) Brand Funnel Plan (awareness, consideration, conversion actions today), "
                "(6) Top 3 CEO priorities for today with owner, deadline, and expected business impact, "
                "(7) Message to Founder (directly to Michael), "
                "(8) Physical-World CEO Actions (in-person meetings/events/partnerships/recruiting/client visits)."
            ),
            agent=michael_meta,
        )
        crew = Crew(agents=[michael_meta], tasks=[task], process=Process.sequential, memory=False, verbose=False)
        result = crew.kickoff()
        guarded_output = _enforce_ceo_operating_brief("michael_meta", str(result))
        persist_log("michael_meta", "briefing", guarded_output)
        logging.info("[Scheduler] Michael Meta briefing complete.")
    except Exception as e:
        logging.error(f"[Scheduler] Michael Meta briefing failed: {type(e).__name__}: {e}")


# ── Sales Prospecting ── 8:30, 8:32, 8:34 CST ──────────────────────────────
# NOW REVENUE-ACTIVE: Parse → GHL Contact → Send Email → Create Opportunity → Track

def _run_tyler_crew():
    """Run Tyler's CrewAI prospecting with FORCED web_search tool usage.

    This prompt explicitly requires Tyler to call web_search for EVERY piece of
    information and return ZERO prospects if searches don't return verifiable data.
    Fabrication is explicitly forbidden.
    """
    task = Task(
        description=(
            "CRITICAL RULE: You MUST use the Web Search tool for research. NEVER fabricate "
            "data — every fact you return must come from a real web search result. "
            "However, you do NOT need every field filled. A prospect with business_name + "
            "city + website + one verified fact is GOOD ENOUGH even without email or phone. "
            "Return what you found. Leave unfound fields as empty strings.\n\n"
            "IF YOU RETURN A PHONE NUMBER, IT MUST BE A REAL NUMBER YOU FOUND IN A WEB "
            "SEARCH RESULT. Do NOT use 555 numbers. Do NOT use any phone with repeated or "
            "sequential digits. Do NOT invent phone numbers.\n\n"
            "IF YOU RETURN AN EMAIL, IT MUST BE EXPLICITLY STATED ON A WEB PAGE YOU FOUND. "
            "Do NOT guess email formats. Do NOT use 'firstname@company.com' unless you "
            "literally saw that exact email on a website. If no email is verifiable, "
            "return an empty string for email.\n\n"
            "IF YOU RETURN A CONTACT NAME, IT MUST BE A REAL PERSON YOU FOUND IN A WEB "
            "SEARCH RESULT. Do NOT use 'John Smith', 'Jane Doe', or any generic names. "
            "If no contact name is verifiable, return an empty string.\n\n"
            "=== YOUR TASK ===\n"
            "Find local service businesses in the DFW 380 Corridor "
            "(Aubrey, Celina, Prosper, Pilot Point, Little Elm, Denton, McKinney, Frisco, "
            "Flower Mound TX) that need an AI receptionist.\n\n"
            "REQUIRED SEARCH WORKFLOW (execute each step via Web Search tool):\n\n"
            "STEP 1: Use Web Search with a query like 'plumbers in Aubrey TX' or "
            "'HVAC companies Celina TX Google reviews' to find a list of REAL businesses. "
            "From the results, pick ONE real business that appears in multiple sources.\n\n"
            "STEP 2: For that business, use Web Search to find their website: "
            "'[business name] official website contact'. Read the contact page content "
            "in the search results to extract phone number, email, owner name.\n\n"
            "STEP 3: Use Web Search to find their Google reviews or Yelp reviews. Look for "
            "actual review text mentioning missed calls, slow response, or voicemail "
            "complaints. Quote the review text directly.\n\n"
            "STEP 4: Use Web Search to find their closest competitor in the same city "
            "with similar services but higher review count.\n\n"
            "STEP 5: Include the business if you found business_name + city + website + "
            "at least ONE verified fact or trigger event. Email, phone, contact_name, "
            "and competitive_insight are OPTIONAL — return empty string if not found. "
            "Do NOT skip a real business just because you couldn't find their email.\n\n"
            "REPEAT the workflow above until you have up to 3 fully verified prospects.\n\n"
            "MANDATORY: You MUST execute at least 3 Web Search queries before you can "
            "report 'No verifiable prospects found'. If you have not searched yet, SEARCH NOW.\n\n"
            "=== CRITICAL CONSTRAINTS ===\n"
            "- If your web searches return nothing useful, RETURN AN EMPTY LIST with the "
            "explanation 'No verifiable prospects found this run'. This is ACCEPTABLE.\n"
            "- ZERO prospects is BETTER than fabricated prospects.\n"
            "- You will be evaluated on VERIFIABILITY, not quantity.\n"
            "- Every field (phone, email, name, fact) must be traceable to a specific "
            "web search result. Include the source URL in the verified_fact field.\n\n"
            "=== ICP GUARDRAILS ===\n"
            "- Located in DFW North Metro (380 Corridor + surrounding suburbs)\n"
            "- Industry: Plumbers, HVAC, Roofers, Dental offices, or Personal Injury Law\n"
            "- Owner-operated or small team (1-10 employees)\n"
            "EXCLUDE: Franchises, chains, businesses already using AI receptionists\n\n"
            "DO NOT write cold emails. Instantly campaigns handle email sequences."
        ),
        expected_output=(
            "A structured prospect intelligence report with 0 to 3 VERIFIED prospects. "
            "Return ZERO prospects if web searches did not produce verifiable data.\n\n"
            "For each verified prospect, include ONLY fields you actually found via Web Search:\n"
            "- business_name (required, from search results)\n"
            "- business_type (e.g. 'Plumbing', 'HVAC', 'Dental', 'Roofing', 'Personal Injury Law')\n"
            "- city (city and state from search results)\n"
            "- contact_name (owner first + last name IF found on website/directory, else empty string)\n"
            "- email (direct email IF explicitly listed on website, else empty string)\n"
            "- phone (real phone from website contact page, else empty string)\n"
            "- website (website URL you found)\n"
            "- verified_fact (one specific fact with its source URL in parentheses)\n"
            "- trigger_event (specific event with source URL)\n"
            "- competitive_insight (what competitor does better, with competitor name and source URL)\n"
            "- reason (2-3 sentences on why this needs an AI receptionist)\n\n"
            "IF ZERO PROSPECTS: return the text 'No verifiable prospects found this run' "
            "and a brief explanation of what was searched and why nothing met the criteria."
        ),
        agent=tyler,
    )
    crew = Crew(agents=[tyler], tasks=[task], process=Process.sequential, memory=False, verbose=False)
    result = crew.kickoff()
    return str(result)


def run_tyler_prospecting():
    max_icp_attempts = 3
    for attempt in range(1, max_icp_attempts + 1):
        try:
            raw_output = _run_tyler_crew()
            persist_log("tyler", "prospecting", raw_output)
            logging.info("[Scheduler] Tyler prospecting complete (attempt %d).", attempt)

            pipeline_result = _execute_sales_pipeline("tyler", raw_output, "aiphoneguy")

            if pipeline_result.get("icp_discarded") and pipeline_result.get("crm_created", 0) == 0 and attempt < max_icp_attempts:
                logging.warning("[ICP] Tyler attempt %d: all prospects discarded, retrying.", attempt)
                continue

            return {"agent": "tyler", "pipeline": pipeline_result}

        except Exception as e:
            logging.error(f"[Scheduler] Tyler prospecting failed (attempt {attempt}): {type(e).__name__}: {e}")
            if attempt == max_icp_attempts:
                return {"agent": "tyler", "status": "error", "error": f"{type(e).__name__}: {e}"}
    return {"agent": "tyler", "status": "error", "error": "ICP retry exhausted"}


# ── Marcus vertical rotation ────────────────────────────────────────────────
# Cycles through verticals so each day targets a different ICP.
# Monday=med-spa, Tuesday=pi-law, Wednesday=real-estate, Thursday=home-builder, Friday=med-spa
# Region rotation — cycles per-run based on current hour so each of the
# 7 daily runs (0, 4, 8, 10, 12, 14, 16 CST) hits a different region.
PROSPECT_REGIONS = [
    ("Southwest", "Texas, Arizona, New Mexico, or Nevada", "Dallas TX", "Phoenix AZ"),
    ("Southeast", "Florida, Georgia, North Carolina, South Carolina, or Tennessee", "Miami FL", "Atlanta GA"),
    ("Northeast", "New York, New Jersey, Connecticut, Massachusetts, or Pennsylvania", "New York NY", "Boston MA"),
    ("Midwest", "Illinois, Ohio, Michigan, Minnesota, or Wisconsin", "Chicago IL", "Detroit MI"),
    ("West Coast", "California, Oregon, Washington, or Colorado", "Los Angeles CA", "Denver CO"),
    ("South Central", "Louisiana, Mississippi, Alabama, Arkansas, or Oklahoma", "New Orleans LA", "Nashville TN"),
    ("Mid-Atlantic", "Virginia, Maryland, Washington DC, or Delaware", "Washington DC", "Richmond VA"),
]


def _get_prospect_region() -> tuple:
    """Returns (region_name, states_phrase, city1, city2) rotating by hour."""
    from datetime import datetime
    import pytz
    cst = pytz.timezone("US/Central")
    hour = datetime.now(cst).hour
    idx = hour % len(PROSPECT_REGIONS)
    return PROSPECT_REGIONS[idx]


MARCUS_VERTICAL_ROTATION = {
    0: ("med-spa", "med spa", "med spas"),
    1: ("pi-law", "personal injury law", "personal injury law firms"),
    2: ("real-estate", "real estate", "real estate teams and brokerages"),
    3: ("home-builder", "custom home building", "custom home builders"),
    4: ("med-spa", "med spa", "med spas"),     # Friday cycles back
    5: ("pi-law", "personal injury law", "personal injury law firms"),
    6: ("real-estate", "real estate", "real estate teams and brokerages"),
}


def _get_marcus_vertical_today() -> tuple:
    """Returns (vertical_slug, industry_label, plural_label) for today."""
    from datetime import datetime
    import pytz
    cst = pytz.timezone("US/Central")
    weekday = datetime.now(cst).weekday()  # 0=Monday
    return MARCUS_VERTICAL_ROTATION.get(weekday, MARCUS_VERTICAL_ROTATION[0])


_MARCUS_VERTICAL_LOOKUP = {
    "med-spa": ("med-spa", "med spa", "med spas"),
    "pi-law": ("pi-law", "personal injury law", "personal injury law firms"),
    "real-estate": ("real-estate", "real estate", "real estate teams and brokerages"),
    "home-builder": ("home-builder", "custom home building", "custom home builders"),
}


def _run_marcus_crew(vertical_override: str | None = None):
    """Run Marcus's CrewAI prospecting with vertical rotation and FORCED web_search tool usage.

    Mirrors Tyler's anti-fabrication guardrails (commit 3c639ce) adapted for Marcus's
    Texas + vertical rotation scope. Marcus MUST use Web Search for every fact and
    return zero prospects rather than fabricate data.

    vertical_override: if provided (one of med-spa/pi-law/real-estate/home-builder),
    bypass the weekday rotation and force Marcus onto that vertical for this run.
    """
    if vertical_override and vertical_override in _MARCUS_VERTICAL_LOOKUP:
        vertical_slug, industry_label, plural_label = _MARCUS_VERTICAL_LOOKUP[vertical_override]
    else:
        vertical_slug, industry_label, plural_label = _get_marcus_vertical_today()

    region_name, region_states, region_city1, region_city2 = _get_prospect_region()

    task = Task(
        description=(
            f"CRITICAL RULE: You MUST use the Web Search tool for research. NEVER fabricate "
            f"data — every fact you return must come from a real web search result. "
            f"However, you do NOT need every field filled. A prospect with business_name + "
            f"city + website + one verified fact is GOOD ENOUGH even without email or phone. "
            f"Return what you found. Leave unfound fields as empty strings.\n\n"
            f"IF YOU RETURN A PHONE NUMBER, IT MUST BE A REAL NUMBER YOU FOUND IN A WEB "
            f"SEARCH RESULT. Do NOT use 555 numbers. Do NOT use any phone with repeated or "
            f"sequential digits (e.g. 888-8888, 555-1234, 123-4567). Do NOT invent phone numbers.\n\n"
            f"IF YOU RETURN AN EMAIL, IT MUST BE EXPLICITLY STATED ON A WEB PAGE YOU FOUND. "
            f"Do NOT guess email formats. Do NOT use 'firstname@company.com' or 'info@company.com' "
            f"unless you literally saw that exact email on a website. If no email is verifiable, "
            f"return an empty string for email.\n\n"
            f"IF YOU RETURN A CONTACT NAME, IT MUST BE A REAL PERSON YOU FOUND IN A WEB "
            f"SEARCH RESULT. Do NOT use 'John Smith', 'Jane Doe', 'Angel Reyes', or any "
            f"generic/plausible-sounding names. If no contact name is verifiable, return an "
            f"empty string.\n\n"
            f"=== YOUR TASK ===\n"
            f"TODAY'S VERTICAL: {plural_label.upper()} (tag: {vertical_slug})\n"
            f"TODAY'S REGION: {region_name.upper()} ({region_states})\n\n"
            f"Find {plural_label} in {region_states} that are experiencing TRIGGER EVENTS — "
            f"moments of change that create buying urgency for digital marketing services. "
            f"You are prospecting for Calling Digital, a digital marketing agency.\n\n"
            f"TRIGGER EVENTS TO HUNT FOR:\n"
            f"- New location opening or expansion\n"
            f"- Leadership change or new hire announcement\n"
            f"- Negative Google review streak (3+ recent bad reviews)\n"
            f"- Competitor in their city making aggressive digital moves\n"
            f"- New service launch, rebrand, or renovation\n"
            f"- Award, press mention, or milestone anniversary\n"
            f"- Seasonal demand approaching\n\n"
            f"REQUIRED SEARCH WORKFLOW (execute each step via Web Search tool):\n\n"
            f"STEP 1: Use Web Search with queries like '{plural_label} {region_city1} trigger event' "
            f"or '{plural_label} {region_city2} new location 2026' to find REAL businesses with "
            f"recent activity. From the results, pick businesses that appear in multiple sources.\n\n"
            f"STEP 2: For each candidate, use Web Search to find their official website: "
            f"'[business name] {plural_label} official website contact'. Read the contact page "
            f"content in the search results to extract phone number, email, owner/decision-maker name.\n\n"
            f"STEP 3: Use Web Search to find their Google reviews, press mentions, or news "
            f"articles that confirm the trigger event. Quote source URLs directly.\n\n"
            f"STEP 4: Use Web Search to find their closest local competitor and identify "
            f"what that competitor is doing digitally (SEO ranking, paid ads, social presence) "
            f"that this business is not.\n\n"
            f"STEP 5: Include the business if you found business_name + city + website + "
            f"at least ONE verified fact or trigger event. Email, phone, contact_name, "
            f"and competitive_insight are OPTIONAL — return empty string if not found. "
            f"Do NOT skip a real business just because you couldn't find their email.\n\n"
            f"REPEAT the workflow above until you have up to 3 fully verified prospects.\n\n"
            "MANDATORY: You MUST execute at least 3 Web Search queries before you can "
            "report 'No verifiable prospects found'. If you have not searched yet, SEARCH NOW.\n\n"
            f"=== CRITICAL CONSTRAINTS ===\n"
            f"- If your web searches return nothing useful, RETURN AN EMPTY LIST with the "
            f"explanation 'No verifiable prospects found this run'. This is ACCEPTABLE.\n"
            f"- ZERO prospects is BETTER than fabricated prospects.\n"
            f"- You will be evaluated on VERIFIABILITY, not quantity.\n"
            f"- Every field (phone, email, name, fact) must be traceable to a specific "
            f"web search result. Include the source URL in the verified_fact field.\n\n"
            f"=== ICP GUARDRAILS (MANDATORY) ===\n"
            f"- Located in {region_states}\n"
            f"- Vertical: {plural_label} ONLY for today's run\n"
            f"- Owner-operated or small team (not enterprise/corporate)\n"
            f"- Must have a real trigger event — do NOT prospect randomly\n"
            f"EXCLUDE: Enterprise companies, national chains, franchises, "
            f"businesses already using AI receptionists\n\n"
            f"DO NOT write cold emails. DO NOT pitch. Your job is RESEARCH and QUALIFICATION only.\n"
            f"The emails are pre-built in Attio sequences — you just deliver the intelligence."
        ),
        expected_output=(
            f"A structured prospect intelligence report with 0 to 3 VERIFIED prospects. "
            f"Return ZERO prospects if web searches did not produce verifiable data.\n\n"
            f"For each verified prospect, include ONLY fields you actually found via Web Search:\n"
            f"- business_name (required, from search results)\n"
            f"- business_type: '{industry_label}'\n"
            f"- vertical: '{vertical_slug}'\n"
            f"- city (city and state from search results)\n"
            f"- contact_name (owner first + last name IF found on website/directory, else empty string)\n"
            f"- email (direct email IF explicitly listed on website, else empty string)\n"
            f"- phone (real phone from website contact page, else empty string)\n"
            f"- website (website URL you found)\n"
            f"- verified_fact (one specific fact with its source URL in parentheses)\n"
            f"- trigger_event (specific event with source URL)\n"
            f"- competitive_insight (what competitor does better, with competitor name and source URL)\n"
            f"- reason (2-3 sentences on why this business needs digital marketing help)\n\n"
            f"IF ZERO PROSPECTS: return the text 'No verifiable prospects found this run' "
            f"and a brief explanation of what was searched and why nothing met the criteria.\n\n"
            f"DO NOT include cold emails. DO NOT include subject lines. Research only."
        ),
        agent=marcus,
    )
    crew = Crew(agents=[marcus], tasks=[task], process=Process.sequential, memory=False, verbose=False)
    result = crew.kickoff()
    return str(result)


def run_marcus_prospecting(vertical_override: str | None = None):
    """Run Marcus's full prospecting + ICP + enrollment pipeline.

    vertical_override: optional vertical slug (med-spa/pi-law/real-estate/home-builder)
    to force Marcus onto a specific vertical for this run, bypassing weekday rotation.
    Used by the /admin/run-now endpoint to sweep multiple verticals in one session.
    """
    max_icp_attempts = 3
    for attempt in range(1, max_icp_attempts + 1):
        try:
            raw_output = _run_marcus_crew(vertical_override=vertical_override)
            persist_log("marcus", "prospecting", raw_output)
            logging.info("[Scheduler] Marcus prospecting complete (attempt %d).", attempt)

            pipeline_result = _execute_sales_pipeline("marcus", raw_output, "callingdigital")

            if pipeline_result.get("icp_discarded") and pipeline_result.get("crm_created", 0) == 0 and attempt < max_icp_attempts:
                logging.warning("[ICP] Marcus attempt %d: all prospects discarded, retrying.", attempt)
                continue

            return {"agent": "marcus", "vertical_override": vertical_override, "pipeline": pipeline_result}

        except Exception as e:
            logging.error(f"[Scheduler] Marcus prospecting failed (attempt {attempt}): {type(e).__name__}: {e}")
            if attempt == max_icp_attempts:
                return {"agent": "marcus", "status": "error", "error": f"{type(e).__name__}: {e}"}
    return {"agent": "marcus", "status": "error", "error": "ICP retry exhausted"}


def _run_ryan_data_crew():
    """Run Ryan Data's CrewAI prospecting with FORCED web_search tool usage.

    Same verified-research-only pattern as Tyler. Fabrication is forbidden.
    """
    region_name, region_states, region_city1, region_city2 = _get_prospect_region()

    task = Task(
        description=(
            "CRITICAL RULE: You MUST use the Web Search tool for research. NEVER fabricate "
            "data — every fact you return must come from a real web search result. "
            "Your job is SIMPLE: find real car dealership WEBSITES. That's it. "
            "Bloomberry will score their tech stack and qualify them automatically. "
            "You do NOT need to find trigger events, GM names, or emails — just the "
            "dealership name, city, and website URL.\n\n"
            "IF YOU RETURN A PHONE NUMBER, IT MUST BE A REAL NUMBER FROM A WEB SEARCH. "
            "Do NOT use 555 numbers. If no phone found, return empty string.\n\n"
            "IF YOU RETURN AN EMAIL, IT MUST BE EXPLICITLY STATED ON A WEB PAGE. "
            "If no email found, return empty string. This is FINE — enrichment handles it.\n\n"
            "=== YOUR TASK ===\n"
            f"TODAY'S REGION: {region_name.upper()} ({region_states})\n\n"
            f"Find 3-5 car dealerships in {region_states}. Focus on finding their "
            "official WEBSITE URL. Everything else is optional.\n\n"
            "REQUIRED SEARCH WORKFLOW (execute each step via Web Search tool):\n\n"
            f"STEP 1: Use Web Search to find dealerships:\n"
            f"  - 'car dealerships {region_city1}'\n"
            f"  - 'Ford Chevrolet Honda dealership {region_city2}'\n"
            f"  - 'auto dealer {region_states.split(',')[0].strip()}'\n"
            "Pick dealerships that have their own website (not just a Cars.com listing).\n\n"
            "STEP 2: For each dealership, use Web Search: '[dealership name] official website' "
            "to confirm the website URL and find any contact info (phone, email, staff names).\n\n"
            "STEP 3: If you find a contact page, note the phone and any staff names. "
            "If not, that's OK — return the dealership with just name + city + website.\n\n"
            "REPEAT workflow until you have up to 3 fully verified dealership prospects.\n\n"
            "MANDATORY: You MUST execute at least 3 Web Search queries before you can "
            "report 'No verifiable prospects found'. If you have not searched yet, SEARCH NOW.\n\n"
            "=== CRITICAL CONSTRAINTS ===\n"
            "- If your web searches return nothing useful, RETURN AN EMPTY LIST with "
            "the explanation 'No verifiable prospects found this run'. This is ACCEPTABLE.\n"
            "- ZERO prospects is BETTER than fabricated prospects.\n"
            "- You will be evaluated on finding REAL dealerships with REAL websites.\n"
            "- Bloomberry handles qualification. You handle discovery.\n\n"
            "=== ICP GUARDRAILS ===\n"
            f"- Car dealerships in {region_states}\n"
            "- Must have their own website (not just a Cars.com or DealerRater listing)\n"
            "EXCLUDE: Buy-here-pay-here lots, auction-only, wholesale-only\n\n"
            "DO NOT write cold emails. Instantly campaigns handle email sequences."
        ),
        expected_output=(
            "A list of 3-5 car dealerships with their websites. "
            "Return ZERO if web searches found nothing.\n\n"
            "For each dealership:\n"
            "- business_name (required)\n"
            "- business_type (e.g. 'Ford Dealership', 'Chevrolet Dealership', 'Independent Used')\n"
            "- city (city and state)\n"
            "- website (required — the dealership's own website URL)\n"
            "- contact_name (if found, else empty string)\n"
            "- email (if found on website, else empty string)\n"
            "- phone (if found on website, else empty string)\n"
            "- group_affiliation (if known, else empty string)\n"
            "- verified_fact (anything notable from their website or reviews, else empty string)\n"
            "- trigger_event (empty string — Bloomberry handles this)\n"
            "- competitive_insight (empty string — not needed)\n"
            "- reason (empty string — Bloomberry generates this)\n\n"
            "IF ZERO: return 'No verifiable prospects found this run' with explanation."
        ),
        agent=ryan_data,
    )
    crew = Crew(agents=[ryan_data], tasks=[task], process=Process.sequential, memory=False, verbose=False)
    result = crew.kickoff()
    return str(result)


def run_ryan_data_prospecting():
    max_icp_attempts = 3
    for attempt in range(1, max_icp_attempts + 1):
        try:
            raw_output = _run_ryan_data_crew()
            persist_log("ryan_data", "prospecting", raw_output)
            logging.info("[Scheduler] Ryan Data prospecting complete (attempt %d).", attempt)

            pipeline_result = _execute_sales_pipeline("ryan_data", raw_output, "autointelligence")

            if pipeline_result.get("icp_discarded") and pipeline_result.get("crm_created", 0) == 0 and attempt < max_icp_attempts:
                logging.warning("[ICP] Ryan Data attempt %d: all prospects discarded, retrying.", attempt)
                continue

            return {"agent": "ryan_data", "pipeline": pipeline_result}

        except Exception as e:
            logging.error(f"[Scheduler] Ryan Data prospecting failed (attempt {attempt}): {type(e).__name__}: {e}")
            if attempt == max_icp_attempts:
                return {"agent": "ryan_data", "status": "error", "error": f"{type(e).__name__}: {e}"}
    return {"agent": "ryan_data", "status": "error", "error": "ICP retry exhausted"}


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
        pipeline_result = _execute_content_pipeline("zoe", raw_output, "aiphoneguy")
        return {"agent": "zoe", "pipeline": pipeline_result}

    except Exception as e:
        logging.error(f"[Scheduler] Zoe content failed: {type(e).__name__}: {e}")
        return {"agent": "zoe", "status": "error", "error": f"{type(e).__name__}: {e}"}


CALLING_DIGITAL_TOPIC_CLUSTERS = {
    "website_design": {
        "service_page": "https://www.calling.digital/website-design",
        "primary_keywords": [
            "website design aubrey tx",
            "small business website design texas",
            "website redesign cost for small business",
        ],
        "secondary_keywords": [
            "website redesign checklist",
            "small business website conversion rate",
            "seo friendly website design",
        ],
    },
    "seo_local": {
        "service_page": "https://www.calling.digital/local-seo",
        "primary_keywords": [
            "local seo aubrey tx",
            "local seo for small business texas",
            "seo company north texas",
        ],
        "secondary_keywords": [
            "google business profile optimization",
            "local keyword research",
            "local search rankings for service businesses",
        ],
    },
    "seo_general": {
        "service_page": "https://www.calling.digital/search-engine-optimization",
        "primary_keywords": [
            "search engine optimization for small business",
            "seo services for local businesses",
            "digital marketing agency seo texas",
        ],
        "secondary_keywords": [
            "technical seo audit",
            "on page seo for local businesses",
            "seo content strategy for small business",
        ],
    },
    "sem_ppc": {
        "service_page": "https://www.calling.digital/search-engine-marketing",
        "primary_keywords": [
            "google ads for roofers",
            "ppc management for contractors",
            "search engine marketing for small business",
        ],
        "secondary_keywords": [
            "cost per lead google ads local service business",
            "google ads landing page optimization",
            "remarketing for service businesses",
        ],
    },
    "social_media": {
        "service_page": "https://www.calling.digital/social-media-marketing-and-management-services",
        "primary_keywords": [
            "social media marketing for small business texas",
            "social media management aubrey tx",
            "facebook ads for local businesses",
        ],
        "secondary_keywords": [
            "social media content for contractors",
            "local business social media strategy",
            "instagram marketing for small business",
        ],
    },
    "data_aggregation": {
        "service_page": "https://www.calling.digital/data-aggregation",
        "primary_keywords": [
            "marketing reporting dashboard for small business",
            "data aggregation for marketing agencies",
            "small business marketing analytics texas",
        ],
        "secondary_keywords": [
            "cross channel attribution for smb",
            "marketing dashboard for local businesses",
            "lead source reporting small business",
        ],
    },
    "ai_implementation": {
        "service_page": "https://www.calling.digital/contact-us",
        "primary_keywords": [
            "ai automation for small business",
            "ai implementation services texas",
            "ai consulting for local business",
        ],
        "secondary_keywords": [
            "crm automation for smb",
            "lead follow up automation",
            "ai workflow automation for service businesses",
        ],
    },
}

CALLING_DIGITAL_TARGET_CITIES = [
    "Aubrey",
    "Prosper",
    "Celina",
    "Frisco",
    "McKinney",
    "Denton",
    "Little Elm",
    "Dallas",
    "North Texas",
    "380 Corridor",
]

CALLING_DIGITAL_ANCHOR_RULES = [
    ("local SEO services in Aubrey", "https://www.calling.digital/local-seo"),
    ("SEO services for small businesses", "https://www.calling.digital/search-engine-optimization"),
    ("website design for small businesses", "https://www.calling.digital/website-design"),
    ("Google Ads management for local service businesses", "https://www.calling.digital/search-engine-marketing"),
    ("social media management for small businesses", "https://www.calling.digital/social-media-marketing-and-management-services"),
    ("marketing reporting and data aggregation", "https://www.calling.digital/data-aggregation"),
    ("book a strategy session", "https://calendly.com/calling-michael/strategy-session"),
    ("contact Calling Digital", "https://www.calling.digital/contact-us"),
]

CALLING_DIGITAL_BLOG_LENGTH_GUIDANCE = (
    "Length strategy: write to search intent and competitive depth, not arbitrary word count. "
    "For high-value SEO guides and commercial investigation posts, default to 1600-2400 words because that range usually gives enough space "
    "to rank, demonstrate expertise, and convert without filler. "
    "For pillar or highly competitive explainers, expand to 2400-3200 words only if the topic truly needs deeper comparisons, examples, FAQs, and local proof. "
    "For narrow BOFU case studies, comparisons, or cost posts with tight intent, 1000-1500 words is acceptable if it answers the buying question completely. "
    "Never add fluff just to hit length. Depth, specificity, examples, FAQs, and clear conversion paths matter more than raw word count."
)


def _format_calling_digital_topic_map() -> str:
    lines = []
    for cluster, details in CALLING_DIGITAL_TOPIC_CLUSTERS.items():
        lines.append(
            f"- {cluster}: service page {details['service_page']} | primary keywords: {', '.join(details['primary_keywords'])} | secondary keywords: {', '.join(details['secondary_keywords'])}"
        )
    return "\n".join(lines)


def _format_calling_digital_anchor_rules() -> str:
    return "\n".join(f"- Use anchor text '{anchor}' -> {url}" for anchor, url in CALLING_DIGITAL_ANCHOR_RULES)


def run_sofia_content():
    try:
        booking_link_cd = (
            os.getenv("BOOKING_LINK_CD", "").strip()
            or "https://calendly.com/calling-michael/strategy-session"
        )
        task = Task(
            description=(
                "Search for trending topics in digital marketing, AI for business, Dallas business news, "
                "and small-business buyer questions that lead to service inquiries. "
                "Search for what other marketing agencies are publishing and what content is performing well. "
                "Create a revenue-focused Calling Digital content package for today. "
                "Public-facing content must use the Calling Digital brand name, never Nova AI Consulting. "
                "Do not use placeholder links like [Link] or [link]. Use concrete CTA destinations only. "
                "Prioritize buyer-intent topics Dallas and North Texas SMB owners actually search for before hiring an agency: "
                "website redesign cost, lead generation, SEO for local businesses, ads ROI, CRM follow-up, and AI automation for SMBs. "
                f"Target cities and geo modifiers: {', '.join(CALLING_DIGITAL_TARGET_CITIES)}. Use geo modifiers naturally when they strengthen local intent. "
                "Approved service-line keyword map:\n"
                f"{_format_calling_digital_topic_map()}\n"
                "For every blog/article output, choose exactly one primary keyword and 2-4 secondary keywords with commercial intent. "
                "Every blog/article must name the topic explicitly, define the search intent (informational, commercial investigation, or transactional), "
                "and explain why that topic is likely to drive a booked call instead of vanity traffic. "
                "Every blog/article must include 2-3 precise internal links and use meaningful anchor text. Approved anchor rules:\n"
                f"{_format_calling_digital_anchor_rules()}\n"
                f"Conversion CTAs must point to either https://www.calling.digital/contact-us or {booking_link_cd} unless a more precise Calling Digital page is clearly better. "
                "Avoid generic trend roundups unless they are anchored to a local SMB buying problem and a specific service outcome. "
                "Use blog frames that convert: cost breakdown, mistakes to avoid, checklist, local case study, service comparison, audit framework, or ROI explainer. "
                f"{CALLING_DIGITAL_BLOG_LENGTH_GUIDANCE} "
                "Create exactly one ready-to-publish long-form Ghost blog article today, not just an idea. "
                "That blog article must include: title, recommended slug, funnel stage, topic, primary keyword, secondary keywords, search intent, target city or region, "
                "suggested meta description, recommended word-count target, 5-8 section outline, 2-3 internal links with anchor text, one primary CTA, one secondary CTA, and the full article body. "
                "The blog body must be complete and publication-ready, with a strong introduction, scannable subheads, concrete examples, FAQ-style clarity where useful, "
                "and a direct conversion section near the end. "
                "After the full blog draft, provide one consideration-stage asset idea, one conversion-stage asset idea, one AI education piece idea for the Calling Digital AI services pipeline, "
                "and one ready-to-publish social post that promotes the blog."
            ),
            expected_output=(
                "Daily content package: (1) one full ready-to-publish Ghost blog draft with title, slug, topic, primary keyword, secondary keywords, "
                "search intent, target geography, meta description, target word count, internal links with anchor text, CTA plan, and complete article body. "
                "(2) one consideration-stage content asset idea. "
                "(3) one conversion-stage content asset idea. "
                "(4) one AI education piece idea for the Calling Digital AI services pipeline. "
                "(5) one social post ready to publish for Calling Digital that promotes the blog."
            ),
            agent=sofia,
        )
        crew = Crew(agents=[sofia], tasks=[task], process=Process.sequential, memory=False, verbose=False)
        result = crew.kickoff()
        raw_output = str(result)
        persist_log("sofia", "content", raw_output)
        logging.info("[Scheduler] Sofia content complete.")

        # ── CONTENT PIPELINE: Parse → Queue → Track ──
        pipeline_result = _execute_content_pipeline("sofia", raw_output, "callingdigital")
        return {"agent": "sofia", "pipeline": pipeline_result}

    except Exception as e:
        logging.error(f"[Scheduler] Sofia content failed: {type(e).__name__}: {e}")
        return {"agent": "sofia", "status": "error", "error": f"{type(e).__name__}: {e}"}


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
        pipeline_result = _execute_content_pipeline("chase", raw_output, "autointelligence")
        return {"agent": "chase", "pipeline": pipeline_result}

    except Exception as e:
        logging.error(f"[Scheduler] Chase content failed: {type(e).__name__}: {e}")
        return {"agent": "chase", "status": "error", "error": f"{type(e).__name__}: {e}"}



# ── Ghost Drain ── 9:20 CST (runs after all content agents queue) ────────────
def run_ghost_drain_callingdigital():
    """Drain queued blog content to Ghost CMS for Calling Digital. Runs daily at 9:20 CST."""
    try:
        if not ghost_publish_ready("callingdigital"):
            logging.warning("[Scheduler] Ghost drain skipped — callingdigital not configured.")
            return {"status": "skipped", "reason": "Ghost not configured"}

        social_platforms = {"linkedin", "twitter", "x", "instagram", "facebook", "tiktok", "youtube"}
        queued_all = get_content_queue(business_key="callingdigital", status="queued", limit=100)
        queued = [q for q in queued_all if (q.get("platform") or "").strip().lower() not in social_platforms][:5]

        if not queued:
            logging.info("[Scheduler] Ghost drain: no queued blog content for callingdigital.")
            return {"status": "ok", "published": 0, "message": "Queue empty"}

        published = 0
        failed = 0
        results = []
        for item in queued:
            enriched = dict(item)
            enriched["business_key"] = "callingdigital"
            enriched = _normalize_content_pieces([enriched], "callingdigital")[0]
            try:
                result = publish_content_to_ghost(enriched)
                mark_content_published(item["id"])
                track_event(
                    "content_published",
                    business_key="callingdigital",
                    agent_name=item.get("agent_name", "sofia"),
                    metadata={
                        "content_id": item.get("id"),
                        "provider": "ghost",
                        "published_url": result.get("url", ""),
                        "slug": result.get("slug", ""),
                    },
                )
                results.append({"content_id": item.get("id"), "status": "published", "url": result.get("url", "")})
                published += 1
                logging.info("[Scheduler] Ghost published: %s → %s", result.get("slug"), result.get("url"))
            except Exception as e:
                failed += 1
                logging.warning("[Scheduler] Ghost publish failed for content_id=%s: %s", item.get("id"), e)
                results.append({"content_id": item.get("id"), "status": "failed", "error": str(e)})

        logging.info("[Scheduler] Ghost drain done — published=%d failed=%d", published, failed)
        return {"status": "ok", "published": published, "failed": failed, "results": results}

    except Exception as e:
        logging.error("[Scheduler] Ghost drain failed: %s: %s", type(e).__name__, e)
        return {"status": "error", "error": f"{type(e).__name__}: {e}"}


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


# ── AVO Intelligence Wrappers (must be defined before scheduler registrations) ─

def _avo_sched_alex():
    _avo_wrap_run("alex", "aiphoneguy", run_alex_daily_briefing)

def _avo_sched_dek():
    _avo_wrap_run("dek", "callingdigital", run_dek_daily_briefing)

def _avo_sched_michael_meta():
    _avo_wrap_run("michael_meta", "autointelligence", run_michael_meta_daily_briefing)

def _avo_sched_tyler():
    _avo_wrap_run("tyler", "aiphoneguy", run_tyler_prospecting)

def _avo_sched_marcus():
    _avo_wrap_run("marcus", "callingdigital", run_marcus_prospecting)

def _avo_sched_ryan_data():
    _avo_wrap_run("ryan_data", "autointelligence", run_ryan_data_prospecting)

def _avo_sched_zoe():
    _avo_wrap_run("zoe", "aiphoneguy", run_zoe_content)

def _avo_sched_sofia():
    _avo_wrap_run("sofia", "callingdigital", run_sofia_content)

def _avo_sched_chase():
    _avo_wrap_run("chase", "autointelligence", run_chase_content)

def _avo_sched_jennifer():
    _avo_wrap_run("jennifer", "aiphoneguy", run_jennifer_retention)

def _avo_sched_carlos():
    _avo_wrap_run("carlos", "callingdigital", run_carlos_retention)

def _avo_sched_nova():
    _avo_wrap_run("nova", "callingdigital", run_nova_intelligence)

def _avo_sched_atlas():
    _avo_wrap_run("atlas", "autointelligence", run_atlas_intel)

def _avo_sched_phoenix():
    _avo_wrap_run("phoenix", "autointelligence", run_phoenix_delivery)

def _avo_sched_axiom():
    try:
        from agents.ceo.axiom import run_axiom
        _avo_wrap_run("axiom", "ceo", run_axiom)
    except Exception as e:
        logging.error(f"[AVO] Axiom scheduled run failed: {e}")


# ── Register Scheduler Jobs ──────────────────────────────────────────────────

# COO Command — 7:45 (runs before all other agents)
scheduler.add_job(run_coo_command, CronTrigger(hour=7, minute=45, timezone=CST),
    id="coo_command_daily", name="COO Command Daily Ops",
    replace_existing=True, misfire_grace_time=3600)

# CEOs — 8:00, 8:02, 8:04 (once daily — strategic briefing) [AVO wrapped]
scheduler.add_job(_avo_sched_alex, CronTrigger(hour=8, minute=0, timezone=CST),
    id="alex_daily_briefing", name="Alex Daily Briefing",
    replace_existing=True, misfire_grace_time=3600)

scheduler.add_job(_avo_sched_dek, CronTrigger(hour=8, minute=2, timezone=CST),
    id="dek_daily_briefing", name="Dek Daily Briefing",
    replace_existing=True, misfire_grace_time=3600)

scheduler.add_job(_avo_sched_michael_meta, CronTrigger(hour=8, minute=4, timezone=CST),
    id="michael_meta_daily_briefing", name="Michael Meta Daily Briefing",
    replace_existing=True, misfire_grace_time=3600)

# Sales — Tyler/Marcus/Ryan prospect during business hours + overnight runs
# Business hours: 8:30, 10:30, 12:30, 14:30, 16:30 CST
# Overnight:      0:00 (midnight), 4:00 CST
# All 3 agents run every window so RevOps has fresh pipeline by morning
SALES_HOURS = [0, 4, 8, 10, 12, 14, 16]

for hour in SALES_HOURS:
    scheduler.add_job(
        _avo_sched_tyler,
        CronTrigger(hour=hour, minute=30, timezone=CST),
        id=f"tyler_prospecting_{hour}30",
        name=f"Tyler Prospecting {hour}:30",
        replace_existing=True, misfire_grace_time=3600,
    )
    scheduler.add_job(
        _avo_sched_marcus,
        CronTrigger(hour=hour, minute=32, timezone=CST),
        id=f"marcus_prospecting_{hour}32",
        name=f"Marcus Prospecting {hour}:32",
        replace_existing=True, misfire_grace_time=3600,
    )
    scheduler.add_job(
        _avo_sched_ryan_data,
        CronTrigger(hour=hour, minute=34, timezone=CST),
        id=f"ryan_data_prospecting_{hour}34",
        name=f"Ryan Data Prospecting {hour}:34",
        replace_existing=True, misfire_grace_time=3600,
    )

# Marketing — 9:00, 9:02, 9:04 (once daily — content planning) [AVO wrapped]
scheduler.add_job(_avo_sched_zoe, CronTrigger(hour=9, minute=0, timezone=CST),
    id="zoe_daily_content", name="Zoe Daily Content",
    replace_existing=True, misfire_grace_time=3600)

scheduler.add_job(_avo_sched_sofia, CronTrigger(hour=9, minute=2, timezone=CST),
    id="sofia_daily_content", name="Sofia Daily Content",
    replace_existing=True, misfire_grace_time=3600)

scheduler.add_job(_avo_sched_chase, CronTrigger(hour=9, minute=4, timezone=CST),
    id="chase_daily_content", name="Chase Daily Content",
    replace_existing=True, misfire_grace_time=3600)

# Client Success — 9:30, 9:32
scheduler.add_job(run_ghost_drain_callingdigital, CronTrigger(hour=9, minute=20, timezone=CST),
    id="ghost_drain_callingdigital", name="Ghost Blog Drain — Calling Digital",
    replace_existing=True, misfire_grace_time=3600)

# Client Success — 9:30, 9:32 [AVO wrapped]
scheduler.add_job(_avo_sched_jennifer, CronTrigger(hour=9, minute=30, timezone=CST),
    id="jennifer_daily_retention", name="Jennifer Daily Retention",
    replace_existing=True, misfire_grace_time=3600)

scheduler.add_job(_avo_sched_carlos, CronTrigger(hour=9, minute=32, timezone=CST),
    id="carlos_daily_retention", name="Carlos Daily Retention",
    replace_existing=True, misfire_grace_time=3600)

# Specialists — 10:00, 10:02, 10:04 [AVO wrapped]
scheduler.add_job(_avo_sched_nova, CronTrigger(hour=10, minute=0, timezone=CST),
    id="nova_daily_intelligence", name="Nova Daily Intelligence",
    replace_existing=True, misfire_grace_time=3600)

scheduler.add_job(_avo_sched_atlas, CronTrigger(hour=10, minute=2, timezone=CST),
    id="atlas_daily_intel", name="Atlas Daily Intel",
    replace_existing=True, misfire_grace_time=3600)

scheduler.add_job(_avo_sched_phoenix, CronTrigger(hour=10, minute=4, timezone=CST),
    id="phoenix_daily_delivery", name="Phoenix Daily Delivery",
    replace_existing=True, misfire_grace_time=3600)

# Master Test Suite — 6:30 PM (18:30) CST — All Agents
scheduler.add_job(run_all_agents_test, CronTrigger(hour=18, minute=30, timezone=CST),
    id="all_agents_test", name="🧪 Master Test Suite - All Agents",
    replace_existing=True, misfire_grace_time=3600)

# Quality score snapshot — 6:45 PM CST (after daily activity and test suite)
scheduler.add_job(run_quality_snapshot_daily, CronTrigger(hour=18, minute=45, timezone=CST),
    id="quality_snapshot_daily", name="📈 Daily Quality Snapshot",
    replace_existing=True, misfire_grace_time=3600)

# ── Project Paperclip — RevOps Agents ─────────────────────────────────────────

def _run_randy():
    try:
        from rivers.ai_phone_guy.workflow import randy_run
        _avo_wrap_run("randy", "aiphoneguy", randy_run)
    except Exception as e:
        logging.error(f"[Paperclip] Randy scheduled run failed: {e}")

def _run_brenda():
    try:
        from rivers.calling_digital.workflow import brenda_run
        _avo_wrap_run("brenda", "callingdigital", brenda_run)
    except Exception as e:
        logging.error(f"[Paperclip] Brenda scheduled run failed: {e}")

def _run_darrell():
    try:
        from rivers.automotive_intelligence.workflow import darrell_run
        _avo_wrap_run("darrell", "autointelligence", darrell_run)
    except Exception as e:
        logging.error(f"[Paperclip] Darrell scheduled run failed: {e}")

def _run_joshua():
    try:
        from rivers.ai_phone_guy.pit_wall import joshua_run
        _avo_wrap_run("joshua", "aiphoneguy", joshua_run)
    except Exception as e:
        logging.error(f"[Paperclip] Joshua scheduled run failed: {e}")

def _run_tammy():
    try:
        from rivers.agent_empire.workflow import tammy_run
        _avo_wrap_run("tammy_ae", "agentempire", tammy_run)
    except Exception as e:
        logging.error(f"[Paperclip] Tammy scheduled run failed: {e}")

def _run_wade():
    try:
        from rivers.agent_empire.workflow import wade_run
        _avo_wrap_run("wade_ae", "agentempire", wade_run)
    except Exception as e:
        logging.error(f"[Paperclip] Wade scheduled run failed: {e}")

def _run_debra():
    try:
        from rivers.agent_empire.workflow import debra_run
        _avo_wrap_run("debra", "agentempire", debra_run)
    except Exception as e:
        logging.error(f"[Paperclip] Debra scheduled run failed: {e}")

def _run_clint():
    try:
        from rivers.customer_advocate.workflow import clint_run
        _avo_wrap_run("clint", "customeradvocate", clint_run)
    except Exception as e:
        logging.error(f"[Paperclip] Clint scheduled run failed: {e}")

def _run_sherry():
    try:
        from rivers.customer_advocate.workflow import sherry_run
        _avo_wrap_run("sherry", "customeradvocate", sherry_run)
    except Exception as e:
        logging.error(f"[Paperclip] Sherry scheduled run failed: {e}")

def _run_sterling():
    try:
        from rivers.agent_empire.workflow import sterling_run
        _avo_wrap_run("sterling", "agentempire", sterling_run)
    except Exception as e:
        logging.error(f"[Paperclip] Sterling scheduled run failed: {e}")


# Randy (GHL RevOps): every 4 hours
scheduler.add_job(_run_randy, IntervalTrigger(hours=4, timezone=CST),
    id="randy_revops_4h", name="Randy RevOps (GHL) — Every 4h",
    replace_existing=True, misfire_grace_time=3600)

# Brenda (Attio RevOps): every 2 hours
scheduler.add_job(_run_brenda, IntervalTrigger(hours=2, timezone=CST),
    id="brenda_revops_2h", name="Brenda RevOps (Attio) — Every 2h",
    replace_existing=True, misfire_grace_time=3600)

# Darrell (HubSpot RevOps + Pit Wall): every 1 hour
scheduler.add_job(_run_darrell, IntervalTrigger(hours=1, timezone=CST),
    id="darrell_revops_1h", name="Darrell RevOps (HubSpot + Pit Wall) — Every 1h",
    replace_existing=True, misfire_grace_time=3600)

# Joshua (Pit Wall — AI Phone Guy): every 2 hours
scheduler.add_job(_run_joshua, IntervalTrigger(hours=2, timezone=CST),
    id="joshua_pitwall_2h", name="Joshua Pit Wall (Instantly) — Every 2h",
    replace_existing=True, misfire_grace_time=3600)

# Morning Briefing — daily 8am CDT email + SMS to Michael
def _run_morning_briefing():
    try:
        from rivers.shared.morning_briefing import morning_briefing_run
        morning_briefing_run()
    except Exception as e:
        logging.error(f"[Paperclip] Morning briefing failed: {e}")

scheduler.add_job(_run_morning_briefing, CronTrigger(hour=8, minute=0, timezone=CST),
    id="morning_briefing_daily_8am", name="Morning Briefing (Email + SMS) — Daily 8am CST",
    replace_existing=True, misfire_grace_time=3600)

# Tammy (Skool Community): every 6 hours
scheduler.add_job(_run_tammy, IntervalTrigger(hours=6, timezone=CST),
    id="tammy_community_6h", name="Tammy Community (Skool) — Every 6h",
    replace_existing=True, misfire_grace_time=3600)

# Wade (Sponsor Outreach): Monday 9am CST
scheduler.add_job(_run_wade, CronTrigger(day_of_week="mon", hour=9, minute=0, timezone=CST),
    id="wade_sponsors_mon9am", name="Wade Sponsor Outreach — Mon 9am",
    replace_existing=True, misfire_grace_time=3600)

# Debra (Content Producer): Monday 6am CST
scheduler.add_job(_run_debra, CronTrigger(day_of_week="mon", hour=6, minute=0, timezone=CST),
    id="debra_content_mon6am", name="Debra Content Producer — Mon 6am",
    replace_existing=True, misfire_grace_time=3600)

# Clint (Technical Builder): 10am CST daily
scheduler.add_job(_run_clint, CronTrigger(hour=10, minute=10, timezone=CST),
    id="clint_build_daily", name="Clint Technical Builder — Daily 10:10am",
    replace_existing=True, misfire_grace_time=3600)

# Sherry (Web Design): 11am CST daily
scheduler.add_job(_run_sherry, CronTrigger(hour=11, minute=0, timezone=CST),
    id="sherry_design_daily", name="Sherry Web Design — Daily 11am",
    replace_existing=True, misfire_grace_time=3600)

# Sterling (Web Agent): daily 7am CST
scheduler.add_job(_run_sterling, CronTrigger(hour=7, minute=0, timezone=CST),
    id="sterling_web_daily", name="Sterling Web Agent — Daily 7am",
    replace_existing=True, misfire_grace_time=3600)

# Axiom CEO Orchestration: 11:30 PM CST daily (after all agents finish)
scheduler.add_job(_avo_sched_axiom, CronTrigger(hour=23, minute=30, timezone=CST),
    id="axiom_ceo_nightly", name="AXIOM CEO Orchestration — Nightly 11:30pm",
    replace_existing=True, misfire_grace_time=3600)

# Changelog: Friday 5pm CST
def _run_changelog():
    try:
        from paperclip.changelog_gen import run_changelog
        run_changelog()
    except Exception as e:
        logging.error(f"[Paperclip] Changelog generation failed: {e}")

scheduler.add_job(_run_changelog, CronTrigger(day_of_week="fri", hour=17, minute=0, timezone=CST),
    id="changelog_friday_5pm", name="Weekly Changelog — Fri 5pm",
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


RUN_NOW_SCOPES = {
    "ceo": [
        ("alex_daily_briefing", run_alex_daily_briefing),
        ("dek_daily_briefing", run_dek_daily_briefing),
        ("michael_meta_daily_briefing", run_michael_meta_daily_briefing),
    ],
    "sales": [
        ("tyler_prospecting", run_tyler_prospecting),
        ("marcus_prospecting", run_marcus_prospecting),
        ("ryan_data_prospecting", run_ryan_data_prospecting),
    ],
    "content": [
        ("zoe_content", run_zoe_content),
        ("sofia_content", run_sofia_content),
        ("chase_content", run_chase_content),
    ],
    "sofia": [
        ("sofia_content", run_sofia_content),
    ],
    "zoe": [
        ("zoe_content", run_zoe_content),
    ],
    "chase": [
        ("chase_content", run_chase_content),
    ],
    "ghost": [
        ("ghost_drain_callingdigital", run_ghost_drain_callingdigital),
    ],
    "retention": [
        ("jennifer_retention", run_jennifer_retention),
        ("carlos_retention", run_carlos_retention),
    ],
    "specialists": [
        ("nova_intelligence", run_nova_intelligence),
        ("atlas_intel", run_atlas_intel),
        ("phoenix_delivery", run_phoenix_delivery),
    ],
    "quality": [
        ("quality_snapshot_daily", run_quality_snapshot_daily),
    ],
    "coo": [
        ("coo_command", run_coo_command),
    ],
    "axiom": [
        ("axiom_ceo", _avo_sched_axiom),
    ],
    "revops": [
        ("randy_revops", _run_randy),
        ("brenda_revops", _run_brenda),
        ("darrell_revops", _run_darrell),
    ],
    "randy": [
        ("randy_revops", _run_randy),
    ],
    "brenda": [
        ("brenda_revops", _run_brenda),
    ],
    "darrell": [
        ("darrell_revops", _run_darrell),
    ],
    "tammy": [
        ("tammy_community", _run_tammy),
    ],
    "wade": [
        ("wade_sponsors", _run_wade),
    ],
    "debra": [
        ("debra_content", _run_debra),
    ],
    "sterling": [
        ("sterling_web", _run_sterling),
    ],
    "agentempire": [
        ("tammy_community", _run_tammy),
        ("wade_sponsors", _run_wade),
        ("debra_content", _run_debra),
        ("sterling_web", _run_sterling),
    ],
    "clint": [
        ("clint_build", _run_clint),
    ],
    "sherry": [
        ("sherry_design", _run_sherry),
    ],
    "customeradvocate": [
        ("clint_build", _run_clint),
        ("sherry_design", _run_sherry),
    ],
    "all": [
        ("alex_daily_briefing", run_alex_daily_briefing),
        ("dek_daily_briefing", run_dek_daily_briefing),
        ("michael_meta_daily_briefing", run_michael_meta_daily_briefing),
        ("tyler_prospecting", run_tyler_prospecting),
        ("marcus_prospecting", run_marcus_prospecting),
        ("ryan_data_prospecting", run_ryan_data_prospecting),
        ("zoe_content", run_zoe_content),
        ("sofia_content", run_sofia_content),
        ("chase_content", run_chase_content),
        ("jennifer_retention", run_jennifer_retention),
        ("carlos_retention", run_carlos_retention),
        ("nova_intelligence", run_nova_intelligence),
        ("atlas_intel", run_atlas_intel),
        ("phoenix_delivery", run_phoenix_delivery),
        ("randy_revops", _run_randy),
        ("brenda_revops", _run_brenda),
        ("darrell_revops", _run_darrell),
        ("tammy_community", _run_tammy),
        ("wade_sponsors", _run_wade),
        ("debra_content", _run_debra),
        ("sterling_web", _run_sterling),
        ("clint_build", _run_clint),
        ("sherry_design", _run_sherry),
        ("quality_snapshot_daily", run_quality_snapshot_daily),
    ],
}


# ── FastAPI App ──────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup configuration checks
    logging.info(
        "[Startup] env=%s strict=%s postgres=%s ghl=%s zernio=%s llm_model=%s llm_key=%s",
        SETTINGS.environment,
        SETTINGS.strict_startup,
        SETTINGS.postgres_enabled,
        SETTINGS.ghl_ready,
        zernio_ready(),
        SETTINGS.llm_model,
        SETTINGS.llm_api_key_present,
    )
    for warning in SETTINGS.startup_warnings():
        logging.warning(f"[Startup] {warning}")
    fatals = SETTINGS.startup_fatals()
    if fatals:
        for msg in fatals:
            logging.error(f"[Startup] {msg}")
        raise RuntimeError("; ".join(fatals))

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

    # ── Register scheduler jobs before startup so readiness is not blocked by bootstrap work
    try:
        from core.scheduler import register_all_jobs
        register_all_jobs(scheduler)
        logging.info("[Paperclip] All river agents registered on scheduler")
    except Exception as e:
        logging.warning(f"[Paperclip] Scheduler job registration failed: {e}")

    # ── Scheduler — never crash startup if APScheduler misfires
    try:
        scheduler.start()
        logging.info(f"[Scheduler] Started {len(scheduler.get_jobs())} agent jobs registered.")
    except Exception as e:
        logging.error(f"[Scheduler] Failed to start: {e}")

    startup_bootstrap_task = asyncio.create_task(asyncio.to_thread(_run_startup_bootstrap))
    logging.info("[Startup] Background bootstrap scheduled")

    yield

    # ── Shutdown
    if not startup_bootstrap_task.done():
        startup_bootstrap_task.cancel()

    try:
        scheduler.shutdown(wait=False)
    except Exception:
        pass
    logging.info("[Scheduler] Shut down.")


def _run_startup_bootstrap():
    # ── Zernio social media integration init
    try:
        if zernio_ready():
            profiles = get_zernio_profiles()
            logging.info(f"[Zernio] Initialized with {len(profiles)} profile(s)")
            for profile in profiles:
                try:
                    accounts = list_zernio_accounts(profile.get("_id"))
                    logging.info(f"[Zernio] Profile '{profile.get('name')}': {len(accounts)} account(s)")
                except Exception as e:
                    logging.warning(f"[Zernio] Could not list accounts for profile {profile.get('_id')}: {e}")
        else:
            logging.info("[Zernio] Not configured (ZERNIO_API_KEY not set)")
    except Exception as e:
        logging.warning(f"[Zernio] Init failed — social publishing via Zernio disabled: {e}")

    # ── Project Paperclip Rivers — 5 rivers, all agents
    try:
        from rivers.automotive_intelligence.cleanup import run_cleanup
        cleanup_result = run_cleanup()
        logging.info(f"[Paperclip] HubSpot cleanup: {cleanup_result}")

        try:
            from rivers.ai_phone_guy.workflow import randy_run
            randy_run()
        except Exception as e:
            logging.info(f"[Paperclip] Randy initial run skipped: {e}")
        try:
            from rivers.calling_digital.workflow import brenda_run
            brenda_run()
        except Exception as e:
            logging.info(f"[Paperclip] Brenda initial run skipped: {e}")
        try:
            from rivers.automotive_intelligence.workflow import darrell_run
            darrell_run()
        except Exception as e:
            logging.info(f"[Paperclip] Darrell initial run skipped: {e}")
        try:
            from rivers.agent_empire.workflow import tammy_run
            tammy_run()
        except Exception as e:
            logging.info(f"[Paperclip] Tammy initial run skipped: {e}")
        try:
            from rivers.customer_advocate.workflow import clint_run
            clint_run()
        except Exception as e:
            logging.info(f"[Paperclip] Clint initial run skipped: {e}")
        try:
            from rivers.customer_advocate.workflow import sherry_run
            sherry_run()
        except Exception as e:
            logging.info(f"[Paperclip] Sherry initial run skipped: {e}")

        logging.info("[Paperclip] Initial enrollment pass complete — 5 rivers active")
    except Exception as e:
        logging.warning(f"[Paperclip] River init failed — rivers disabled: {e}")


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)

app = FastAPI(
    title="Paperclip Multi-Agent Revenue Engine",
    description=(
        "AI-native revenue platform powering The AI Phone Guy, Calling Digital, "
        "and Automotive Intelligence. Agents prospect, email, track pipeline, "
        "queue content, and execute retention — autonomously."
    ),
    version=SETTINGS.app_version,
    lifespan=lifespan,
)

# Global exception handler for unhandled errors
from fastapi.responses import JSONResponse
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logging.error(f"[GLOBAL EXCEPTION] {request.url}: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": f"Internal server error: {exc}"},
    )


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        set_request_id(request_id)
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


app.add_middleware(RequestContextMiddleware)

pitwall_assets_dir = Path(__file__).parent / "static" / "pitwall-react"
pitwall_assets_mount = pitwall_assets_dir / "assets"
if pitwall_assets_mount.exists():
    app.mount("/pitwall-static/assets", StaticFiles(directory=pitwall_assets_mount), name="pitwall-react-assets")
    app.mount("/assets", StaticFiles(directory=pitwall_assets_mount), name="pitwall-react-assets-root")


# ── Auth ─────────────────────────────────────────────────────────────────────

class AuthRequest(BaseModel):
    agent_id: str
    message: str


def validate_key(authorization: Optional[str] = Header(None)):
    if not API_KEYS:
        if SETTINGS.environment == "production":
            raise HTTPException(
                status_code=503,
                detail="Protected endpoints are disabled: API_KEYS is required in production.",
            )
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


def normalize_agent_id(agent_name: str) -> str:
    """Accept display names (e.g., 'Michael Meta') and normalize to agent ids."""
    normalized = agent_name.lower().strip()
    if normalized in AGENTS:
        return normalized
    normalized = normalized.replace(" ", "_")
    return normalized


# ── Routes ───────────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    react_index = Path(__file__).parent / "static" / "pitwall-react" / "index.html"
    if react_index.exists():
        return FileResponse(str(react_index), media_type="text/html")
    return {"status": "ok", "engine": "revenue", "version": SETTINGS.app_version}


@app.post("/chat")
async def chat(request: AuthRequest, authorization: Optional[str] = Header(None)):
    validate_key(authorization)
    agent_id = normalize_agent_id(request.agent_id)
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
        # Persist interactive output so it is part of historical logs.
        persist_log(agent_id, "chat", str(result))
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
    agent_name = normalize_agent_id(agent_name)
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
    agent_name = normalize_agent_id(agent_name)
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


@app.post("/content/publish/ghl")
async def publish_content_to_ghl(
    limit: int = 5,
    authorization: Optional[str] = Header(None),
):
    """Publish queued AI Phone Guy content to GHL site workflow with a branded graphic payload."""
    validate_key(authorization)
    if limit < 1:
        limit = 1
    if limit > 25:
        limit = 25

    if not ghl_site_publish_ready():
        raise HTTPException(
            status_code=503,
            detail="GHL site publishing is not configured. Set GHL_API_KEY and GHL_LOCATION_ID in Railway.",
        )

    queued_all = get_content_queue(business_key="aiphoneguy", status="queued", limit=100)
    social_platforms = {"linkedin", "twitter", "x", "instagram", "facebook", "tiktok", "youtube"}
    queued = [q for q in queued_all if (q.get("platform") or "").strip().lower() not in social_platforms][:limit]
    if not queued:
        return {
            "status": "ok",
            "published": 0,
            "failed": 0,
            "results": [],
            "message": "No queued aiphoneguy site content found.",
        }

    published = 0
    failed = 0
    results = []

    for item in queued:
        enriched_item = dict(item)
        enriched_item["business_key"] = "aiphoneguy"
        try:
            publish_result = publish_content_to_ghl_site(enriched_item)
            mark_content_published(item["id"])
            track_event(
                "content_published",
                business_key="aiphoneguy",
                agent_name=item.get("agent_name", "zoe"),
                metadata={
                    "content_id": item.get("id"),
                    "provider": "ghl",
                    "published_url": publish_result.get("url", ""),
                    "slug": publish_result.get("slug", ""),
                },
            )
            results.append(
                {
                    "content_id": item.get("id"),
                    "title": item.get("title", ""),
                    "status": "published",
                    "url": publish_result.get("url", ""),
                    "slug": publish_result.get("slug", ""),
                }
            )
            published += 1
        except Exception as e:
            failed += 1
            logging.warning("[Content] GHL publish failed for content_id=%s: %s", item.get("id"), e)
            track_event(
                "content_publish_failed",
                business_key="aiphoneguy",
                agent_name=item.get("agent_name", "zoe"),
                metadata={
                    "content_id": item.get("id"),
                    "provider": "ghl",
                    "error": str(e),
                },
            )
            results.append(
                {
                    "content_id": item.get("id"),
                    "title": item.get("title", ""),
                    "status": "failed",
                    "error": str(e),
                }
            )

    return {
        "status": "ok",
        "published": published,
        "failed": failed,
        "results": results,
    }


@app.post("/content/publish/ghl/social")
async def publish_content_to_ghl_social_endpoint(
    limit: int = 5,
    authorization: Optional[str] = Header(None),
):
    """Publish queued AI Phone Guy social content via GHL social channels."""
    validate_key(authorization)
    if limit < 1:
        limit = 1
    if limit > 25:
        limit = 25

    if not ghl_social_publish_ready():
        raise HTTPException(
            status_code=503,
            detail="GHL social publishing is not configured. Set GHL_API_KEY and GHL_LOCATION_ID in Railway.",
        )

    queued_all = get_content_queue(business_key="aiphoneguy", status="queued", limit=100)
    social_platforms = {"linkedin", "twitter", "x", "instagram", "facebook", "tiktok", "youtube"}
    queued = [q for q in queued_all if (q.get("platform") or "").strip().lower() in social_platforms][:limit]
    if not queued:
        return {
            "status": "ok",
            "published": 0,
            "failed": 0,
            "results": [],
            "message": "No queued aiphoneguy social content found.",
        }

    published = 0
    failed = 0
    results = []

    for item in queued:
        enriched_item = dict(item)
        enriched_item["business_key"] = "aiphoneguy"
        try:
            prep = prepare_social_piece_with_creative_director(
                piece=enriched_item,
                business_key="aiphoneguy",
            )
            creative_item = prep.get("piece", enriched_item)
            publish_result = publish_content_to_ghl_social(creative_item)
            mark_content_published(item["id"])
            track_event(
                "content_published_social",
                business_key="aiphoneguy",
                agent_name=item.get("agent_name", "zoe"),
                metadata={
                    "content_id": item.get("id"),
                    "provider": "ghl_social",
                    "platform": item.get("platform", ""),
                    "published_url": publish_result.get("url", ""),
                    "generated_media": prep.get("generated_media", False),
                    "media_url": prep.get("media_url", ""),
                },
            )
            results.append(
                {
                    "content_id": item.get("id"),
                    "title": item.get("title", ""),
                    "platform": item.get("platform", ""),
                    "status": "published",
                    "url": publish_result.get("url", ""),
                    "generated_media": prep.get("generated_media", False),
                    "media_url": prep.get("media_url", ""),
                    "creative_director": prep.get("creative_director", {}),
                }
            )
            published += 1
        except Exception as e:
            failed += 1
            logging.warning("[Content] GHL social publish failed for content_id=%s: %s", item.get("id"), e)
            track_event(
                "content_publish_failed_social",
                business_key="aiphoneguy",
                agent_name=item.get("agent_name", "zoe"),
                metadata={
                    "content_id": item.get("id"),
                    "provider": "ghl_social",
                    "platform": item.get("platform", ""),
                    "error": str(e),
                },
            )
            results.append(
                {
                    "content_id": item.get("id"),
                    "title": item.get("title", ""),
                    "platform": item.get("platform", ""),
                    "status": "failed",
                    "error": str(e),
                }
            )

    return {
        "status": "ok",
        "published": published,
        "failed": failed,
        "results": results,
    }


@app.post("/content/publish/zernio/{business_key}")
async def publish_content_to_zernio_endpoint(
    business_key: Optional[str] = None,
    limit: int = 5,
    authorization: Optional[str] = Header(None),
):
    """Publish queued social content via Zernio to 14+ platforms (Twitter/X, Instagram, Facebook, LinkedIn, TikTok, YouTube, etc.)"""
    logging.info(f"[Zernio] Endpoint called for business_key={business_key}, limit={limit}")
    validate_key(authorization)
    
    if not zernio_ready():
        raise HTTPException(
            status_code=503,
            detail="Zernio is not configured. Set ZERNIO_API_KEY environment variable.",
        )

    if limit < 1:
        limit = 1
    if limit > 25:
        limit = 25

    # Map business keys to Zernio profile names for route purposes
    business_key = (business_key or "aiphoneguy").strip().lower()
    
    # Get queued content for this business
    queued_all = get_content_queue(business_key=business_key, status="queued", limit=100)
    social_platforms = {
        "twitter", "x", "instagram", "facebook", "linkedin", "tiktok", "youtube",
        "pinterest", "reddit", "bluesky", "threads", "googlebusiness", "telegram", "snapchat"
    }
    queued = [q for q in queued_all if (q.get("platform") or "").strip().lower() in social_platforms][:limit]
    
    if not queued:
        return {
            "status": "ok",
            "published": 0,
            "failed": 0,
            "results": [],
            "message": f"No queued {business_key} social content found.",
        }

    published = 0
    failed = 0
    results = []

    # Get Zernio profiles and find the one matching this business
    # Known aliases handle cases where business_key doesn't substring-match the profile name
    # (e.g. "autointelligence" vs "Automotive Intelligence").
    PROFILE_ALIASES = {
        "autointelligence": ["automotiveintelligence", "autointelligence", "automotive intelligence"],
        "aiphoneguy": ["aiphoneguy", "theaiphoneguy", "ai phone guy"],
        "callingdigital": ["callingdigital", "calling digital"],
    }
    try:
        profiles = get_zernio_profiles()
        matching_profile = None
        business_key_norm = re.sub(r"[^a-z0-9]", "", business_key.lower())
        aliases = PROFILE_ALIASES.get(business_key, [business_key_norm])

        for p in profiles:
            profile_name_norm = re.sub(r"[^a-z0-9]", "", (p.get("name") or "").lower())
            if profile_name_norm == business_key_norm:
                matching_profile = p
                break
            if any(alias in profile_name_norm or profile_name_norm in alias for alias in aliases):
                matching_profile = p
                break

        if not matching_profile:
            for p in profiles:
                profile_name_norm = re.sub(r"[^a-z0-9]", "", (p.get("name") or "").lower())
                if business_key_norm and business_key_norm in profile_name_norm:
                    matching_profile = p
                    break

        if not matching_profile:
            forced_profile_id = os.getenv(f"{business_key.upper()}_ZERNIO_PROFILE_ID", "").strip()
            if forced_profile_id:
                for p in profiles:
                    if (p.get("_id") or "").strip() == forced_profile_id:
                        matching_profile = p
                        break
        
        if not matching_profile:
            available = [p.get("name", "") for p in profiles]
            raise HTTPException(
                status_code=503,
                detail=(
                    f"No Zernio profile matched business '{business_key}'. "
                    f"Available profiles: {available}. "
                    f"Set {business_key.upper()}_ZERNIO_PROFILE_ID to force profile mapping."
                ),
            )
        
        profile_id = matching_profile.get("_id")
        profile_name = matching_profile.get("name", "")
    except Exception as e:
        logging.error(f"[Zernio] Failed to get profiles: {e}")
        raise HTTPException(
            status_code=503,
            detail=f"Failed to retrieve Zernio profiles: {e}",
        )

    for item in queued:
        try:
            platform = (item.get("platform") or "").strip().lower()
            if not platform or platform not in social_platforms:
                logging.warning(f"[Zernio] Skipping content {item.get('id')} with unsupported platform '{platform}'")
                continue

            pipeline_result = run_zernio_social_pipeline(
                piece=item,
                business_key=business_key,
                profile_id=profile_id,
                publish_now=True,
                publisher=publish_content_piece_to_zernio,
            )
            raw_result = pipeline_result.get("post", {})
            result = raw_result.get("post") if isinstance(raw_result.get("post"), dict) else raw_result

            post_url = ""
            platform_entries = result.get("platforms") if isinstance(result, dict) else None
            if isinstance(platform_entries, list) and platform_entries:
                post_url = platform_entries[0].get("platformPostUrl", "")
            
            mark_content_published(item["id"])
            track_event(
                "content_published_social",
                business_key=business_key,
                agent_name=item.get("agent_name", "unknown"),
                metadata={
                    "content_id": item.get("id"),
                    "provider": "zernio",
                    "platform": platform,
                    "zernio_post_id": result.get("_id", ""),
                },
            )
            results.append({
                "content_id": item.get("id"),
                "title": item.get("title", ""),
                "platform": platform,
                "status": "published",
                "zernio_post_id": result.get("_id", ""),
                "post_status": result.get("status", "unknown"),
                "post_url": post_url,
                "media_url": pipeline_result.get("media_url"),
                "generated_media": pipeline_result.get("generated_media", False),
                "creative_director": pipeline_result.get("creative_director", {}),
            })
            published += 1
        except Exception as e:
            failed += 1
            logging.error(f"[Zernio] Publish failed for content_id={item.get('id')}: {e}")
            track_event(
                "content_publish_failed_social",
                business_key=business_key,
                agent_name=item.get("agent_name", "unknown"),
                metadata={
                    "content_id": item.get("id"),
                    "provider": "zernio",
                    "platform": item.get("platform", ""),
                    "error": str(e),
                },
            )
            results.append({
                "content_id": item.get("id"),
                "title": item.get("title", ""),
                "platform": item.get("platform", ""),
                "status": "failed",
                "error": str(e),
            })

    return {
        "status": "ok",
        "profile": {
            "id": profile_id,
            "name": profile_name,
        },
        "published": published,
        "failed": failed,
        "results": results,
    }


@app.post("/content/publish/ghl/all")
async def publish_content_to_ghl_all(
    limit_site: int = 5,
    limit_social: int = 5,
    authorization: Optional[str] = Header(None),
):
    """Publish AI Phone Guy site and social content in one run."""
    validate_key(authorization)
    site_result = await publish_content_to_ghl(limit=limit_site, authorization=authorization)
    social_result = await publish_content_to_ghl_social_endpoint(limit=limit_social, authorization=authorization)
    return {
        "status": "ok",
        "site": site_result,
        "social": social_result,
    }


@app.post("/content/publish/ghost/{business_key}")
async def publish_content_to_ghost_endpoint(
    business_key: str,
    limit: int = 5,
    authorization: Optional[str] = Header(None),
):
    """Publish queued blog/site content for a Ghost-backed business such as Calling Digital."""
    validate_key(authorization)
    business_key = (business_key or "").strip().lower()
    if not business_key:
        raise HTTPException(status_code=400, detail="business_key is required.")

    if limit < 1:
        limit = 1
    if limit > 25:
        limit = 25

    if not ghost_publish_ready(business_key):
        env_url = f"{business_key.upper()}_GHOST_API_URL"
        env_key = f"{business_key.upper()}_GHOST_ADMIN_API_KEY"
        raise HTTPException(
            status_code=503,
            detail=f"Ghost publishing is not configured for {business_key}. Set {env_url} and {env_key} in Railway.",
        )

    queued_all = get_content_queue(business_key=business_key, status="queued", limit=100)
    social_platforms = {"linkedin", "twitter", "x", "instagram", "facebook", "tiktok", "youtube"}
    queued = [q for q in queued_all if (q.get("platform") or "").strip().lower() not in social_platforms][:limit]
    if not queued:
        return {
            "status": "ok",
            "published": 0,
            "failed": 0,
            "results": [],
            "message": f"No queued {business_key} Ghost content found.",
        }

    published = 0
    failed = 0
    results = []

    for item in queued:
        enriched_item = dict(item)
        enriched_item["business_key"] = business_key
        enriched_item = _normalize_content_pieces([enriched_item], business_key)[0]
        try:
            publish_result = publish_content_to_ghost(enriched_item)
            mark_content_published(item["id"])
            track_event(
                "content_published",
                business_key=business_key,
                agent_name=item.get("agent_name", "content"),
                metadata={
                    "content_id": item.get("id"),
                    "provider": "ghost",
                    "published_url": publish_result.get("url", ""),
                    "slug": publish_result.get("slug", ""),
                },
            )
            results.append(
                {
                    "content_id": item.get("id"),
                    "title": item.get("title", ""),
                    "status": "published",
                    "url": publish_result.get("url", ""),
                    "slug": publish_result.get("slug", ""),
                }
            )
            published += 1
        except Exception as e:
            failed += 1
            logging.warning("[Content] Ghost publish failed for business=%s content_id=%s: %s", business_key, item.get("id"), e)
            track_event(
                "content_publish_failed",
                business_key=business_key,
                agent_name=item.get("agent_name", "content"),
                metadata={
                    "content_id": item.get("id"),
                    "provider": "ghost",
                    "error": str(e),
                },
            )
            results.append(
                {
                    "content_id": item.get("id"),
                    "title": item.get("title", ""),
                    "status": "failed",
                    "error": str(e),
                }
            )

    return {
        "status": "ok",
        "published": published,
        "failed": failed,
        "results": results,
    }


@app.get("/api/changelogs")
async def api_changelogs(week: Optional[int] = None, year: Optional[int] = None):
    """JSON feed for the dashboard changelog section. Returns week list + selected week data."""
    from paperclip.changelog_view import feed
    return JSONResponse(content=feed(week, year))


@app.post("/admin/regenerate-changelog")
async def admin_regenerate_changelog(week: Optional[int] = None, year: Optional[int] = None):
    """Regenerate a changelog for a specific ISO week (or current week if omitted)."""
    from paperclip.changelog_gen import run_changelog
    try:
        filepath = run_changelog(week=week, year=year)
        return {"status": "ok", "filepath": filepath, "week": week, "year": year}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Changelog generation failed: {e}")


@app.post("/admin/backfill-changelogs-to-db")
async def admin_backfill_changelogs_to_db():
    """One-shot: copy committed filesystem changelogs into Postgres (idempotent)."""
    from paperclip.changelog_view import backfill_fs_to_db
    return backfill_fs_to_db()


@app.post("/admin/purge-changelogs-db")
async def admin_purge_changelogs_db():
    """Delete all changelog rows from Postgres. Use before regenerate-all to get a clean rebuild."""
    from paperclip.changelog_view import purge_db
    return purge_db()


@app.post("/admin/regenerate-all-changelogs")
async def admin_regenerate_all_changelogs(start_week: int = 10, end_week: int = 16, year: int = 2026):
    """Regenerate every weekly changelog in [start_week, end_week] — used to pick up
    new template sections (e.g. LLM talking points) across historical weeks."""
    from paperclip.changelog_gen import run_changelog
    results = []
    for w in range(start_week, end_week + 1):
        try:
            fp = run_changelog(week=w, year=year)
            results.append({"week": w, "year": year, "status": "ok", "filepath": fp})
        except Exception as e:
            results.append({"week": w, "year": year, "status": "error", "error": str(e)})
    return {"results": results}


@app.get("/pipeline")
async def pipeline_overview():
    """Quick pipeline overview — how many prospects, emails, opportunities across all businesses."""
    summary = {}
    for biz_key in BUSINESSES:
        summary[biz_key] = get_revenue_summary(business_key=biz_key, days=30)
    return JSONResponse(content=summary)


@app.get("/api/sales/preflight")
async def sales_preflight(authorization: Optional[str] = Header(None)):
    """Return sales execution readiness by provider and sales agent routing."""
    validate_key(authorization)
    return _sales_preflight_report()


@app.get("/api/sales/pipeline")
async def sales_pipeline(
    days: int = 30,
    authorization: Optional[str] = Header(None),
):
    """
    Return pipeline health snapshot: prospects created, emails sent, and
    stage breakdown per business, aggregated from the revenue_events table.
    """
    validate_key(authorization)

    try:
        from tools.revenue_tracker import get_revenue_summary, get_daily_metrics

        businesses = {
            "aiphoneguy": {"name": "The AI Phone Guy", "agents": ["tyler"], "deal_value": 482},
            "callingdigital": {"name": "Calling Digital", "agents": ["marcus"], "deal_value": 2500},
            "autointelligence": {"name": "Automotive Intelligence", "agents": ["ryan_data"], "deal_value": 2500},
        }

        pipeline = {}
        for biz_key, biz_info in businesses.items():
            summary = get_revenue_summary(business_key=biz_key, days=days)
            daily = get_daily_metrics(business_key=biz_key, days=7)

            prospects_created = summary.get("prospect_created", 0)
            emails_sent = summary.get("email_sent", 0)
            demos_booked = summary.get("demo_booked", 0)
            deals_closed = summary.get("deal_closed", 0)
            pipeline_value = prospects_created * biz_info["deal_value"]
            closed_value = deals_closed * biz_info["deal_value"]

            contact_rate = round((emails_sent / prospects_created * 100), 1) if prospects_created else 0
            demo_rate = round((demos_booked / emails_sent * 100), 1) if emails_sent else 0
            close_rate = round((deals_closed / demos_booked * 100), 1) if demos_booked else 0

            pipeline[biz_key] = {
                "business": biz_info["name"],
                "period_days": days,
                "stages": {
                    "prospected": prospects_created,
                    "emailed": emails_sent,
                    "demo_booked": demos_booked,
                    "deals_closed": deals_closed,
                },
                "pipeline_value_usd": pipeline_value,
                "closed_value_usd": closed_value,
                "conversion_rates": {
                    "prospect_to_email_pct": contact_rate,
                    "email_to_demo_pct": demo_rate,
                    "demo_to_close_pct": close_rate,
                },
                "daily_last_7d": daily,
            }

        return JSONResponse(content={"status": "ok", "pipeline": pipeline})

    except Exception as e:
        logging.error(f"[Pipeline] Status endpoint error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/sales/email-templates/report")
async def sales_email_templates_report(
    days: int = 7,
    business_key: str = "",
    authorization: Optional[str] = Header(None),
):
    """Daily template usage and validation quality summary from tracked revenue events."""
    validate_key(authorization)
    result = get_email_template_report(
        business_key=business_key.strip() or None,
        days=max(1, min(days, 90)),
    )
    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])
    return JSONResponse(content=result)


@app.post("/admin/run-coo")
async def run_coo_now(authorization: Optional[str] = Header(None)):
    """Trigger COO Command ops report immediately."""
    validate_key(authorization)
    result = await asyncio.to_thread(run_coo_command)
    return JSONResponse(content=result)


@app.post("/admin/run-now")
async def run_now(
    scope: str = "sales",
    debug: bool = False,
    marcus_vertical: Optional[str] = None,
    authorization: Optional[str] = Header(None),
):
    """Trigger scheduled jobs immediately (authenticated).

    marcus_vertical: optional override (med-spa, pi-law, real-estate, home-builder)
    that forces Marcus onto a specific vertical for this run, bypassing his weekday
    rotation. Useful for on-demand multi-vertical sweeps in the same session.
    Only applies when Marcus is in the scope.
    """
    validate_key(authorization)

    scope = scope.lower().strip()
    jobs = RUN_NOW_SCOPES.get(scope)
    if not jobs:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid scope '{scope}'. Valid scopes: {', '.join(sorted(RUN_NOW_SCOPES.keys()))}",
        )

    if marcus_vertical:
        marcus_vertical = marcus_vertical.lower().strip()
        if marcus_vertical not in {"med-spa", "pi-law", "real-estate", "home-builder"}:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid marcus_vertical '{marcus_vertical}'. Valid: med-spa, pi-law, real-estate, home-builder",
            )

    started_at = datetime.datetime.now(CST).isoformat()

    async def _run_job(job_name, fn):
        try:
            if job_name == "marcus_prospecting" and marcus_vertical:
                fn_result = await asyncio.to_thread(fn, marcus_vertical)
            else:
                fn_result = await asyncio.to_thread(fn)
            row = {"job": job_name, "status": "ok"}
            if debug and isinstance(fn_result, dict):
                row["debug"] = fn_result
            return row
        except Exception as e:
            logging.error(f"[RunNow] {job_name} failed: {type(e).__name__}: {e}")
            return {"job": job_name, "status": "error", "error": f"{type(e).__name__}: {e}"}

    results = list(await asyncio.gather(*[_run_job(name, fn) for name, fn in jobs]))

    return JSONResponse(
        content={
            "status": "completed",
            "scope": scope,
            "started_at": started_at,
            "finished_at": datetime.datetime.now(CST).isoformat(),
            "total": len(results),
            "ok": len([r for r in results if r["status"] == "ok"]),
            "errors": len([r for r in results if r["status"] == "error"]),
            "results": results,
        }
    )


@app.post("/admin/test-creative-pipeline")
async def test_creative_pipeline(
    publish: bool = False,
    authorization: Optional[str] = Header(None),
):
    """
    Test the AI creative pipeline (image gen, video gen, carousel) for ALL businesses.

    Generates a branded test post per business, runs it through the full social pipeline
    (AI image generation via Replicate FLUX), and optionally publishes to each business's
    connected Zernio platforms.

    Query params:
        publish (bool): If True, actually publish test posts to Zernio. Default False (dry-run).

    Returns per-business results with:
        - Agent used (content lead per business)
        - Zernio profile matched
        - Connected platforms discovered
        - AI image generation result
        - Publish result per platform (if publish=True)
    """
    validate_key(authorization)

    # ── Agent-to-business mapping for content leads ──
    CONTENT_LEADS = {
        "aiphoneguy": {
            "agent": "zoe",
            "agent_name": "Zoe",
            "role": "Head of Marketing",
            "brand": "The AI Phone Guy",
            "test_content": (
                "AI-powered phone systems are transforming how local businesses handle calls. "
                "Never miss another lead — your AI receptionist works 24/7. "
                "Book a demo today and see the difference."
            ),
            "test_headline": "Never Miss a Call That Should Convert",
            "platforms_to_test": ["instagram", "facebook", "linkedin", "twitter"],
        },
        "autointelligence": {
            "agent": "chase",
            "agent_name": "Chase",
            "role": "Head of Marketing",
            "brand": "Automotive Intelligence",
            "test_content": (
                "Dealerships using AI are booking 3x more service appointments. "
                "Our AI platform analyzes your customer data and automates follow-up "
                "so your team can focus on closing. See the data."
            ),
            "test_headline": "AI That Drives Appointments to Your Dealership",
            "platforms_to_test": ["linkedin", "facebook", "instagram", "twitter"],
        },
        "callingdigital": {
            "agent": "sofia",
            "agent_name": "Sofia",
            "role": "Head of Content & Creative",
            "brand": "Calling Digital",
            "test_content": (
                "Small businesses in Dallas are growing 2x faster with the right digital strategy. "
                "SEO, ads, AI automation — Calling Digital builds growth systems that actually work. "
                "Let's talk about your goals."
            ),
            "test_headline": "Growth Systems for Serious Owners",
            "platforms_to_test": ["instagram", "linkedin", "facebook", "twitter"],
        },
    }

    all_results = {}
    overall_ok = 0
    overall_errors = 0

    # Check AI readiness.
    ai_image_available = False
    ai_video_available = False
    try:
        from tools.image_gen import image_gen_ready
        ai_image_available = image_gen_ready()
    except ImportError:
        pass
    try:
        from tools.video_gen import video_gen_ready
        ai_video_available = video_gen_ready()
    except ImportError:
        pass

    # Get all Zernio profiles once.
    zernio_profiles = []
    if zernio_ready():
        try:
            zernio_profiles = get_zernio_profiles()
        except Exception as e:
            logging.error(f"[TestCreative] Failed to get Zernio profiles: {e}")

    for business_key, lead in CONTENT_LEADS.items():
        biz_result = {
            "business": business_key,
            "brand": lead["brand"],
            "content_agent": f"{lead['agent_name']} ({lead['role']})",
            "ai_image_available": ai_image_available,
            "ai_video_available": ai_video_available,
            "zernio_profile": None,
            "connected_platforms": [],
            "image_generation": None,
            "platform_results": [],
        }

        # ── Step 1: Match Zernio profile ──
        # Known aliases: business_key → possible Zernio profile name fragments.
        PROFILE_ALIASES = {
            "autointelligence": ["automotiveintelligence", "autointelligence", "automotive intelligence"],
            "aiphoneguy": ["aiphoneguy", "theaiphoneguy", "ai phone guy"],
            "callingdigital": ["callingdigital", "calling digital"],
        }
        matching_profile = None
        biz_norm = re.sub(r"[^a-z0-9]", "", business_key.lower())
        aliases = PROFILE_ALIASES.get(business_key, [biz_norm])

        for p in zernio_profiles:
            profile_norm = re.sub(r"[^a-z0-9]", "", (p.get("name") or "").lower())
            if any(alias in profile_norm or profile_norm in alias for alias in aliases):
                matching_profile = p
                break
            if biz_norm in profile_norm or profile_norm in biz_norm:
                matching_profile = p
                break

        if not matching_profile:
            forced_id = os.getenv(f"{business_key.upper()}_ZERNIO_PROFILE_ID", "").strip()
            if forced_id:
                for p in zernio_profiles:
                    if (p.get("_id") or "") == forced_id:
                        matching_profile = p
                        break

        if matching_profile:
            profile_id = matching_profile.get("_id")
            biz_result["zernio_profile"] = {
                "id": profile_id,
                "name": matching_profile.get("name", ""),
            }

            # ── Step 2: Discover connected platforms ──
            try:
                accounts = list_zernio_accounts(profile_id)
                connected = []
                for acct in accounts:
                    connected.append({
                        "platform": acct.get("platform", "unknown"),
                        "username": acct.get("username", acct.get("name", "unknown")),
                        "account_id": acct.get("_id", ""),
                    })
                biz_result["connected_platforms"] = connected
            except Exception as e:
                biz_result["connected_platforms"] = [{"error": str(e)}]
                accounts = []
        else:
            biz_result["zernio_profile"] = {"error": f"No profile matched. Set {business_key.upper()}_ZERNIO_PROFILE_ID."}
            profile_id = None
            accounts = []

        # ── Step 3: Run creative pipeline (AI image gen) ──
        test_piece = {
            "id": f"test-{business_key}-creative",
            "content": lead["test_content"],
            "platform": "instagram",
            "cta": f"Visit {lead['brand']}",
        }

        try:
            prep = prepare_social_piece_with_creative_director(
                piece=test_piece,
                business_key=business_key,
            )
            biz_result["image_generation"] = {
                "status": "ok",
                "media_url": prep.get("media_url"),
                "media_type": prep.get("media_type", "unknown"),
                "generated_media": prep.get("generated_media", False),
                "creative_director": prep.get("creative_director", {}),
            }
        except Exception as e:
            biz_result["image_generation"] = {
                "status": "error",
                "error": str(e),
            }
            overall_errors += 1
            all_results[business_key] = biz_result
            continue

        # ── Step 4: Publish test post to each connected platform (if publish=True) ──
        if publish and profile_id and accounts:
            media_url = prep.get("media_url")
            for platform_name in lead["platforms_to_test"]:
                # Find the account for this platform.
                target_account = None
                for acct in accounts:
                    if acct.get("platform") == platform_name:
                        target_account = acct
                        break

                if not target_account:
                    biz_result["platform_results"].append({
                        "platform": platform_name,
                        "status": "skipped",
                        "reason": f"No {platform_name} account connected for {lead['brand']}",
                    })
                    continue

                try:
                    platform_piece = dict(prep.get("piece", test_piece))
                    platform_piece["platform"] = platform_name
                    platform_piece["media_url"] = media_url

                    result = publish_content_piece_to_zernio(
                        piece=platform_piece,
                        profile_id=profile_id,
                        publish_now=True,
                    )

                    post_id = result.get("_id", "")
                    post_status = result.get("status", "unknown")
                    post_url = ""
                    platforms_resp = result.get("platforms")
                    if isinstance(platforms_resp, list) and platforms_resp:
                        post_url = platforms_resp[0].get("platformPostUrl", "")

                    biz_result["platform_results"].append({
                        "platform": platform_name,
                        "account": target_account.get("username", ""),
                        "status": "published",
                        "post_id": post_id,
                        "post_status": post_status,
                        "post_url": post_url,
                        "media_url": media_url,
                    })
                    overall_ok += 1
                except Exception as e:
                    biz_result["platform_results"].append({
                        "platform": platform_name,
                        "status": "error",
                        "error": str(e),
                    })
                    overall_errors += 1
        elif not publish:
            # Dry run — just report what would happen.
            for platform_name in lead["platforms_to_test"]:
                has_account = any(
                    acct.get("platform") == platform_name for acct in accounts
                )
                biz_result["platform_results"].append({
                    "platform": platform_name,
                    "status": "dry_run",
                    "account_connected": has_account,
                    "would_publish": has_account,
                    "media_url": prep.get("media_url"),
                })

        all_results[business_key] = biz_result

    return JSONResponse(content={
        "status": "completed",
        "test_mode": "publish" if publish else "dry_run",
        "timestamp": datetime.datetime.now(CST).isoformat(),
        "ai_capabilities": {
            "image_gen_replicate": ai_image_available,
            "video_gen_replicate": ai_video_available,
            "pil_fallback": True,
        },
        "businesses_tested": len(all_results),
        "total_ok": overall_ok,
        "total_errors": overall_errors,
        "results": all_results,
    })


@app.get("/admin/test-ghl")
async def test_ghl_social(authorization: Optional[str] = Header(None)):
    """Diagnose GHL social publishing — check credentials, accounts, and test post."""
    validate_key(authorization)
    import traceback
    from tools.ghl import _get_ghl_social_accounts, _GHL_PLATFORM_MAP

    results = {
        "ghl_api_key_set": bool(os.getenv("GHL_API_KEY", "").strip()),
        "ghl_location_id_set": bool(os.getenv("GHL_LOCATION_ID", "").strip()),
        "ghl_social_ready": ghl_social_publish_ready(),
        "connected_accounts": None,
        "test_post": None,
    }

    if not results["ghl_social_ready"]:
        return results

    location_id = os.getenv("GHL_LOCATION_ID", "").strip()

    results["ghl_user_id_set"] = bool(os.getenv("GHL_USER_ID", "").strip())

    # Step 0: Try to get GHL users for this location
    import requests as req
    ghl_key = os.getenv("GHL_API_KEY", "").strip()
    try:
        resp = req.get(
            f"https://services.leadconnectorhq.com/users/search?locationId={location_id}&limit=5",
            headers={"Authorization": f"Bearer {ghl_key}", "Version": "2021-07-28"},
            timeout=10,
        )
        if resp.status_code == 200:
            users_data = resp.json()
            results["ghl_users"] = [
                {"id": u.get("id"), "name": u.get("name"), "email": u.get("email")}
                for u in users_data.get("users", [])[:5]
            ]
        else:
            results["ghl_users"] = {"status": resp.status_code, "body": resp.text[:300]}
    except Exception as e:
        results["ghl_users"] = {"error": str(e)}

    # Step 1: Get connected social accounts
    try:
        accounts = _get_ghl_social_accounts(location_id)
        results["connected_accounts"] = [
            {
                "id": a.get("id"),
                "type": a.get("type"),
                "name": a.get("name", a.get("displayName", "")),
                "platform": a.get("platform", a.get("type", "")),
            }
            for a in accounts
        ]
    except Exception as e:
        results["connected_accounts"] = {"error": str(e)}

        # Try alternate GHL social API paths for debugging
        import requests as req
        ghl_key = os.getenv("GHL_API_KEY", "").strip()
        alt_paths = [
            f"/social-media-posting/oauth/{location_id}/accounts",
            f"/social-media-posting/oauth/{location_id}/socialmedia/accounts",
            f"/social-media-posting/{location_id}/accounts",
        ]
        results["api_path_probe"] = {}
        for path in alt_paths:
            try:
                resp = req.get(
                    f"https://services.leadconnectorhq.com{path}",
                    headers={"Authorization": f"Bearer {ghl_key}", "Version": "2021-07-28"},
                    timeout=10,
                )
                results["api_path_probe"][path] = {
                    "status": resp.status_code,
                    "body": resp.json() if resp.status_code == 200 else resp.text[:300],
                }
            except Exception as pe:
                results["api_path_probe"][path] = {"error": str(pe)}
        return results

    # Step 2: Test post with AI image
    if accounts:
        try:
            test_piece = {
                "id": "test-ghl-aiphoneguy",
                "title": "AI Phone Guy Systems Check",
                "body": "AI-powered phone systems are changing how local businesses handle calls. Never miss a lead again.",
                "content": "AI-powered phone systems are changing how local businesses handle calls. Never miss a lead again.",
                "platform": "facebook",
                "cta": "Book a Demo at theaiphoneguy.ai",
                "hashtags": "#AIPhoneGuy #SmallBusiness #AI",
            }

            prep = prepare_social_piece_with_creative_director(
                piece=test_piece,
                business_key="aiphoneguy",
            )

            results["image_generation"] = {
                "media_type": prep.get("media_type", "unknown"),
                "media_url": prep.get("media_url"),
                "generated_media": prep.get("generated_media", False),
            }

            creative_piece = prep.get("piece", test_piece)
            media_url = prep.get("media_url", "")

            # Try raw GHL API call with different media formats to find what works
            import requests as req
            ghl_key = os.getenv("GHL_API_KEY", "").strip()
            user_id = os.getenv("GHL_USER_ID", "").strip()
            fb_account_id = "69836b2494123f7e79dac361_ZoxVB4ibMZZ2lZ5QpXep_1075138305672977_page"

            media_formats_to_try = {
                "format_1_url_string_array": [media_url] if media_url else [],
                "format_2_object_url_only": [{"url": media_url}] if media_url else [],
                "format_3_object_url_type": [{"url": media_url, "type": "image"}] if media_url else [],
                "format_4_object_full": [{"url": media_url, "type": "image", "name": "post.png"}] if media_url else [],
                "format_5_empty": [],
            }

            results["media_format_tests"] = {}
            for fmt_name, fmt_value in media_formats_to_try.items():
                test_payload = {
                    "accountIds": [fb_account_id],
                    "userId": user_id,
                    "summary": f"GHL media format test ({fmt_name}) — systems check.",
                    "status": "draft",
                    "type": "post",
                    "media": fmt_value,
                }
                try:
                    resp = req.post(
                        f"https://services.leadconnectorhq.com/social-media-posting/{location_id}/posts",
                        json=test_payload,
                        headers={"Authorization": f"Bearer {ghl_key}", "Version": "2021-07-28", "Content-Type": "application/json"},
                        timeout=15,
                    )
                    results["media_format_tests"][fmt_name] = {
                        "status_code": resp.status_code,
                        "body": resp.text[:300],
                    }
                    # If it worked, stop testing
                    if resp.status_code in (200, 201):
                        results["media_format_tests"][fmt_name]["winner"] = True
                        break
                except Exception as e:
                    results["media_format_tests"][fmt_name] = {"error": str(e)}

            results["test_post"] = {
                "status": "media_format_test",
                "result": "See media_format_tests for results",
            }
        except Exception as e:
            results["test_post"] = {
                "status": "error",
                "error": str(e),
                "type": type(e).__name__,
                "traceback": traceback.format_exc()[-1000:],
            }

    return results


@app.get("/admin/test-replicate")
async def test_replicate_directly(authorization: Optional[str] = Header(None)):
    """Diagnose Replicate API — test auth, connectivity, and image generation."""
    validate_key(authorization)
    import traceback

    results = {
        "env_var_set": False,
        "token_prefix": "",
        "auth_test": None,
        "image_generation": None,
    }

    # Step 1: Check env var
    token = os.getenv("REPLICATE_API_TOKEN", "").strip()
    results["env_var_set"] = bool(token)
    results["token_prefix"] = token[:8] + "..." if len(token) > 8 else "(empty)"

    if not token:
        return results

    # Step 2: Test auth — hit the account endpoint
    import requests as req
    try:
        resp = req.get(
            "https://api.replicate.com/v1/account",
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        )
        results["auth_test"] = {
            "status_code": resp.status_code,
            "body": resp.json() if resp.status_code == 200 else resp.text[:500],
        }
    except Exception as e:
        results["auth_test"] = {"error": str(e), "traceback": traceback.format_exc()[-500:]}

    # Step 3: Try actual image generation
    try:
        from tools.image_gen import generate_image
        gen_result = generate_image(
            prompt="A simple blue gradient background, minimal, clean",
            business_key="callingdigital",
            platform="instagram",
            num_outputs=1,
        )
        results["image_generation"] = {
            "status": "ok",
            "urls": gen_result.get("urls", []),
            "model": gen_result.get("model", ""),
            "prompt_used": gen_result.get("prompt_used", ""),
        }
    except Exception as e:
        results["image_generation"] = {
            "status": "error",
            "error": str(e),
            "type": type(e).__name__,
            "traceback": traceback.format_exc()[-1000:],
        }

    return results


@app.post("/admin/test-zernio-post")
async def test_zernio_single_post(
    business_key: str = "callingdigital",
    platform: str = "facebook",
    authorization: Optional[str] = Header(None),
):
    """
    Publish a single test post to one platform and return the RAW Zernio response.
    Useful for diagnosing why specific platforms fail silently.
    """
    validate_key(authorization)
    import traceback

    if not zernio_ready():
        raise HTTPException(status_code=503, detail="Zernio not configured.")

    # Find profile
    PROFILE_ALIASES = {
        "autointelligence": ["automotiveintelligence", "autointelligence"],
        "aiphoneguy": ["aiphoneguy", "theaiphoneguy"],
        "callingdigital": ["callingdigital"],
    }
    profiles = get_zernio_profiles()
    aliases = PROFILE_ALIASES.get(business_key, [business_key])
    matching_profile = None
    for p in profiles:
        pnorm = re.sub(r"[^a-z0-9]", "", (p.get("name") or "").lower())
        if any(a in pnorm or pnorm in a for a in aliases):
            matching_profile = p
            break

    if not matching_profile:
        return {"error": f"No profile for {business_key}", "profiles": [p.get("name") for p in profiles]}

    profile_id = matching_profile["_id"]

    # Find account for this platform
    accounts = list_zernio_accounts(profile_id)
    target = [a for a in accounts if a.get("platform") == platform]
    if not target:
        return {
            "error": f"No {platform} account connected",
            "connected": [{"platform": a.get("platform"), "username": a.get("username")} for a in accounts],
        }

    account = target[0]

    # Publish a simple test post
    test_content = f"Test post from Paperclip AI Ops — {business_key} on {platform}. This is a systems check."
    try:
        raw_result = publish_to_zernio(
            content=test_content,
            platforms=[platform],
            account_ids=[account["_id"]],
            publish_now=True,
        )
        return {
            "status": "ok",
            "business": business_key,
            "platform": platform,
            "account": {
                "id": account.get("_id"),
                "username": account.get("username", account.get("name")),
            },
            "raw_zernio_response": raw_result,
        }
    except Exception as e:
        return {
            "status": "error",
            "business": business_key,
            "platform": platform,
            "account": {
                "id": account.get("_id"),
                "username": account.get("username", account.get("name")),
            },
            "error": str(e),
            "type": type(e).__name__,
            "traceback": traceback.format_exc()[-1000:],
        }


@app.get("/admin/zernio-profiles")
async def list_all_zernio_profiles(authorization: Optional[str] = Header(None)):
    """List all Zernio profiles and their connected accounts for debugging."""
    validate_key(authorization)
    if not zernio_ready():
        raise HTTPException(status_code=503, detail="Zernio not configured.")
    profiles = get_zernio_profiles()
    result = []
    for p in profiles:
        pid = p.get("_id", "")
        name = p.get("name", "")
        try:
            accounts = list_zernio_accounts(pid)
        except Exception:
            accounts = []
        result.append({
            "profile_id": pid,
            "name": name,
            "accounts": [
                {"platform": a.get("platform"), "username": a.get("username", a.get("name", ""))}
                for a in accounts
            ],
        })
    return {"profiles": result}


@app.post("/admin/migrate_agentlogs_to_contentqueue")
async def migrate_agentlogs_to_contentqueue(authorization: Optional[str] = Header(None)):
    """Migrate all AI Phone Guy agent_logs to content_queue with status='review' for manual vetting."""
    validate_key(authorization)
    AGENT_NAME = "aiphoneguy"
    migrated = 0
    if not DATABASE_URL:
        raise HTTPException(status_code=503, detail="Postgres not configured.")
    try:
        with _db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT content, created_at FROM agent_logs WHERE agent_name = %s ORDER BY created_at DESC",
                    (AGENT_NAME,),
                )
                rows = cur.fetchall()
                for row in rows:
                    content, created_at = row
                    cur.execute(
                        "INSERT INTO content_queue (business_key, agent_name, platform, content_type, title, body, hashtags, cta, funnel_stage, status, created_at) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'review', %s)",
                        (
                            AGENT_NAME,
                            "zoe",
                            "facebook",
                            "post",
                            "AI Phone Guy Dashboard Migration",
                            content[:100],
                            "#AI #PhoneGuy #Migration",
                            "Call now for your AI phone demo!",
                            "awareness",
                            created_at,
                        ),
                    )
                    migrated += 1
            conn.commit()
        return {"status": "ok", "migrated": migrated}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Migration failed: {e}")


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
            "next_run": _job_next_run_str(job),
            "trigger": str(job.trigger),
        })
    
    return {
        "total_jobs": len(jobs_info),
        "scheduler_running": scheduler.running,
        "jobs": sorted(jobs_info, key=lambda x: x["next_run"])
    }


@app.get("/api/logs")
async def get_recent_logs(agent: Optional[str] = None, limit: int = 50):
    """Return recent log entries, backed by Postgres history (Railway-safe)."""
    if DATABASE_URL:
        try:
            with _db() as conn:
                with conn.cursor() as cur:
                    if agent:
                        agent_id = normalize_agent_id(agent)
                        cur.execute(
                            "SELECT agent_name, log_type, run_date, created_at, content "
                            "FROM agent_logs WHERE agent_name = %s "
                            "ORDER BY created_at DESC LIMIT %s",
                            (agent_id, limit),
                        )
                    else:
                        cur.execute(
                            "SELECT agent_name, log_type, run_date, created_at, content "
                            "FROM agent_logs ORDER BY created_at DESC LIMIT %s",
                            (limit,),
                        )
                    rows = cur.fetchall()

            entries = []
            for r in rows:
                agent_name, log_type, run_date, created_at, content = r
                preview_line = (content or "").strip().splitlines()[0] if content else ""
                entries.append({
                    "file": f"{agent_name}_{log_type}_{run_date}.log",
                    "line": preview_line[:220],
                    "timestamp": str(created_at),
                })

            return {
                "total_entries": len(entries),
                "entries": entries,
            }
        except Exception as e:
            logging.error(f"[DB] /api/logs failed, falling back to files: {e}")

    # Filesystem fallback for local/dev only.
    log_files = glob.glob("logs/*.log")
    entries = []
    for log_file in sorted(log_files, reverse=True)[:10]:
        try:
            with open(log_file, "r") as f:
                lines = f.readlines()
            for line in lines[-limit:]:
                if agent is None or agent.lower() in line.lower():
                    entries.append(
                        {
                            "file": os.path.basename(log_file),
                            "line": line.strip(),
                            "timestamp": log_file,
                        }
                    )
        except Exception as e:
            logging.warning(f"Could not read log file {log_file}: {e}")
    return {"total_entries": len(entries), "entries": entries[-limit:]}


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
            "next_run": _job_next_run_str(job),
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
        "timestamp": datetime.datetime.now(pytz.timezone(SETTINGS.timezone)).isoformat(),
        "system_status": "operational",
        "scheduler_running": scheduler.running,
        "jobs_registered": len(jobs_info),
        "agents_total": 14,
        "test_pass_rate": test_pass_rate,
        "uptime": "running",
        "database": "Postgres" if SETTINGS.postgres_enabled else "Filesystem",
        "environment": SETTINGS.environment,
        "strict_startup": SETTINGS.strict_startup,
        "ghl_ready": SETTINGS.ghl_ready,
        "hubspot_ready": SETTINGS.hubspot_ready,
        "attio_ready": SETTINGS.attio_ready,
        "business_crm_map": SETTINGS.business_crm_map,
        "llm_model": SETTINGS.llm_model,
        "llm_ready": SETTINGS.llm_ready,
    }


@app.get("/api/quality/now")
async def get_quality_now(
    hours: int = 24,
    persist: bool = False,
    authorization: Optional[str] = Header(None),
):
    """Compute a current quality snapshot; optional persist for historical tracking."""
    if persist:
        validate_key(authorization)

    snapshot = _compute_quality_snapshot(window_hours=hours)
    if snapshot.get("status") == "no_database":
        raise HTTPException(status_code=503, detail=snapshot.get("message", "Database unavailable."))
    if snapshot.get("status") == "error":
        raise HTTPException(status_code=500, detail=snapshot.get("message", "Quality compute failed."))

    snapshot_id = None
    if persist:
        try:
            snapshot_id = _persist_quality_snapshot(snapshot)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Persist failed: {type(e).__name__}: {e}")

    return {
        "snapshot": snapshot,
        "persisted": bool(snapshot_id),
        "snapshot_id": snapshot_id,
    }


@app.get("/api/quality/history")
async def get_quality_history(limit: int = 30, authorization: Optional[str] = Header(None)):
    """Return historical quality snapshots for trend tracking and weekly reviews."""
    validate_key(authorization)
    if not DATABASE_URL:
        raise HTTPException(status_code=503, detail="DATABASE_URL not configured.")

    if limit < 1:
        limit = 1
    if limit > 200:
        limit = 200

    try:
        rows = fetch_all(
            """
            SELECT
                snapshot_id, window_hours, total_runs, active_agents, availability_ratio,
                short_outputs, error_like_outputs, delivered_artifacts, failed_artifacts,
                delivery_ratio, score, details, created_at
            FROM quality_snapshots
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (limit,),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"DB error: {type(e).__name__}: {e}")

    snapshots = []
    for row in rows:
        (
            snapshot_id,
            window_hours,
            total_runs,
            active_agents,
            availability_ratio,
            short_outputs,
            error_like_outputs,
            delivered_artifacts,
            failed_artifacts,
            delivery_ratio,
            score,
            details,
            created_at,
        ) = row

        parsed_details: Dict[str, Any] = {}
        try:
            parsed_details = json.loads(details or "{}")
        except Exception:
            parsed_details = {}

        snapshots.append(
            {
                "snapshot_id": snapshot_id,
                "created_at": str(created_at),
                "window_hours": int(window_hours),
                "score": float(score),
                "total_runs": int(total_runs),
                "active_agents": int(active_agents),
                "availability_ratio": float(availability_ratio),
                "short_outputs": int(short_outputs),
                "error_like_outputs": int(error_like_outputs),
                "delivered_artifacts": int(delivered_artifacts),
                "failed_artifacts": int(failed_artifacts),
                "delivery_ratio": float(delivery_ratio),
                "details": parsed_details,
            }
        )

    return {
        "count": len(snapshots),
        "snapshots": snapshots,
    }


@app.get("/api/crm/config")
async def get_crm_config():
    """Return CRM mapping and provider readiness for plug-and-play onboarding."""
    return crm_status_snapshot()


class CrmConfigUpdate(BaseModel):
    """Payload for updating CRM routing and credentials at runtime.

    All fields are optional — send only what you want to change.
    Changes survive until the next process restart (Railway env vars are the
    persistent store; use this endpoint to apply changes without redeploying).
    """
    # Routing maps  {business_key: provider}  e.g. {"aiphoneguy": "ghl"}
    business_crm_map: Optional[Dict[str, str]] = None
    # Agent-level overrides  {agent_id: provider}
    agent_crm_map: Optional[Dict[str, str]] = None
    # Credentials — omit any key you don't want to change
    ghl_api_key: Optional[str] = None
    ghl_location_id: Optional[str] = None
    hubspot_api_key: Optional[str] = None
    attio_api_key: Optional[str] = None


_VALID_PROVIDERS = {"ghl", "hubspot", "attio"}


@app.post("/api/crm/config")
async def update_crm_config(
    payload: CrmConfigUpdate,
    authorization: Optional[str] = Header(None),
):
    """Update CRM routing and credentials at runtime without redeploying.

    Requires Bearer auth when API_KEYS is configured.
    Changes take effect immediately — next CRM push uses the new mapping.
    """
    validate_key(authorization)

    import json as _json

    changed: List[str] = []

    # -- Validate and apply business_crm_map
    if payload.business_crm_map is not None:
        for biz, provider in payload.business_crm_map.items():
            if provider not in _VALID_PROVIDERS:
                raise HTTPException(
                    status_code=422,
                    detail=f"Invalid provider '{provider}' for business '{biz}'. Must be one of: {sorted(_VALID_PROVIDERS)}",
                )
        os.environ["BUSINESS_CRM_MAP"] = _json.dumps(payload.business_crm_map)
        changed.append("business_crm_map")

    # -- Validate and apply agent_crm_map
    if payload.agent_crm_map is not None:
        for agent, provider in payload.agent_crm_map.items():
            if provider not in _VALID_PROVIDERS:
                raise HTTPException(
                    status_code=422,
                    detail=f"Invalid provider '{provider}' for agent '{agent}'. Must be one of: {sorted(_VALID_PROVIDERS)}",
                )
        os.environ["AGENT_CRM_MAP"] = _json.dumps(payload.agent_crm_map)
        changed.append("agent_crm_map")

    # -- Apply credential updates (never log or echo values back)
    if payload.ghl_api_key:
        os.environ["GHL_API_KEY"] = payload.ghl_api_key.strip()
        changed.append("ghl_api_key")
    if payload.ghl_location_id:
        os.environ["GHL_LOCATION_ID"] = payload.ghl_location_id.strip()
        changed.append("ghl_location_id")
    if payload.hubspot_api_key:
        os.environ["HUBSPOT_API_KEY"] = payload.hubspot_api_key.strip()
        changed.append("hubspot_api_key")
    if payload.attio_api_key:
        os.environ["ATTIO_API_KEY"] = payload.attio_api_key.strip()
        changed.append("attio_api_key")

    if not changed:
        raise HTTPException(status_code=400, detail="No fields provided to update.")

    # -- Clear cached settings so next call re-reads the updated env vars
    get_settings.cache_clear()

    return {
        "updated": changed,
        "crm_config": crm_status_snapshot(),
    }


# ── Activation Layer — Artifact & Approval Queue API ─────────────────────────

class ArtifactCreateRequest(BaseModel):
    """Request body for manually submitting an artifact from an external agent or system."""
    agent_id: str
    business_key: str
    artifact_type: str
    audience: str
    intent: str
    content: str
    subject: Optional[str] = None
    channel_candidates: Optional[List[str]] = None
    confidence: float = 0.8
    risk_level: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class RejectRequest(BaseModel):
    reason: str = ""


@app.post("/api/artifacts")
async def submit_artifact(
    payload: ArtifactCreateRequest,
    authorization: Optional[str] = Header(None),
):
    """
    Submit a new artifact into the Activation Layer.

    The system evaluates the artifact against the moral gate, assigns a risk
    tier, and either auto-approves and dispatches it (low risk, high confidence)
    or places it in the approval queue / escalation lane.

    Requires Bearer auth when API_KEYS is configured.
    """
    validate_key(authorization)

    try:
        artifact = create_artifact(
            agent_id=payload.agent_id,
            business_key=payload.business_key,
            artifact_type=payload.artifact_type,
            audience=payload.audience,
            intent=payload.intent,
            content=payload.content,
            subject=payload.subject,
            channel_candidates=payload.channel_candidates,
            confidence=payload.confidence,
            risk_level=payload.risk_level,
            metadata=payload.metadata,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    artifact_id = queue_artifact(artifact)

    receipt = None
    if artifact.status == "auto_approved":
        try:
            receipt = dispatch_artifact(artifact)
        except Exception as exc:
            logging.error("[artifacts] auto-dispatch failed for %s: %s", artifact_id, exc)

    return {
        "artifact_id": artifact_id,
        "status": artifact.status,
        "risk_level": artifact.risk_level,
        "requires_human_approval": artifact.requires_human_approval,
        "receipt": receipt.to_dict() if receipt else None,
    }


@app.get("/api/artifacts")
async def list_artifacts_endpoint(
    business_key: Optional[str] = None,
    agent_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 50,
    authorization: Optional[str] = Header(None),
):
    """
    List artifacts with optional filters.

    Query params:
        business_key — filter by business
        agent_id     — filter by producing agent
        status       — filter by status (pending_approval, delivered, failed, …)
        limit        — max results (default 50, max 200)
    """
    validate_key(authorization)

    if status and status not in ARTIFACT_STATUSES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid status '{status}'. Valid: {sorted(ARTIFACT_STATUSES)}",
        )

    items = list_artifacts(
        business_key=business_key,
        agent_id=agent_id,
        status=status,
        limit=limit,
    )
    return {"count": len(items), "artifacts": items}


@app.get("/api/artifacts/pending")
async def get_pending_approvals(authorization: Optional[str] = Header(None)):
    """Return all artifacts waiting for human approval (oldest first)."""
    validate_key(authorization)
    items = get_pending()
    return {"count": len(items), "pending": items}


@app.get("/api/artifacts/escalated")
async def get_escalated_artifacts(authorization: Optional[str] = Header(None)):
    """Return all escalated artifacts (high risk or moral-gate failures)."""
    validate_key(authorization)
    items = get_escalated()
    return {"count": len(items), "escalated": items}


@app.get("/api/artifacts/{artifact_id}")
async def get_artifact(artifact_id: str, authorization: Optional[str] = Header(None)):
    """Return a single artifact and all its delivery receipts."""
    validate_key(authorization)
    record = get_artifact_record(artifact_id)
    if not record:
        raise HTTPException(status_code=404, detail=f"Artifact '{artifact_id}' not found.")
    receipts = get_receipts(artifact_id)
    return {
        "artifact": record,
        "receipts": [r.to_dict() for r in receipts],
    }


@app.post("/api/artifacts/{artifact_id}/approve")
async def approve_artifact_endpoint(
    artifact_id: str,
    authorization: Optional[str] = Header(None),
):
    """
    1-click approval — approve a queued artifact and dispatch it immediately.

    Requires Bearer auth. The reviewer identity is inferred from the API key
    header (recorded in the approval audit log as "api_key_holder").
    """
    validate_key(authorization)

    ok = approve_artifact(artifact_id, reviewer="api_key_holder")
    if not ok:
        record = get_artifact_record(artifact_id)
        current_status = record["status"] if record else "not_found"
        raise HTTPException(
            status_code=409,
            detail=f"Cannot approve artifact '{artifact_id}' — current status: '{current_status}'.",
        )

    # Reconstruct an Artifact object for dispatch from the DB record
    record = get_artifact_record(artifact_id)
    if not record:
        raise HTTPException(status_code=404, detail="Artifact not found after approval.")

    import datetime as _dt
    artifact = Artifact(
        artifact_id=record["artifact_id"],
        agent_id=record["agent_id"],
        business_key=record["business_key"],
        artifact_type=record["artifact_type"],
        audience=record["audience"],
        intent=record["intent"],
        content=record["content"],
        subject=record.get("subject"),
        channel_candidates=record.get("channel_candidates") or [],
        confidence=float(record.get("confidence", 0.8)),
        risk_level=record["risk_level"],
        requires_human_approval=bool(record.get("requires_human_approval", True)),
        metadata=record.get("metadata") or {},
        created_at=record["created_at"] if isinstance(record["created_at"], _dt.datetime)
                   else _dt.datetime.utcnow(),
        status="approved",
    )

    try:
        receipt = dispatch_artifact(artifact)
    except Exception as exc:
        logging.error("[artifacts] dispatch after approval failed for %s: %s", artifact_id, exc)
        raise HTTPException(status_code=500, detail=f"Dispatch failed: {exc}")

    return {
        "artifact_id": artifact_id,
        "status": "dispatched",
        "receipt": receipt.to_dict(),
    }


@app.post("/api/artifacts/{artifact_id}/reject")
async def reject_artifact_endpoint(
    artifact_id: str,
    payload: RejectRequest,
    authorization: Optional[str] = Header(None),
):
    """Reject a queued artifact. It will not be dispatched."""
    validate_key(authorization)

    ok = reject_artifact(artifact_id, reviewer="api_key_holder", reason=payload.reason)
    if not ok:
        raise HTTPException(
            status_code=404,
            detail=f"Artifact '{artifact_id}' not found.",
        )
    return {"artifact_id": artifact_id, "status": "rejected", "reason": payload.reason}


@app.get("/api/agent-logs/{agent_name}")
async def get_agent_logs_by_date(agent_name: str):
    """Return all logs for a specific agent organized by date, word-for-word."""
    agent_id = normalize_agent_id(agent_name)
    if agent_id not in AGENTS:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_name}' not found.")

    agent_type = LOG_TYPES.get(agent_id, "unknown")

    if DATABASE_URL:
        try:
            with _db() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT run_date, log_type, content, created_at "
                        "FROM agent_logs WHERE agent_name = %s "
                        "ORDER BY created_at DESC",
                        (agent_id,),
                    )
                    rows = cur.fetchall()

            logs_by_date = {}
            for run_date, log_type, content, created_at in rows:
                date_key = str(run_date)
                if date_key not in logs_by_date:
                    logs_by_date[date_key] = []
                logs_by_date[date_key].append(
                    {
                        "filename": f"{agent_id}_{log_type}_{run_date}.log",
                        "type": "log",
                        "content": content,
                        "size_kb": round(len(content or "") / 1024, 2),
                        "timestamp": str(created_at),
                    }
                )

            return {
                "agent": agent_name,
                "agent_type": agent_type,
                "logs_by_date": logs_by_date,
                "total_runs": len(rows),
                "dates": sorted(logs_by_date.keys(), reverse=True),
            }
        except Exception as e:
            logging.error(f"[DB] /api/agent-logs failed, falling back to files: {e}")

    # Filesystem fallback for local/dev only.
    pattern = os.path.join("logs", f"{agent_id}_*.log")
    file_logs = sorted(glob.glob(pattern), reverse=True)
    if not file_logs:
        return {
            "agent": agent_name,
            "agent_type": agent_type,
            "logs_by_date": {},
            "total_runs": 0,
            "dates": [],
        }

    logs_by_date = {}
    for file_path in file_logs:
        try:
            filename = os.path.basename(file_path)
            date_key = filename.split("_")[-1].replace(".log", "")
            with open(file_path, "r") as f:
                content = f.read()
            logs_by_date.setdefault(date_key, []).append(
                {
                    "filename": filename,
                    "type": "log",
                    "content": content,
                    "size_kb": round(len(content) / 1024, 2),
                    "timestamp": datetime.datetime.fromtimestamp(os.path.getmtime(file_path)).isoformat(),
                }
            )
        except Exception as e:
            logging.warning(f"Could not read file {file_path}: {e}")

    return {
        "agent": agent_name,
        "agent_type": agent_type,
        "logs_by_date": logs_by_date,
        "total_runs": len(file_logs),
        "dates": sorted(logs_by_date.keys(), reverse=True),
    }


@app.get("/api/pitwall/telemetry")
async def get_pitwall_telemetry():
    """Master telemetry feed for Pit Wall React app (auto-refresh friendly)."""
    crm_config = crm_status_snapshot()
    business_map = crm_config.get("business_crm_map", {})
    recent_runs = _fetch_recent_runs_by_agent()

    # Non-CRM rivers — Agent Empire runs on Skool, CustomerAdvocate is internal.
    # These should not fall through to resolve_provider() which defaults to 'ghl'.
    NON_CRM_PLATFORMS = {
        "agentempire": "skool",
        "customeradvocate": "internal",
    }

    teams = []
    for team_id in _pitwall_team_ids():
        team_info = BUSINESSES.get(team_id, {})
        if team_id in NON_CRM_PLATFORMS:
            provider = NON_CRM_PLATFORMS[team_id]
        else:
            provider = str(business_map.get(team_id, resolve_provider(team_id, team_info.get("agents", [""])[0] if team_info.get("agents") else "")))
        kpis = _team_revenue_kpis(team_id)

        agents = []
        for agent_id in team_info.get("agents", []):
            meta = PITWALL_AGENT_META.get(agent_id, {})
            last_run = recent_runs.get(agent_id)
            agents.append(
                {
                    "agent_id": agent_id,
                    "name": _pitwall_display_name(agent_id),
                    "role": meta.get("role", "Agent"),
                    "lane": meta.get("lane", "Operations"),
                    "last_run": last_run,
                    "status": _derive_status_from_last_run(last_run),
                }
            )

        teams.append(
            {
                "team_id": team_id,
                "team_name": team_info.get("name", team_id),
                "crm_provider": provider,
                "sprint_status": "active" if any(a["status"] == "green" for a in agents) else "degraded",
                "kpis": {
                    "open_opps": kpis["open_opps"],
                    "reply_rate": kpis["reply_rate"],
                    "win_rate": kpis["win_rate"],
                },
                "agents": agents,
            }
        )

    # AVO Intelligence Layer panels
    axiom_data = {}
    cost_data = {}
    try:
        from agents.ceo.axiom import get_axiom_dashboard_data, get_directives_by_agent
        axiom_data = get_axiom_dashboard_data()
        axiom_data["directives_by_agent"] = get_directives_by_agent(days=7)
    except Exception:
        pass
    try:
        from core.cost_tracker import (
            get_daily_cost, get_monthly_projection,
            get_agent_cost_summary, get_cost_by_day,
        )
        cost_data = {
            "today_usd": get_daily_cost(),
            "projection": get_monthly_projection(),
            "by_agent": get_agent_cost_summary(days=7)[:5],
            "by_day": get_cost_by_day(days=7),
        }
    except Exception:
        pass

    return {
        "timestamp": _iso_now(),
        "refresh_seconds": 60,
        "railway": _fetch_railway_status(),
        "activation_pipeline": _artifact_pipeline_counts(),
        "teams": teams,
        "axiom": axiom_data,
        "cost": cost_data,
    }


@app.get("/api/pitwall/team/{team_id}")
async def get_pitwall_team(team_id: str):
    """Team-level telemetry and performance details."""
    team_id = (team_id or "").strip().lower()
    if team_id not in BUSINESSES:
        raise HTTPException(status_code=404, detail=f"Unknown team_id '{team_id}'")

    crm_config = crm_status_snapshot()
    _non_crm = {"agentempire": "skool", "customeradvocate": "internal"}
    if team_id in _non_crm:
        provider = _non_crm[team_id]
    else:
        provider = str((crm_config.get("business_crm_map") or {}).get(team_id, "ghl"))
    team_info = BUSINESSES[team_id]
    kpis = _team_revenue_kpis(team_id)
    first_agent = team_info.get("agents", [""])[0] if team_info.get("agents") else ""

    latest_log = _latest_log_for_agent(first_agent)
    priorities = _extract_signal_lines(latest_log, limit=4)
    if not priorities:
        priorities = ["No recent live priorities were parsed from agent logs."]

    bottlenecks = _team_bottlenecks(team_id, provider)

    recent_runs = _fetch_recent_runs_by_agent()
    roster = []
    for agent_id in team_info.get("agents", []):
        meta = PITWALL_AGENT_META.get(agent_id, {})
        last_run = recent_runs.get(agent_id)
        roster.append(
            {
                "agent_id": agent_id,
                "name": _pitwall_display_name(agent_id),
                "role": meta.get("role", "Agent"),
                "lane": meta.get("lane", "Operations"),
                "status": _derive_status_from_last_run(last_run),
                "last_run": last_run,
            }
        )

    return {
        "timestamp": _iso_now(),
        "team_id": team_id,
        "team_name": team_info.get("name", team_id),
        "crm_provider": provider,
        "live_status": "active" if any(r["status"] == "green" for r in roster) else "degraded",
        "stage": "execution",
        "sprint": "current",
        "kpis": {
            "open_opps": kpis["open_opps"],
            "reply_rate": kpis["reply_rate"],
            "win_rate": kpis["win_rate"],
        },
        "pipeline_activity": kpis["pipeline_activity"],
        "priorities": priorities,
        "bottlenecks": bottlenecks,
        "crm_live_metrics": _crm_live_metrics(team_id, provider),
        "agents": roster,
    }


@app.get("/api/pitwall/team/{team_id}/agent/{agent_id}")
async def get_pitwall_agent(team_id: str, agent_id: str):
    """Agent-level telemetry and brief details."""
    team_id = (team_id or "").strip().lower()
    agent_id = normalize_agent_id(agent_id)

    if team_id not in BUSINESSES:
        raise HTTPException(status_code=404, detail=f"Unknown team_id '{team_id}'")
    if agent_id not in AGENTS:
        raise HTTPException(status_code=404, detail=f"Unknown agent_id '{agent_id}'")
    if agent_id not in BUSINESSES[team_id]["agents"]:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' is not part of team '{team_id}'")

    last_runs = _fetch_recent_runs_by_agent()
    last_run = last_runs.get(agent_id)
    status = _derive_status_from_last_run(last_run)
    meta = PITWALL_AGENT_META.get(agent_id, {})
    grounded = _agent_grounded_focus_and_actions(team_id=team_id, agent_id=agent_id, last_run=last_run)
    focus = grounded["focus"]
    actions = grounded["next_actions"]
    risks = _agent_risk_flags(team_id=team_id, agent_id=agent_id, last_run=last_run)

    return {
        "timestamp": _iso_now(),
        "team_id": team_id,
        "team_name": BUSINESSES[team_id]["name"],
        "agent": {
            "agent_id": agent_id,
            "name": _pitwall_display_name(agent_id),
            "role": meta.get("role", "Agent"),
            "lane": meta.get("lane", "Operations"),
            "status": status,
            "last_run": last_run,
            "initials": "".join([part[0] for part in _pitwall_display_name(agent_id).split() if part])[:2].upper(),
        },
        "focus": focus,
        "next_actions": actions,
        "risk_flags": risks,
        "data_sources": {
            "focus": "revenue_events aggregates + artifacts status + provider readiness + run freshness",
            "next_actions": "artifacts status + provider readiness + run freshness + team KPI aggregates",
            "risk_flags": "artifacts table + provider readiness + run freshness telemetry",
        },
    }


@app.get("/dashboard")
async def get_dashboard():
    """Serve the React Pit Wall app (company → agent → history navigation)."""
    react_index = Path(__file__).parent / "static" / "pitwall-react" / "index.html"
    if react_index.exists():
        return FileResponse(str(react_index), media_type="text/html")
    # Fallback to legacy static HTML
    dashboard_path = Path(__file__).parent / "static" / "dashboard.html"
    if dashboard_path.exists():
        return FileResponse(str(dashboard_path), media_type="text/html")
    return {"error": f"Dashboard not found"}


@app.get("/pit-wall")
async def get_pit_wall():
    """Serve React Pit Wall app when built, fallback to legacy static page."""
    react_index = Path(__file__).parent / "static" / "pitwall-react" / "index.html"
    if react_index.exists():
        return FileResponse(str(react_index), media_type="text/html")

    visual_path = Path(__file__).parent / "static" / "visual.html"
    if visual_path.exists():
        return FileResponse(str(visual_path), media_type="text/html")
    return {"error": f"Pit Wall page not found at {visual_path}"}


@app.get("/team/{team_id}")
async def get_pit_wall_team_route(team_id: str):
    """Serve React app for team-level client-side routes."""
    react_index = Path(__file__).parent / "static" / "pitwall-react" / "index.html"
    if react_index.exists():
        return FileResponse(str(react_index), media_type="text/html")
    raise HTTPException(status_code=404, detail="Pit Wall React build not found.")


@app.get("/team/{team_id}/agent/{agent_id}")
async def get_pit_wall_agent_route(team_id: str, agent_id: str):
    """Serve React app for agent-level client-side routes."""
    react_index = Path(__file__).parent / "static" / "pitwall-react" / "index.html"
    if react_index.exists():
        return FileResponse(str(react_index), media_type="text/html")
    raise HTTPException(status_code=404, detail="Pit Wall React build not found.")


@app.get("/api/pitwall/ops-dashboard")
async def pitwall_ops_dashboard():
    """Aggregated data feed for the Pit Wall ops dashboard."""
    try:
        # Agent health: last run time + status for all agents
        recent_runs = _fetch_recent_runs_by_agent()
        agent_health = []
        for agent_id in PITWALL_AGENT_META:
            last_run = recent_runs.get(agent_id)
            meta = PITWALL_AGENT_META.get(agent_id, {})
            status = _derive_status_from_last_run(last_run)
            # Get last log preview
            preview = ""
            try:
                rows = fetch_all(
                    "SELECT LEFT(content, 200), created_at FROM agent_logs "
                    "WHERE agent_name = %s ORDER BY created_at DESC LIMIT 1",
                    (agent_id,),
                )
                if rows:
                    preview = rows[0][0]
            except Exception:
                pass
            agent_health.append({
                "agent_id": agent_id,
                "name": _pitwall_display_name(agent_id),
                "role": meta.get("role", "Agent"),
                "last_run": last_run,
                "status": status,
                "log_preview": preview,
            })

        # CRM push counts today
        crm_today = {}
        try:
            rows = fetch_all(
                "SELECT agent_name, status, COUNT(*) FROM crm_push_logs "
                "WHERE created_at >= CURRENT_DATE GROUP BY agent_name, status"
            )
            for agent, status, count in rows:
                if agent not in crm_today:
                    crm_today[agent] = {"created": 0, "duplicate_skipped": 0}
                crm_today[agent][status] = crm_today[agent].get(status, 0) + count
        except Exception:
            pass

        # CRM push counts this week
        crm_week = {}
        try:
            rows = fetch_all(
                "SELECT agent_name, status, COUNT(*) FROM crm_push_logs "
                "WHERE created_at >= CURRENT_DATE - INTERVAL '7 days' GROUP BY agent_name, status"
            )
            for agent, status, count in rows:
                if agent not in crm_week:
                    crm_week[agent] = {"created": 0, "duplicate_skipped": 0}
                crm_week[agent][status] = crm_week[agent].get(status, 0) + count
        except Exception:
            pass

        # Latest COO report
        coo_report = None
        try:
            rows = fetch_all(
                "SELECT content FROM agent_logs "
                "WHERE agent_name = 'command' AND log_type = 'ops_report' "
                "ORDER BY created_at DESC LIMIT 1"
            )
            if rows:
                import json as _json
                coo_report = _json.loads(rows[0][0])
        except Exception:
            pass

        return JSONResponse(content={
            "timestamp": _iso_now(),
            "refresh_seconds": 60,
            "agent_health": agent_health,
            "crm_today": crm_today,
            "crm_week": crm_week,
            "coo_report": coo_report,
            "businesses": {
                "aiphoneguy": {
                    "name": "The AI Phone Guy",
                    "sales_agent": "tyler",
                    "crm": "GoHighLevel",
                },
                "callingdigital": {
                    "name": "Calling Digital",
                    "sales_agent": "marcus",
                    "crm": "Attio",
                },
                "autointelligence": {
                    "name": "Automotive Intelligence",
                    "sales_agent": "ryan_data",
                    "crm": "HubSpot",
                },
            },
        })
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.post("/api/pitwall/clear-alerts")
async def clear_alerts():
    """Re-run the COO command to refresh ops alerts.

    The COO checks live system state every time it runs. If issues
    have been fixed since the last report, the alerts disappear from
    the new report. Issues that are still real will reappear.

    This is the right semantics for a 'Clear Alerts' button — it does
    not silence anything, it re-checks the system.
    """
    try:
        report = run_coo_command()
        return JSONResponse(content={
            "status": "ok",
            "alerts_remaining": report.get("alert_count", 0),
            "alerts": report.get("alerts", []),
            "priority": report.get("priority_recommendation", ""),
            "regenerated_at": _iso_now(),
        })
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "error": str(e)})


@app.get("/ops-report")
async def ops_report():
    """Latest COO Command ops report. Pulls most recent from PostgreSQL."""
    try:
        rows = fetch_all(
            "SELECT content, created_at FROM agent_logs "
            "WHERE agent_name = 'command' AND log_type = 'ops_report' "
            "ORDER BY created_at DESC LIMIT 1"
        )
        if not rows:
            return JSONResponse(content={"status": "no_report", "message": "No ops report generated yet."})
        import json as _json
        report = _json.loads(rows[0][0])
        report["retrieved_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        return JSONResponse(content=report)
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "error": str(e)})


@app.get("/health")
async def health():
    jobs_info = []
    for job in scheduler.get_jobs():
        jobs_info.append({
            "id": job.id,
            "name": job.name,
            "next_run": _job_next_run_str(job),
        })
    return {
        "status": "ok",
        "version": SETTINGS.app_version,
        "engine": "revenue",
        "scheduler": "running" if scheduler.running else "stopped",
        "jobs_registered": len(jobs_info),
        "postgres": "connected" if SETTINGS.postgres_enabled else "not configured",
        "ghl_configured": SETTINGS.ghl_ready,
        "hubspot_configured": SETTINGS.hubspot_ready,
        "attio_configured": SETTINGS.attio_ready,
        "business_crm_map": SETTINGS.business_crm_map,
        "revenue_tracking": SETTINGS.postgres_enabled,
        "environment": SETTINGS.environment,
        "strict_startup": SETTINGS.strict_startup,
        "llm_model": SETTINGS.llm_model,
        "llm_ready": SETTINGS.llm_ready,
        "jobs": jobs_info,
    }


@app.get("/health/ready")
async def readiness():
    """Readiness endpoint for production probes and deployment gates."""
    warnings = SETTINGS.startup_warnings()
    fatals = SETTINGS.startup_fatals()
    is_ready = scheduler.running and (len(fatals) == 0)
    payload = {
        "ready": is_ready,
        "scheduler_running": scheduler.running,
        "environment": SETTINGS.environment,
        "strict_startup": SETTINGS.strict_startup,
        "warnings": warnings,
        "fatals": fatals,
    }
    if is_ready:
        return payload
    return JSONResponse(status_code=503, content=payload)


# ═══════════════════════════════════════════════════════════════════
# BLOOMBERRY — Tech Stack Lookup
# ═══════════════════════════════════════════════════════════════════

@app.get("/tech/{domain}")
async def tech_stack_lookup(domain: str, category: str = None):
    """Look up a company's tech stack via Bloomberry."""
    from tools.bloomberry import get_tech_stack, bloomberry_ready
    if not bloomberry_ready():
        return JSONResponse(status_code=503, content={"error": "BLOOMBERRY_API_KEY not configured"})
    return get_tech_stack(domain, category=category)


# ═══════════════════════════════════════════════════════════════════
# PROJECT PAPERCLIP — River Endpoints
# ═══════════════════════════════════════════════════════════════════

@app.get("/paperclip")
async def paperclip_status():
    """Project Paperclip empire status."""
    try:
        from rivers.ai_phone_guy.workflow import get_stats as apg_stats
        from rivers.calling_digital.workflow import get_stats as cd_stats
        from rivers.automotive_intelligence.workflow import get_stats as ai_stats
        from rivers.agent_empire.workflow import get_stats as ae_stats
        from rivers.customer_advocate.workflow import get_stats as ca_stats
        return {
            "status": "online",
            "project": "paperclip",
            "rivers": {
                "ai_phone_guy": apg_stats(),
                "calling_digital": cd_stats(),
                "automotive_intelligence": ai_stats(),
                "agent_empire": ae_stats(),
                "customer_advocate": ca_stats(),
            },
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.get("/paperclip/rivers")
async def paperclip_rivers():
    return {
        "rivers": [
            {"name": "AI Phone Guy", "crm": "GoHighLevel", "agents": ["Alex", "Tyler", "Zoe", "Jennifer", "Randy"], "revops": "Randy", "schedule": "Randy every 4h"},
            {"name": "Calling Digital", "crm": "Attio", "agents": ["Dek", "Marcus", "Sofia", "Carlos", "Nova", "Brenda"], "revops": "Brenda", "schedule": "Brenda every 2h"},
            {"name": "Automotive Intelligence", "crm": "HubSpot", "agents": ["Michael Meta", "Chase", "Atlas", "Ryan", "Phoenix", "Darrell"], "revops": "Darrell", "schedule": "Darrell every 1h"},
            {"name": "Agent Empire", "platform": "Skool", "agents": ["Debra", "Wade", "Tammy", "Sterling"], "schedule": "Debra Mon 6am / Wade Mon 9am / Tammy 6hr / Sterling daily 7am"},
            {"name": "CustomerAdvocate", "platform": "Internal", "agents": ["Clint", "Sherry"], "components": ["VERA", "AATA", "The Exchange"], "schedule": "Clint 10am / Sherry 11am daily"},
        ],
        "total_agents": sum(len(b["agents"]) for b in BUSINESSES.values()),
    }


@app.post("/paperclip/run/{agent}")
async def paperclip_trigger_agent(agent: str):
    """Manually trigger a Paperclip river agent."""
    import importlib
    runners = {
        "randy": ("rivers.ai_phone_guy.workflow", "randy_run"),
        "brenda": ("rivers.calling_digital.workflow", "brenda_run"),
        "darrell": ("rivers.automotive_intelligence.workflow", "darrell_run"),
        "tammy": ("rivers.agent_empire.workflow", "tammy_run"),
        "wade": ("rivers.agent_empire.workflow", "wade_run"),
        "debra": ("rivers.agent_empire.workflow", "debra_run"),
        "sterling": ("rivers.agent_empire.workflow", "sterling_run"),
        "clint": ("rivers.customer_advocate.workflow", "clint_run"),
        "sherry": ("rivers.customer_advocate.workflow", "sherry_run"),
    }
    if agent not in runners:
        return JSONResponse(status_code=404, content={"error": f"Unknown agent: {agent}"})
    mod_name, func_name = runners[agent]
    mod = importlib.import_module(mod_name)
    func = getattr(mod, func_name)
    func()
    return {"status": "completed", "agent": agent}


@app.post("/paperclip/cleanup/hubspot")
async def paperclip_cleanup():
    """Re-run HubSpot contact classification."""
    from rivers.automotive_intelligence.cleanup import run_cleanup
    return run_cleanup()
