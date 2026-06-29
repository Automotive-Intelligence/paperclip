"""Book'd river workflow — run entrypoints for the 6-agent fleet.

Mirrors the repo's CrewAI run pattern (see app.py::run_atlas_intel and the
_run_revops_crew helper) but keeps each Book'd run self-contained here so that
app.py only needs thin import + scheduler.add_job lines (minimizes collision
surface with the parallel RevOps PR that also edits app.py).

Each *_run() returns a string; app.py schedules it through _avo_wrap_run, which
persists the return value as the agent's run log and tracks memory + cost.

Cadences (per bookd_agent_fleet_spec_2026-06-24.md §1):
  marshall  CEO            weekly   (Mon)
  cole      Sales          daily + interval (business-hours windows)
  hayes     RevOps         interval (hourly business hours via app.py)
  sutton    Marketing      daily
  quinn     Customer Success  daily
  reid      Intelligence   daily

Stack notes:
  - CRM of record: Twenty (Book'd workspace), business_key="bookd".
  - Cold outbound: Instantly (meetbookd.com / powerbookd.com). NEVER bookd.cx.
  - Cole + Sutton outbound is HELD until mailbox warmup completes (~2026-07-06);
    they still run on wire-up to produce gated/HELD-flagged drafts.
  - Marshall, Reid, Quinn, and Hayes (reads) run immediately, no warmup needed.
"""

import logging

logging.getLogger(__name__)

BUSINESS_KEY = "bookd"
WORKSPACE_LABEL = "Twenty (Book'd workspace, bookd.twenty.com)"


def _kickoff(agent_symbol, description: str, expected_output: str) -> str:
    """Build a one-shot Crew around an agent + task and return its output string.

    Imports CrewAI lazily so importing this module never fails when the agent
    runtime is unavailable (e.g. lightweight test contexts).
    """
    from crewai import Crew, Task, Process

    task = Task(
        description=description,
        expected_output=expected_output,
        agent=agent_symbol,
    )
    crew = Crew(
        agents=[agent_symbol],
        tasks=[task],
        process=Process.sequential,
        memory=False,
        verbose=False,
    )
    return str(crew.kickoff())


# ── Marshall — CEO (weekly) ──────────────────────────────────────────────────

def marshall_run() -> str:
    from agents.bookd.marshall import marshall
    return _kickoff(
        marshall,
        description=(
            "Run your weekly principal cycle for Book'd. Read the last 7 days of fleet "
            "output (Cole/Sales, Hayes/RevOps, Sutton/Marketing, Quinn/CS, Reid/Intel), "
            "assess strategic alignment, identify the 1 to 3 decisions that genuinely "
            "need a founder this week, and produce the Weekly Principal Brief in Book'd "
            "founder voice. Dispatch to seats; do not dump a task list on Michael. "
            "Uphold the compliance + claims posture and the captive-carrier exclusion."
        ),
        expected_output=(
            "The Weekly Principal Brief: (1) State of Book'd in 3 lines, "
            "(2) fleet alignment check (one line per agent), (3) the 1 to 3 founder "
            "decisions with options + recommendation + what each unblocks, "
            "(4) strategic steers dispatched to named agents, (5) risks and watch-items."
        ),
    )


# ── Cole — Sales (daily + interval) ──────────────────────────────────────────

def cole_run() -> str:
    from agents.bookd.cole import cole
    return _kickoff(
        cole,
        description=(
            "Run your Book'd revenue cycle. Source real, observed DataMoon intent "
            "signal, segment against the captive-carrier exclusion list, and produce "
            "launch-ready warm-framed sequences (subject + body per step) in Book'd's "
            "peer-operator outbound voice that reference the observed signal directly. "
            "Self-check all 5 gates BEFORE anything goes live. Book'd mailboxes "
            "(meetbookd.com / powerbookd.com) are warming through ~2026-07-06, so mark "
            "every send-ready asset HELD-for-warmup until warmup completes, and NEVER "
            "send from the bookd.cx primary domain. Any customer-named claim is HELD "
            "until Ryan verifies it. No em-dashes."
        ),
        expected_output=(
            "Launch-ready outbound sequences with per-asset gate self-report "
            "(PASS / HELD-reason), the observed signal each references, and a short "
            "list of Michael-or-Ryan-hands items. If no observed signal is available "
            "this cycle, say so explicitly and do not fabricate a sequence (>=200 chars)."
        ),
    )


# ── Hayes — RevOps (interval) ────────────────────────────────────────────────

def hayes_run() -> str:
    """RevOps interval cycle: reply triage, segmentation, Twenty sync intent,
    deliverability telemetry, claims-ledger maintenance.

    Pulls a fresh slice of Twenty (Book'd) people so the prompt has real state,
    and folds in the Instantly deliverability read from pit_wall when a campaign
    exists. Returns a >=200-char digest even when nothing is live yet (warmup).
    """
    from agents.bookd.hayes import hayes
    from rivers.bookd.pit_wall import hayes_pit_wall_run

    # Deliverability telemetry (warmup-gated; never raises).
    try:
        pit = hayes_pit_wall_run()
    except Exception as e:
        pit = {"status": "error", "reason": str(e)}

    contacts = _fetch_recent_bookd_people(hours=2)
    if contacts:
        summary = "\n".join(f"- {_bookd_contact_line(c)}" for c in contacts[:25])
    else:
        summary = "(no new Book'd contacts in the last cycle — mailboxes still warming)"

    deliverability = pit.get("report") or f"deliverability: {pit.get('status')} ({pit.get('reason','')})"

    return _kickoff(
        hayes,
        description=(
            f"Run your RevOps interval cycle against {WORKSPACE_LABEL}.\n\n"
            f"NEW CONTACTS PULLED THIS CYCLE ({len(contacts)} found):\n{summary}\n\n"
            f"DELIVERABILITY TELEMETRY (Instantly Book'd):\n{deliverability}\n\n"
            "Classify any replies, segment new DataMoon signals against the "
            "captive-carrier exclusion list, note Twenty sync intent, audit per-mailbox "
            "deliverability, and maintain the claims ledger (flag candidates for Ryan; "
            "never GREENLIGHT alone). Book'd outbound is warmup-gated through ~2026-07-06. "
            "If zero new contacts arrived, do NOT produce a heartbeat-only log: report "
            "what you checked (source, window, count, last read) and next-cycle intent. "
            "Output >=200 chars."
        ),
        expected_output=(
            "RevOps state digest: replies classified, signals segmented, per-mailbox "
            "deliverability (with any warning state called out), claims-ledger changes, "
            "and anything held for human review (>=200 chars)."
        ),
    )


# ── Sutton — Marketing (daily) ───────────────────────────────────────────────

def sutton_run() -> str:
    from agents.bookd.sutton import sutton
    return _kickoff(
        sutton,
        description=(
            "Run your daily owned-channel marketing cycle for Book'd per the CMO "
            "Operating System. Produce publish-ready owned-channel assets (copy + "
            "scheduling target) and creative briefs for the Higgsfield production lane "
            "(NEVER Skool) in Book'd's operator-to-operator, anti-bad-program voice. "
            "Self-check all 5 gates including the HARD Ryan-verification compliance gate "
            "before anything ships: any testimonial, customer name, case reference, or "
            "insurance regulatory specific is HELD until Ryan signs off. Keep the "
            "bracketed verbatim-brand-line placeholder intact until the brand kit fills "
            "it. On-camera talent: either co-founder may appear (Ryan default, Michael "
            "not excluded). No pricing, no income claims, no em-dashes."
        ),
        expected_output=(
            "Publish-ready owned-channel assets + Higgsfield creative briefs, each with "
            "a gate self-report (PASS / HELD-reason), plus a short list of "
            "Ryan-verification and Michael/Ryan-hands items (>=200 chars)."
        ),
    )


# ── Quinn — Customer Success (daily) ─────────────────────────────────────────

def quinn_run() -> str:
    from agents.bookd.quinn import quinn
    contacts = _fetch_recent_bookd_people(hours=24)
    if contacts:
        snapshot = "\n".join(f"- {_bookd_contact_line(c)}" for c in contacts[:25])
    else:
        snapshot = "(no recent Book'd user records visible this cycle)"
    return _kickoff(
        quinn,
        description=(
            "Run your daily customer-success cycle for Book'd against "
            f"{WORKSPACE_LABEL}.\n\nRECENT USER RECORDS THIS CYCLE:\n{snapshot}\n\n"
            "Pull current user state, detect adoption friction, segment by stage "
            "(new / onboarding / activated / at-risk / churned), and draft the right CS "
            "motion per segment in operator-to-operator voice, queued for Ryan where "
            "customer-facing. Surface Ryan-gated proof-narrative candidates to Sutton and "
            "retention risk to Marshall. Never invent a customer interaction. Report only "
            "what the data actually shows. No pricing, no income claims, no em-dashes. "
            "If user data is missing or stale, say so. Output >=200 chars."
        ),
        expected_output=(
            "Daily CS digest: user-state breakdown by stage, adoption friction detected, "
            "at-risk saves drafted (queued for Ryan), proof-narrative candidates flagged "
            "HELD for Ryan, and anything held for human review (>=200 chars)."
        ),
    )


# ── Reid — Intelligence (daily) ──────────────────────────────────────────────

def reid_run() -> str:
    from agents.bookd.reid import reid
    return _kickoff(
        reid,
        description=(
            "Run your daily market-intelligence cycle for Book'd. Scan the "
            "insurance/AI-setter market, regulatory and compliance changes (A2P 10DLC / "
            "TCPA, state recording/DNC, agent licensing), and competitors (Smith.ai, "
            "AnswerConnect, other AI setters in insurance). Cite a source for every "
            "assertion; label inference as inference, never as fact. Synthesize a daily "
            "intelligence digest and route findings to the right agent (Cole / Hayes / "
            "Sutton / Quinn / Marshall). Compliance changes that affect outbound or "
            "claims are FLAGGED to Hayes and Sutton with the source. Never hand Sutton a "
            "named-competitor attack. No Book'd pricing, no income claims, no em-dashes."
        ),
        expected_output=(
            "Daily intelligence digest: Market / Regulatory-compliance (with affected "
            "agent named) / Competitive (reported as theirs) / So-what for Book'd / "
            "Flags routed. Every assertion sourced; inference labeled (>=200 chars)."
        ),
    )


# ── Twenty (Book'd) helpers ──────────────────────────────────────────────────

def _fetch_recent_bookd_people(hours: int) -> list:
    """Pull people created in the trailing N hours from the Book'd Twenty workspace.

    Returns [] on any failure (missing key, bad URL, non-200) so the agent still
    runs and produces a real "no new contacts" digest instead of a silent zero.
    """
    import datetime as _dt
    try:
        from tools.twenty import _workspace_config, _headers as _twenty_headers
        base_url, api_key = _workspace_config(BUSINESS_KEY)
    except (ImportError, ValueError) as e:
        logging.info("[bookd] _fetch_recent_bookd_people skipped: %s", e)
        return []
    cutoff = (
        _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(hours=hours)
    ).isoformat()
    import requests as _requests
    try:
        r = _requests.get(
            f"{base_url}/rest/people",
            headers=_twenty_headers(api_key),
            params={"filter": f"createdAt[gte]:{cutoff}", "limit": 50},
            timeout=15,
        )
        if not r.ok:
            logging.warning("[bookd] Twenty people fetch http=%s", r.status_code)
            return []
        return (r.json().get("data") or {}).get("people") or []
    except Exception as e:
        logging.warning("[bookd] Twenty people fetch raised: %s", e)
        return []


def _bookd_contact_line(p: dict) -> str:
    name = (p.get("name") or {})
    emails = (p.get("emails") or {})
    return (
        f"{(name.get('firstName') or '').strip()} {(name.get('lastName') or '').strip()} "
        f"<{emails.get('primaryEmail','')}>"
        f" — created {p.get('createdAt','')}"
    ).strip()
