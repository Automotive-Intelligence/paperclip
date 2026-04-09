"""
agents/coo/coo_agent.py - COO Command Agent

Operational commander of all three businesses. Monitors execution,
flags gaps, and generates daily ops reports. Does not set strategy.

Schedule: Daily at 7:45am CST — runs before all other agents.
"""

import datetime
import json
import logging
from typing import Any, Dict, List

from services.database import execute_query, fetch_all

logger = logging.getLogger(__name__)

# All agents that should run daily
EXPECTED_AGENTS = [
    # CEOs
    "alex", "dek", "michael_meta",
    # Sales
    "tyler", "marcus", "ryan_data",
    # Marketing
    "zoe", "sofia", "chase",
    # Client Success
    "jennifer", "carlos",
    # Specialists
    "nova", "atlas", "phoenix",
    # RevOps
    "randy", "brenda", "darrell",
    # Agent Empire
    "debra", "wade_ae", "tammy_ae", "sterling",
    # CustomerAdvocate
    "clint", "sherry",
]

SALES_AGENTS = ["tyler", "marcus", "ryan_data"]

AGENT_BUSINESS_MAP = {
    "alex": "aiphoneguy", "tyler": "aiphoneguy", "zoe": "aiphoneguy", "jennifer": "aiphoneguy",
    "randy": "aiphoneguy",
    "dek": "callingdigital", "marcus": "callingdigital", "sofia": "callingdigital",
    "carlos": "callingdigital", "nova": "callingdigital", "brenda": "callingdigital",
    "michael_meta": "autointelligence", "ryan_data": "autointelligence",
    "chase": "autointelligence", "atlas": "autointelligence", "phoenix": "autointelligence",
    "darrell": "autointelligence",
    "debra": "agentempire", "wade_ae": "agentempire", "tammy_ae": "agentempire",
    "sterling": "agentempire",
    "clint": "customeradvocate", "sherry": "customeradvocate",
}


def _fetch_agent_logs_24h() -> List[Dict[str, Any]]:
    """Pull last 24 hours of agent logs."""
    rows = fetch_all(
        "SELECT agent_name, log_type, run_date, LENGTH(content) as content_len, created_at "
        "FROM agent_logs WHERE created_at >= NOW() - INTERVAL '24 hours' "
        "ORDER BY created_at DESC"
    )
    return [
        {
            "agent_name": r[0],
            "log_type": r[1],
            "run_date": str(r[2]),
            "content_len": r[3],
            "created_at": str(r[4]),
        }
        for r in rows
    ]


def _fetch_crm_push_counts_24h() -> Dict[str, Dict[str, int]]:
    """Get CRM push counts per agent in last 24 hours."""
    rows = fetch_all(
        "SELECT agent_name, status, COUNT(*) "
        "FROM crm_push_logs WHERE created_at >= NOW() - INTERVAL '24 hours' "
        "GROUP BY agent_name, status"
    )
    counts: Dict[str, Dict[str, int]] = {}
    for agent, status, count in rows:
        if agent not in counts:
            counts[agent] = {"created": 0, "duplicate_skipped": 0, "error": 0}
        counts[agent][status] = counts[agent].get(status, 0) + count
    return counts


def _fetch_crm_push_errors_24h() -> int:
    """Count CRM push errors in last 24 hours."""
    rows = fetch_all(
        "SELECT COUNT(*) FROM crm_push_logs "
        "WHERE status NOT IN ('created', 'duplicate_skipped') "
        "AND created_at >= NOW() - INTERVAL '24 hours'"
    )
    return rows[0][0] if rows else 0


def _fetch_icp_discards_24h() -> Dict[str, int]:
    """Get ICP discard counts per agent in last 24 hours."""
    rows = fetch_all(
        "SELECT agent_name, COUNT(*) FROM icp_discards "
        "WHERE created_at >= NOW() - INTERVAL '24 hours' "
        "GROUP BY agent_name"
    )
    return {r[0]: r[1] for r in rows}


def _check_agents_ran(logs: List[Dict]) -> Dict[str, Any]:
    """Check which agents ran vs missed in last 24 hours."""
    agents_that_ran = set(log["agent_name"] for log in logs)
    ran = [a for a in EXPECTED_AGENTS if a in agents_that_ran]
    missed = [a for a in EXPECTED_AGENTS if a not in agents_that_ran]
    return {"ran": ran, "missed": missed}


def run_coo_command() -> Dict[str, Any]:
    """
    Daily COO ops check. Runs at 7:45 AM CST before all other agents.

    1. Pull last 24h logs
    2. Check each agent ran
    3. Check CRM push counts
    4. Check for errors
    5. Generate ops report
    6. Log to PostgreSQL
    7. Return report dict (exposed at GET /ops-report)
    """
    try:
        logs = _fetch_agent_logs_24h()
        agent_status = _check_agents_ran(logs)
        crm_counts = _fetch_crm_push_counts_24h()
        crm_errors = _fetch_crm_push_errors_24h()
        icp_discards = _fetch_icp_discards_24h()

        # ── Build per-business summary ──
        business_summary = {}
        for business in ["aiphoneguy", "callingdigital", "autointelligence", "agentempire", "customeradvocate"]:
            biz_agents = [a for a, b in AGENT_BUSINESS_MAP.items() if b == business]
            biz_ran = [a for a in biz_agents if a in agent_status["ran"]]
            biz_missed = [a for a in biz_agents if a in agent_status["missed"]]
            biz_sales = [a for a in SALES_AGENTS if AGENT_BUSINESS_MAP.get(a) == business]
            prospects_created = sum(
                crm_counts.get(a, {}).get("created", 0) for a in biz_sales
            )
            business_summary[business] = {
                "agents_ran": biz_ran,
                "agents_missed": biz_missed,
                "prospects_created": prospects_created,
            }

        # ── Build alerts ──
        alerts = []

        for agent in agent_status["missed"]:
            alerts.append({
                "level": "ALERT",
                "message": f"Agent '{agent}' missed scheduled run in last 24h",
            })

        for agent in SALES_AGENTS:
            created = crm_counts.get(agent, {}).get("created", 0)
            if created == 0:
                alerts.append({
                    "level": "ALERT",
                    "message": f"Sales agent '{agent}' pushed zero prospects in last 24h",
                })

        if crm_errors >= 3:
            alerts.append({
                "level": "ALERT",
                "message": f"CRM push errors hit {crm_errors} in last 24h (threshold: 3)",
            })

        for agent, count in icp_discards.items():
            if count >= 3:
                alerts.append({
                    "level": "ALERT",
                    "message": f"Agent '{agent}' had {count} ICP discards in last 24h",
                })

        # ── Priority recommendation ──
        if agent_status["missed"]:
            priority = f"Investigate why {len(agent_status['missed'])} agent(s) missed their run: {', '.join(agent_status['missed'])}"
        elif crm_errors >= 3:
            priority = f"CRM push errors elevated ({crm_errors}). Check API keys and rate limits."
        elif any(crm_counts.get(a, {}).get("created", 0) == 0 for a in SALES_AGENTS):
            zero_agents = [a for a in SALES_AGENTS if crm_counts.get(a, {}).get("created", 0) == 0]
            priority = f"Zero prospects from {', '.join(zero_agents)}. Review Tavily search quality and ICP guardrails."
        else:
            total = sum(crm_counts.get(a, {}).get("created", 0) for a in SALES_AGENTS)
            priority = f"All systems nominal. {total} prospects created across sales agents. Monitor conversion rates."

        # ── Assemble report ──
        report = {
            "report_type": "coo_daily_ops",
            "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "agents_ran": len(agent_status["ran"]),
            "agents_missed": len(agent_status["missed"]),
            "missed_agents": agent_status["missed"],
            "business_summary": business_summary,
            "crm_push_counts": crm_counts,
            "crm_errors_24h": crm_errors,
            "icp_discards_24h": icp_discards,
            "alerts": alerts,
            "alert_count": len(alerts),
            "priority_recommendation": priority,
        }

        # ── Persist to PostgreSQL ──
        execute_query(
            "INSERT INTO agent_logs (agent_name, log_type, run_date, content) "
            "VALUES (%s, %s, %s, %s)",
            ("command", "ops_report", datetime.date.today(), json.dumps(report, default=str)),
        )
        logger.info("[COO] Command ops report generated. Alerts: %d", len(alerts))

        return report

    except Exception as e:
        logger.error("[COO] Command agent failed: %s", e)
        return {
            "report_type": "coo_daily_ops",
            "status": "error",
            "error": str(e),
            "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
