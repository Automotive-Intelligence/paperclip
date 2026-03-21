"""
tools/revenue_tracker.py — Revenue Intelligence Engine
Tracks pipeline value, conversion metrics, email performance, and MRR
across all 3 businesses. Persists to Postgres for historical analysis.
"""

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
import logging
import datetime
import json
from typing import Optional

# These will be set by app.py at startup to avoid circular imports
_db_context = None
_cst_tz = None


def init_revenue_tracker(db_context_manager, cst_timezone):
    """Called by app.py to inject DB connection and timezone."""
    global _db_context, _cst_tz
    _db_context = db_context_manager
    _cst_tz = cst_timezone


def init_revenue_tables():
    """Create revenue tracking tables if they don't exist."""
    if _db_context is None:
        logging.warning("[Revenue] DB not available — revenue tracking disabled.")
        return

    try:
        with _db_context() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS revenue_events (
                        id              SERIAL PRIMARY KEY,
                        event_type      TEXT NOT NULL,
                        business_key    TEXT NOT NULL,
                        agent_name      TEXT NOT NULL,
                        contact_id      TEXT,
                        opportunity_id  TEXT,
                        monetary_value  NUMERIC(10,2) DEFAULT 0,
                        metadata        JSONB DEFAULT '{}',
                        created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    );
                    CREATE INDEX IF NOT EXISTS idx_revenue_events_biz
                        ON revenue_events (business_key, created_at DESC);
                    CREATE INDEX IF NOT EXISTS idx_revenue_events_type
                        ON revenue_events (event_type, created_at DESC);

                    CREATE TABLE IF NOT EXISTS pipeline_snapshot (
                        id              SERIAL PRIMARY KEY,
                        business_key    TEXT NOT NULL,
                        snapshot_date   DATE NOT NULL,
                        total_prospects INT DEFAULT 0,
                        emails_sent     INT DEFAULT 0,
                        replies         INT DEFAULT 0,
                        demos_booked    INT DEFAULT 0,
                        deals_closed    INT DEFAULT 0,
                        pipeline_value  NUMERIC(10,2) DEFAULT 0,
                        closed_value    NUMERIC(10,2) DEFAULT 0,
                        metadata        JSONB DEFAULT '{}',
                        created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    );
                    CREATE INDEX IF NOT EXISTS idx_pipeline_snapshot_biz
                        ON pipeline_snapshot (business_key, snapshot_date DESC);

                    CREATE TABLE IF NOT EXISTS content_queue (
                        id              SERIAL PRIMARY KEY,
                        business_key    TEXT NOT NULL,
                        agent_name      TEXT NOT NULL,
                        platform        TEXT NOT NULL,
                        content_type    TEXT NOT NULL,
                        title           TEXT,
                        body            TEXT NOT NULL,
                        hashtags        TEXT DEFAULT '',
                        cta             TEXT DEFAULT '',
                        funnel_stage    TEXT DEFAULT 'awareness',
                        status          TEXT DEFAULT 'queued',
                        scheduled_for   TIMESTAMPTZ,
                        published_at    TIMESTAMPTZ,
                        created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    );
                    CREATE INDEX IF NOT EXISTS idx_content_queue_status
                        ON content_queue (status, scheduled_for);
                """)
        logging.info("[Revenue] Revenue tracking tables ready.")
    except Exception as e:
        logging.error(f"[Revenue] Table creation failed: {e}")


# ── Event Tracking ───────────────────────────────────────────────────────────


def track_event(
    event_type: str,
    business_key: str,
    agent_name: str,
    contact_id: str = "",
    opportunity_id: str = "",
    monetary_value: float = 0,
    metadata: Optional[dict] = None,
):
    """
    Record a revenue event. Event types:
    - prospect_created: New contact added to CRM
    - email_sent: Cold email sent
    - email_opened: Email opened (from GHL webhook)
    - email_replied: Prospect replied (from GHL webhook)
    - demo_booked: Demo/assessment scheduled
    - deal_closed: Deal won
    - deal_lost: Deal lost
    - upsell_sent: Upsell outreach sent
    - retention_save: Churn prevention action taken
    """
    if _db_context is None:
        return

    try:
        with _db_context() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO revenue_events "
                    "(event_type, business_key, agent_name, contact_id, opportunity_id, monetary_value, metadata) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (
                        event_type,
                        business_key,
                        agent_name,
                        contact_id,
                        opportunity_id,
                        monetary_value,
                        json.dumps(metadata or {}),
                    ),
                )
        logging.info(f"[Revenue] {event_type}: {agent_name}/{business_key} ${monetary_value}")
    except Exception as e:
        logging.error(f"[Revenue] Event tracking failed: {e}")


# ── Content Queue ────────────────────────────────────────────────────────────


def queue_content(
    business_key: str,
    agent_name: str,
    pieces: list,
):
    """Add parsed content pieces to the publishing queue."""
    if _db_context is None or not pieces:
        return 0

    queued = 0
    try:
        with _db_context() as conn:
            with conn.cursor() as cur:
                for piece in pieces:
                    cur.execute(
                        "INSERT INTO content_queue "
                        "(business_key, agent_name, platform, content_type, title, body, hashtags, cta, funnel_stage) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                        (
                            business_key,
                            agent_name,
                            piece.get("platform", ""),
                            piece.get("content_type", ""),
                            piece.get("title", ""),
                            piece.get("body", ""),
                            piece.get("hashtags", ""),
                            piece.get("cta", ""),
                            piece.get("funnel_stage", "awareness"),
                        ),
                    )
                    queued += 1
        logging.info(f"[Revenue] Queued {queued} content pieces for {business_key}/{agent_name}")
    except Exception as e:
        logging.error(f"[Revenue] Content queue failed: {e}")
    return queued


def get_content_queue(business_key: Optional[str] = None, status: str = "queued", limit: int = 20) -> list:
    """Get pending content from the queue, ready to publish."""
    if _db_context is None:
        return []

    try:
        with _db_context() as conn:
            with conn.cursor() as cur:
                if business_key:
                    cur.execute(
                        "SELECT id, business_key, agent_name, platform, content_type, title, body, hashtags, cta, funnel_stage, created_at "
                        "FROM content_queue WHERE business_key = %s AND status = %s "
                        "ORDER BY created_at DESC LIMIT %s",
                        (business_key, status, limit),
                    )
                else:
                    cur.execute(
                        "SELECT id, business_key, agent_name, platform, content_type, title, body, hashtags, cta, funnel_stage, created_at "
                        "FROM content_queue WHERE status = %s "
                        "ORDER BY created_at DESC LIMIT %s",
                        (status, limit),
                    )
                rows = cur.fetchall()
                return [
                    {
                        "id": r[0],
                        "business_key": r[1],
                        "agent_name": r[2],
                        "platform": r[3],
                        "content_type": r[4],
                        "title": r[5],
                        "body": r[6],
                        "hashtags": r[7],
                        "cta": r[8],
                        "funnel_stage": r[9],
                        "created_at": str(r[10]),
                    }
                    for r in rows
                ]
    except Exception as e:
        logging.error(f"[Revenue] Content queue fetch failed: {e}")
        return []


def mark_content_published(content_id: int):
    """Mark a content piece as published."""
    if _db_context is None:
        return
    try:
        with _db_context() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE content_queue SET status = 'published', published_at = NOW() WHERE id = %s",
                    (content_id,),
                )
    except Exception as e:
        logging.error(f"[Revenue] Mark published failed: {e}")


# ── Revenue Dashboard Queries ────────────────────────────────────────────────


def get_revenue_summary(business_key: Optional[str] = None, days: int = 30) -> dict:
    """
    Get revenue metrics for the dashboard.
    Returns: prospects, emails sent, pipeline value, conversion rates, etc.
    """
    if _db_context is None:
        return {"error": "Database not available"}

    try:
        with _db_context() as conn:
            with conn.cursor() as cur:
                where_biz = "AND business_key = %s" if business_key else ""
                params_base = (days,)
                params = (days, business_key) if business_key else (days,)

                # Total prospects created
                cur.execute(
                    f"SELECT COUNT(*) FROM revenue_events "
                    f"WHERE event_type = 'prospect_created' "
                    f"AND created_at >= NOW() - INTERVAL '%s days' {where_biz}",
                    params,
                )
                prospects = cur.fetchone()[0]

                # Emails sent
                cur.execute(
                    f"SELECT COUNT(*) FROM revenue_events "
                    f"WHERE event_type = 'email_sent' "
                    f"AND created_at >= NOW() - INTERVAL '%s days' {where_biz}",
                    params,
                )
                emails_sent = cur.fetchone()[0]

                # Pipeline value (sum of open opportunities)
                cur.execute(
                    f"SELECT COALESCE(SUM(monetary_value), 0) FROM revenue_events "
                    f"WHERE event_type = 'prospect_created' "
                    f"AND created_at >= NOW() - INTERVAL '%s days' {where_biz}",
                    params,
                )
                pipeline_value = float(cur.fetchone()[0])

                # Deals closed
                cur.execute(
                    f"SELECT COUNT(*), COALESCE(SUM(monetary_value), 0) FROM revenue_events "
                    f"WHERE event_type = 'deal_closed' "
                    f"AND created_at >= NOW() - INTERVAL '%s days' {where_biz}",
                    params,
                )
                row = cur.fetchone()
                deals_closed = row[0]
                closed_revenue = float(row[1])

                # Events by agent
                cur.execute(
                    f"SELECT agent_name, event_type, COUNT(*) FROM revenue_events "
                    f"WHERE created_at >= NOW() - INTERVAL '%s days' {where_biz} "
                    f"GROUP BY agent_name, event_type ORDER BY agent_name",
                    params,
                )
                agent_breakdown = {}
                for r in cur.fetchall():
                    agent = r[0]
                    if agent not in agent_breakdown:
                        agent_breakdown[agent] = {}
                    agent_breakdown[agent][r[1]] = r[2]

                # Content queued
                cur.execute(
                    "SELECT COUNT(*) FROM content_queue WHERE status = 'queued'"
                )
                content_queued = cur.fetchone()[0]

                cur.execute(
                    "SELECT COUNT(*) FROM content_queue WHERE status = 'published'"
                )
                content_published = cur.fetchone()[0]

                return {
                    "period_days": days,
                    "business": business_key or "all",
                    "prospects_created": prospects,
                    "emails_sent": emails_sent,
                    "pipeline_value": pipeline_value,
                    "deals_closed": deals_closed,
                    "closed_revenue": closed_revenue,
                    "conversion_rate": round(deals_closed / max(prospects, 1) * 100, 1),
                    "agent_breakdown": agent_breakdown,
                    "content_queued": content_queued,
                    "content_published": content_published,
                }
    except Exception as e:
        logging.error(f"[Revenue] Summary query failed: {e}")
        return {"error": str(e)}


def get_daily_metrics(business_key: Optional[str] = None, days: int = 7) -> list:
    """Get daily revenue metrics for trend analysis."""
    if _db_context is None:
        return []

    try:
        with _db_context() as conn:
            with conn.cursor() as cur:
                where_biz = "AND business_key = %s" if business_key else ""
                params = (days, business_key) if business_key else (days,)

                cur.execute(
                    f"SELECT DATE(created_at) as day, event_type, COUNT(*), COALESCE(SUM(monetary_value), 0) "
                    f"FROM revenue_events "
                    f"WHERE created_at >= NOW() - INTERVAL '%s days' {where_biz} "
                    f"GROUP BY DATE(created_at), event_type "
                    f"ORDER BY day DESC",
                    params,
                )
                rows = cur.fetchall()
                daily = {}
                for r in rows:
                    day = str(r[0])
                    if day not in daily:
                        daily[day] = {}
                    daily[day][r[1]] = {"count": r[2], "value": float(r[3])}

                return [{"date": k, "metrics": v} for k, v in daily.items()]
    except Exception as e:
        logging.error(f"[Revenue] Daily metrics query failed: {e}")
        return []


def get_email_template_report(business_key: Optional[str] = None, days: int = 7) -> dict:
    """Get daily template usage and validation quality from revenue_events metadata."""
    if _db_context is None:
        return {"error": "Database not available"}

    try:
        with _db_context() as conn:
            with conn.cursor() as cur:
                where_biz = "AND business_key = %s" if business_key else ""
                params = (days, business_key) if business_key else (days,)

                cur.execute(
                    f"SELECT "
                    f"  COALESCE(metadata->>'template_key', ''), "
                    f"  COALESCE((metadata->>'template_valid')::boolean, false), "
                    f"  COUNT(*) "
                    f"FROM revenue_events "
                    f"WHERE event_type = 'email_template_applied' "
                    f"AND created_at >= NOW() - INTERVAL '%s days' {where_biz} "
                    f"GROUP BY 1, 2 "
                    f"ORDER BY 1",
                    params,
                )

                summary = {}
                total = 0
                invalid_total = 0
                for template_key, template_valid, count in cur.fetchall():
                    key = template_key or "unknown"
                    bucket = summary.setdefault(key, {"valid": 0, "invalid": 0, "total": 0})
                    if template_valid:
                        bucket["valid"] += count
                    else:
                        bucket["invalid"] += count
                        invalid_total += count
                    bucket["total"] += count
                    total += count

                cur.execute(
                    f"SELECT DATE(created_at) as day, COUNT(*) "
                    f"FROM revenue_events "
                    f"WHERE event_type = 'email_template_applied' "
                    f"AND created_at >= NOW() - INTERVAL '%s days' {where_biz} "
                    f"GROUP BY day "
                    f"ORDER BY day DESC",
                    params,
                )
                daily = [{"date": str(r[0]), "count": r[1]} for r in cur.fetchall()]

                return {
                    "status": "ok",
                    "period_days": days,
                    "business": business_key or "all",
                    "templates": summary,
                    "total_applied": total,
                    "invalid_total": invalid_total,
                    "invalid_rate_pct": round((invalid_total / total * 100), 2) if total else 0,
                    "daily": daily,
                }
    except Exception as e:
        logging.error(f"[Revenue] Email template report failed: {e}")
        return {"error": str(e)}
