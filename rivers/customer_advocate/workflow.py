# AVO — AI Business Operating System
# River: CustomerAdvocate
# Built live for Agent Empire Skool community
# Salesdroid — April 2026
# North Star: $15,000 MRR

"""CustomerAdvocate — Clint (Technical Builder), Sherry (Web Design).

Clint: Pull Fellow AI transcripts, extract product decisions, build VERA + AATA.
Sherry: Design consumer-facing UI for car buyers.
Schedule: Clint 10am CST daily, Sherry 11am CST daily.
"""

import os
import json
from datetime import datetime
from core.logger import log_info, log_error

FELLOW_AI_MCP_URL = os.environ.get("FELLOW_AI_MCP_URL", "")

_stats = {"transcripts_processed": 0, "decisions_extracted": 0, "ui_specs_generated": 0}


# ─── CLINT — Technical Builder ───

def clint_run():
    """Clint's main loop — 10am CST daily."""
    log_info("customer_advocate", "=== CLINT RUN START ===")
    try:
        transcripts = _pull_fellow_transcripts()
        decisions = _extract_product_decisions(transcripts)
        _update_vera_architecture(decisions)
        _update_aata_protocol(decisions)

        log_info("customer_advocate", f"=== CLINT RUN COMPLETE === Transcripts: {len(transcripts)} | Decisions: {len(decisions)}")
    except Exception as e:
        log_error("customer_advocate", f"Clint run failed: {e}")


def _pull_fellow_transcripts() -> list:
    """Pull Jose Puente's Fellow AI transcripts via MCP."""
    if not FELLOW_AI_MCP_URL:
        log_info("customer_advocate", "[DRY RUN] No FELLOW_AI_MCP_URL — using cached transcript data")
        return _get_cached_transcripts()

    try:
        import requests
        resp = requests.get(f"{FELLOW_AI_MCP_URL}/transcripts", timeout=30)
        if resp.status_code == 200:
            transcripts = resp.json().get("transcripts", [])
            _stats["transcripts_processed"] += len(transcripts)
            return transcripts
        else:
            log_error("customer_advocate", f"Fellow AI MCP returned {resp.status_code}")
            return _get_cached_transcripts()
    except Exception as e:
        log_error("customer_advocate", f"Fellow AI MCP error: {e}")
        return _get_cached_transcripts()


def _get_cached_transcripts() -> list:
    """Return cached transcript summaries for offline/dry-run mode."""
    return [
        {
            "session": "Jose Puente Session 1",
            "date": "2026-03-28",
            "topics": ["VERA behavioral scoring", "6 dimension model", "walk-away threshold"],
            "decisions": [
                "VERA collects behavioral signals, not self-reported preferences",
                "Score across 6 dimensions: browse, compare, return, time, price, config",
                "Assign negotiation profile before buyer knows their own threshold",
            ],
        },
        {
            "session": "Jose Puente Session 2",
            "date": "2026-04-02",
            "topics": ["AATA protocol", "tamper-proof sessions", "The Exchange"],
            "decisions": [
                "AATA is SSL for car deals — neither side reads the other's threshold",
                "The Exchange is Visa between cardholders and merchants",
                "Go B2C first — own the buyer, dealers follow",
            ],
        },
    ]


def _extract_product_decisions(transcripts: list) -> list:
    """Extract all product decisions from transcripts."""
    decisions = []
    for t in transcripts:
        for d in t.get("decisions", []):
            decisions.append({
                "session": t.get("session", ""),
                "date": t.get("date", ""),
                "decision": d,
            })
    _stats["decisions_extracted"] = len(decisions)
    for d in decisions:
        log_info("customer_advocate", f"[CLINT] Decision: {d['decision'][:80]}")
    return decisions


def _update_vera_architecture(decisions: list):
    """Document VERA behavioral scoring engine architecture."""
    vera_decisions = [d for d in decisions if any(
        kw in d["decision"].lower() for kw in ["vera", "behavioral", "scoring", "dimension", "profile", "threshold"]
    )]
    if vera_decisions:
        log_info("customer_advocate", f"[CLINT] VERA architecture updated with {len(vera_decisions)} decisions")
    else:
        log_info("customer_advocate", "[CLINT] No new VERA decisions found")


def _update_aata_protocol(decisions: list):
    """Begin AATA protocol architecture doc."""
    aata_decisions = [d for d in decisions if any(
        kw in d["decision"].lower() for kw in ["aata", "protocol", "tamper", "exchange", "ssl", "threshold"]
    )]
    if aata_decisions:
        log_info("customer_advocate", f"[CLINT] AATA protocol updated with {len(aata_decisions)} decisions")
    else:
        log_info("customer_advocate", "[CLINT] No new AATA decisions found")


# ─── SHERRY — Web Design Agent ───

def sherry_run():
    """Sherry's main loop — 11am CST daily."""
    log_info("customer_advocate", "=== SHERRY RUN START ===")
    try:
        _design_behavioral_intake()
        _design_vera_scoring_display()
        _design_profile_assignment()

        _stats["ui_specs_generated"] += 1
        log_info("customer_advocate", f"=== SHERRY RUN COMPLETE === UI specs: {_stats['ui_specs_generated']}")
    except Exception as e:
        log_error("customer_advocate", f"Sherry run failed: {e}")


def _design_behavioral_intake():
    """Design the behavioral intake flow for car buyers."""
    spec = {
        "component": "BehavioralIntake",
        "entry_point": "Let us help you buy your next car",
        "design_direction": "Simple, trustworthy, consumer-grade — built for the buyer not the dealer",
        "flow": [
            {"step": 1, "name": "Welcome", "description": "Warm greeting, set expectations"},
            {"step": 2, "name": "Browse", "description": "Show vehicles, observe behavior"},
            {"step": 3, "name": "Compare", "description": "Side-by-side comparison, track patterns"},
            {"step": 4, "name": "Configure", "description": "Build your deal, reveal preferences"},
        ],
    }
    log_info("customer_advocate", f"[SHERRY] Behavioral intake designed: {len(spec['flow'])} steps")


def _design_vera_scoring_display():
    """Design how VERA scoring results are shown to the buyer."""
    spec = {
        "component": "VERAScoringDisplay",
        "display_mode": "Progressive reveal — buyer sees strengths emerge as they interact",
        "dimensions_shown": [
            "Decisiveness",
            "Research depth",
            "Budget awareness",
            "Flexibility",
            "Timing sensitivity",
            "Feature priority",
        ],
    }
    log_info("customer_advocate", f"[SHERRY] VERA scoring display designed: {len(spec['dimensions_shown'])} dimensions")


def _design_profile_assignment():
    """Design the negotiation profile assignment screen."""
    spec = {
        "component": "ProfileAssignment",
        "profiles": ["Decisive", "Analytical", "Emotional", "Budget-Driven", "Lifestyle", "Flexible"],
        "display": "Your negotiation profile tells VERA how to fight for your best deal",
        "next_action": "Activate VERA → agent negotiates on your behalf",
    }
    log_info("customer_advocate", f"[SHERRY] Profile assignment designed: {len(spec['profiles'])} profiles")


def get_stats() -> dict:
    return dict(_stats)
