"""services/sdr_first_touch.py -- SP4: autonomous first touch, NO approval queue.

Owner decision 2026-08-07 (Michael): "no approval queue -- install guardrails in
context and be intelligent enough that we don't need approval queues." Recorded
as the OWNER AMENDMENT to principle 17 (docs/superpowers/specs/
sdr-desk-principles.md) and specced in 2026-08-07-sdr-first-touch-design.md.
READ BOTH before changing anything here.

The queue is replaced by CONSTRUCTION, not trust. Per candidate, in order, all
fail-closed -- the first gate that fails records an exception line and moves on:

  source lock      only `SDR-verified` opportunities (gate-PASS built) are read
  dedup            a linked "FIRST TOUCH" note on the opportunity = already sent
  window           Mon-Fri 08:00-17:30 CT only
  cap              5 sends/day/brand, counted from brand_send_audit (constant,
                   NOT config -- raising it is an owner conversation)
  recipient        email published on the company's OWN site (homepage or
                   /contact), domain-matched; never a broker address
  suppression      services/suppression union check (ledger+customers+DNC)
  copy             fixed template + verified-fact slots; deterministic validator
                   proves the variable text carries no digits, no pricing
                   vocabulary, no em-dash, and only evidence-backed slot values
  scrutineering    the AVO Scrutineering Gate's Stage-1 scorer, in-line:
                   Tier-0 kill-switches + 0-5 dims, PASS needs every dim >= 4
                   and avg >= 4.5; scorer down = BLOCK (never fail-open);
                   blocked drafts die as digest exceptions, no rewrite loop
  kill switch      SDR_FIRST_TOUCH_ENABLED=1 required for any live send
  send             tools/brand_send.send_as_brand (audited; still gated by
                   SEND_AUTHORIZED_MAILBOXES) as the brand's real identity
  receipt          "FIRST TOUCH sent <date>" note linked to the opportunity
                   (the durable dedup marker)

Permanent scope exclusions (holds, not approvals): no pricing content ever;
angry/negative replies are never answered by machine; spend untouched.
commit=False (default) is a full dry-run: evaluates every gate, sends nothing,
writes nothing.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import requests

logger = logging.getLogger(__name__)

_CT = ZoneInfo("America/Chicago")
DAILY_CAP_PER_BRAND = 5           # deliberate constant; see module docstring
_SEAT = "sdr_first_touch"

# desk brand -> (twenty runtime key, send identity label, brand display name,
#                 rebuild_motion_allowed). The v1 template pitches a WEBSITE
#                 DEFECT -- that is Worship Digital's motion. An AvI or Book'd
#                 identity making a website-rebuild pitch is a brand-scope
#                 mismatch (their SDR-verified rebuild opportunities exist as
#                 pipeline visibility, not as their outbound motion), so those
#                 brands' candidates die as `brand_motion_mismatch` until a
#                 per-brand motion template is specced and reviewed.
_BRANDS: Dict[str, Tuple[str, str, str, bool]] = {
    "wd":    ("callingdigital",   "wd",    "Worship Digital", True),
    "avi":   ("autointelligence", "avi",   "Automotive Intelligence", False),
    "bookd": ("bookd",            "bookd", "Book'd", False),
}

# The ONLY defect claims this engine may make, keyed by the gate's verified
# defect kind (parsed from the SDR-verified opportunity name). Adding a kind
# here requires the gate to actually verify it first.
DEFECT_PHRASES: Dict[str, str] = {
    "pinch_zoom_blocked": "your site blocks pinch-to-zoom on phones, which makes it hard for mobile visitors to read",
    "site_down": "your site at {domain} is not loading right now",
    "cert_warning": "your site is showing a security-certificate warning",
    "no_contact_path": "there is no phone number or contact form on your homepage",
    "slow_load": "your homepage takes noticeably long to start loading",
}

_SUBJECT_TMPL = "about {domain}"

_BODY_TMPL = """Hi {greeting},

I looked at {domain} recently, honest reason below, and noticed one thing worth a minute of your time: {defect_phrase}.

I am Michael Rodriguez. I run {brand_name} and I check sites like yours for exactly this kind of thing. No charge and no strings: if you want, I will send over a short, specific note on what I found and what fixing it would take. If it is already handled, even better, and you will hear nothing else from me.

Worth a look?

If you would rather not hear from me, reply no thanks and that is the end of it.

Michael Rodriguez
{brand_name}
"""

_FORBIDDEN = re.compile(r"price|pricing|quote|\$|cost|discount|%|\bfee\b", re.I)
_EMDASH = "—"


# --------------------------------------------------------------------------- twenty reads

def _twenty(runtime_key: str):
    from tools.twenty import _headers, _workspace_config
    base, key = _workspace_config(runtime_key)
    return base, _headers(key)


def _sdr_opportunities(runtime_key: str) -> List[dict]:
    """All SDR-verified opportunities (name marker) with their company."""
    base, h = _twenty(runtime_key)
    out, cursor = [], None
    for _ in range(15):
        url = f"{base}/rest/opportunities?limit=60" + (f"&starting_after={cursor}" if cursor else "")
        r = requests.get(url, headers=h, timeout=20)
        r.raise_for_status()
        d = r.json().get("data", {}).get("opportunities") or []
        out += [o for o in d if "SDR-verified" in (o.get("name") or "")]
        pi = r.json().get("pageInfo") or {}
        if not pi.get("hasNextPage"):
            break
        cursor = pi.get("endCursor")
    return out


def _company_domain(runtime_key: str, company_id: str) -> Tuple[str, str]:
    """(company_name, bare_domain) for the opportunity's company."""
    base, h = _twenty(runtime_key)
    r = requests.get(f"{base}/rest/companies/{company_id}", headers=h, timeout=20)
    r.raise_for_status()
    c = (r.json().get("data") or {}).get("company") or {}
    raw = ((c.get("domainName") or {}).get("primaryLinkUrl")) or ""
    host = raw.strip().lower()
    host = host.split("://")[-1].split("/")[0]
    host = host[4:] if host.startswith("www.") else host
    return (c.get("name") or "", host)


def _already_touched(runtime_key: str, opportunity_id: str) -> bool:
    base, h = _twenty(runtime_key)
    r = requests.get(f"{base}/rest/noteTargets", headers=h, timeout=20,
                     params={"filter": f"targetOpportunityId[eq]:{opportunity_id}", "limit": 20})
    if not r.ok:
        return True  # cannot verify dedup -> fail closed, do not send
    for t in (r.json().get("data") or {}).get("noteTargets") or []:
        rn = requests.get(f"{base}/rest/notes/{t.get('noteId')}", headers=h, timeout=20)
        title = (((rn.json().get("data") or {}).get("note") or {}).get("title") or "") if rn.ok else ""
        if title.startswith("FIRST TOUCH"):
            return True
    return False


# --------------------------------------------------------------------------- recipient discovery

def _published_email(domain: str) -> Optional[str]:
    """An email the company itself publishes (homepage or /contact), same
    registrable domain (subdomain-tolerant). Never any other source."""
    pat = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
    root = ".".join(domain.split(".")[-2:])
    for path in ("", "/contact", "/contact-us", "/about"):
        try:
            r = requests.get(f"https://{domain}{path}", timeout=12,
                             headers={"User-Agent": "Mozilla/5.0"})
            if not r.ok:
                continue
            for m in pat.findall(r.text):
                if m.lower().split("@")[1].endswith(root) and not m.lower().startswith(
                        ("noreply", "no-reply", "wordpress", "example", "sentry")):
                    return m
        except Exception:
            continue
    return None


# --------------------------------------------------------------------------- copy + validation

def _defect_kind(opportunity_name: str) -> Optional[str]:
    m = re.search(r"SDR-verified:\s*([a-z_]+)", opportunity_name)
    return m.group(1) if m else None


def compose(company_name: str, domain: str, defect_kind: str, brand_name: str) -> Tuple[str, str]:
    phrase = DEFECT_PHRASES[defect_kind].format(domain=domain)
    greeting = company_name.strip() or "there"
    subject = _SUBJECT_TMPL.format(domain=domain)
    body = _BODY_TMPL.format(greeting=greeting, domain=domain,
                             defect_phrase=phrase, brand_name=brand_name)
    return subject, body


def validate(subject: str, body: str, *, company_name: str, domain: str,
             defect_kind: str, brand_name: str) -> Optional[str]:
    """Deterministic proof the outgoing text is template + evidence, nothing
    else. Returns a reason string on failure, None when clean."""
    if _EMDASH in body or _EMDASH in subject:
        return "em_dash"
    if defect_kind not in DEFECT_PHRASES:
        return "unknown_defect_kind"
    # Rebuild what the template SHOULD produce; any drift = tampering.
    exp_subject, exp_body = compose(company_name, domain, defect_kind, brand_name)
    if subject != exp_subject or body != exp_body:
        return "copy_drift"
    # The variable slots may not smuggle pricing/number claims.
    for slot in (company_name, domain):
        if _FORBIDDEN.search(slot or ""):
            return "forbidden_vocab_in_slot"
        if re.search(r"\d", slot or "") and slot != domain:  # domains may have digits
            return "digits_in_slot"
    return None


_SCRUTINEER_SYSTEM = (
    "You are the AVO Scrutineering Gate, the maker-checker every outbound "
    "artifact passes before it leaves AVO. Score the draft 0-5 on each "
    "dimension. Verdict PASS requires EVERY dimension >= 4 AND average >= 4.5. "
    "Tier-0 kill-switches (any true = automatic BLOCK regardless of scores): "
    "fabricated or unevidenced claim; any pricing/cost content; manipulation "
    "or false urgency; wrong or generic audience (could go to another company "
    "unchanged beyond the name/domain/defect slots); guru or hype voice. "
    "Return JSON only: {\"verdict\": \"PASS\"|\"BLOCK\", \"kill_switch\": str|null, "
    "\"scores\": {\"truth\": n, \"specificity\": n, \"voice\": n, \"respect\": n, "
    "\"clarity\": n}, \"reason\": str}.")


def _scrutineer(subject: str, body: str, company_name: str, domain: str,
                defect_kind: str) -> Tuple[bool, str]:
    """Stage-1 of the Scrutineering Gate, in-line. Returns (blocked, reason).
    Fail-safe mirrors the gate orchestrator: scorer down or off-contract ->
    BLOCK, never fail-open."""
    from services.studio_social_llm import llm_json
    user = (f"Channel: 1:1 cold email, sender is the brand's real principal. "
            f"Recipient: {company_name} ({domain}). The ONLY verified fact "
            f"available is a '{defect_kind}' website defect confirmed by the "
            f"verification gate.\nSUBJECT: {subject}\nBODY:\n{body}")
    try:
        v = llm_json(_SCRUTINEER_SYSTEM, user, max_tokens=400)
        verdict = str(v.get("verdict", "")).upper()
        scores = v.get("scores") or {}
        vals = [float(scores.get(k, 0)) for k in
                ("truth", "specificity", "voice", "respect", "clarity")]
        if verdict != "PASS" or v.get("kill_switch"):
            return True, f"kill={v.get('kill_switch')} {str(v.get('reason',''))[:150]}"
        if not vals or min(vals) < 4 or (sum(vals) / len(vals)) < 4.5:
            return True, f"scores_below_bar {scores}"
        return False, str(v.get("reason", ""))[:150]
    except Exception as e:
        return True, f"scorer_down_block: {type(e).__name__}"


# --------------------------------------------------------------------------- hard gates

def _in_window(now: Optional[datetime] = None) -> bool:
    now = now or datetime.now(_CT)
    return now.weekday() < 5 and (8, 0) <= (now.hour, now.minute) <= (17, 30)


def _sends_today(identity: str) -> int:
    from services.database import fetch_all
    rows = fetch_all(
        "SELECT COUNT(*) AS n FROM brand_send_audit "
        "WHERE from_identity = %s AND seat = %s AND outcome = 'sent' "
        "AND created_at >= date_trunc('day', now() AT TIME ZONE 'America/Chicago') "
        "AT TIME ZONE 'America/Chicago'",
        (identity, _SEAT))
    row = rows[0] if rows else {}
    return int(row.get("n") if isinstance(row, dict) else row[0]) if rows else 0


def _suppressed(email: str, desk_brand: str) -> bool:
    try:
        from services.suppression import is_suppressed
        return is_suppressed(email, desk_brand)
    except Exception:
        return True  # cannot check -> fail closed


# --------------------------------------------------------------------------- the engine

def run_first_touch(commit: bool = False, now: Optional[datetime] = None) -> dict:
    counts = {"considered": 0, "sent": 0, "exceptions": 0, "skipped_touched": 0}
    lines: List[str] = []
    enabled = os.getenv("SDR_FIRST_TOUCH_ENABLED", "").strip() == "1"

    def exc(brand: str, who: str, reason: str) -> None:
        counts["exceptions"] += 1
        lines.append(f"- {brand}: {who} EXCEPTION {reason}")

    for desk, (runtime_key, identity_label, brand_name, motion_ok) in _BRANDS.items():
        try:
            opps = _sdr_opportunities(runtime_key)
        except Exception as e:
            exc(desk, "(read)", f"twenty_read_failed: {type(e).__name__}")
            continue
        for o in opps:
            counts["considered"] += 1
            oid, oname = o.get("id"), o.get("name") or ""
            company_id = o.get("companyId") or ""
            try:
                if _already_touched(runtime_key, oid):
                    counts["skipped_touched"] += 1
                    lines.append(f"- {desk}: {oname[:50]} already touched, skip")
                    continue
                if not _in_window(now):
                    exc(desk, oname[:50], "outside_window")
                    continue
                if not motion_ok:
                    exc(desk, oname[:50], "brand_motion_mismatch (rebuild pitch is WD's motion)")
                    continue
                kind = _defect_kind(oname)
                if not kind or kind not in DEFECT_PHRASES:
                    exc(desk, oname[:50], f"unmapped_defect:{kind}")
                    continue
                company_name, domain = _company_domain(runtime_key, company_id)
                if not domain:
                    exc(desk, oname[:50], "no_domain")
                    continue
                email = _published_email(domain)
                if not email:
                    exc(desk, oname[:50], "no_verified_email")
                    continue
                if _suppressed(email, desk):
                    exc(desk, email, "suppressed")
                    continue
                subject, body = compose(company_name, domain, kind, brand_name)
                v = validate(subject, body, company_name=company_name, domain=domain,
                             defect_kind=kind, brand_name=brand_name)
                if v:
                    exc(desk, email, f"validator:{v}")
                    continue
                blocked, why = _scrutineer(subject, body, company_name, domain, kind)
                if blocked:
                    exc(desk, email, f"scrutineering_block:{why[:80]}")
                    continue
                if not commit:
                    lines.append(f"- {desk}: WOULD SEND to {email} ({oname[:50]})")
                    continue
                if not enabled:
                    exc(desk, email, "kill_switch_off (SDR_FIRST_TOUCH_ENABLED != 1)")
                    continue
                from tools.brand_send import send_as_brand
                if _sends_today_safe(identity_label) >= DAILY_CAP_PER_BRAND:
                    exc(desk, email, "cap_reached")
                    continue
                r = send_as_brand(to=email, subject=subject, body=body,
                                  from_identity=identity_label, seat=_SEAT)
                if r.sent:
                    counts["sent"] += 1
                    lines.append(f"- {desk}: SENT to {email} ({oname[:50]})")
                    _mark_touched(runtime_key, oid, email)
                else:
                    exc(desk, email, f"send_{r.outcome}")
            except Exception as e:
                exc(desk, oname[:50], f"{type(e).__name__}: {e}")
    return {**counts, "digest": "\n".join(lines) or "(no SDR-verified opportunities)"}


def _sends_today_safe(identity_label: str) -> int:
    """Cap check that fails CLOSED: if the audit store is unreachable we treat
    the cap as reached rather than risk over-sending."""
    from tools.brand_send import BRAND_IDENTITIES
    identity = BRAND_IDENTITIES.get(identity_label, identity_label)
    try:
        return _sends_today(identity)
    except Exception:
        return DAILY_CAP_PER_BRAND


def _mark_touched(runtime_key: str, opportunity_id: str, email: str) -> None:
    """The durable dedup marker. Best-effort note; a failure here is logged --
    the audit row still exists and _already_touched fails closed on read errors."""
    try:
        base, h = _twenty(runtime_key)
        r = requests.post(f"{base}/rest/notes", headers=h, timeout=20,
                          json={"title": f"FIRST TOUCH sent {datetime.now(_CT).date()}",
                                "bodyV2": {"markdown": f"Autonomous first touch to {email} "
                                           f"via send-as-brand rail (seat {_SEAT})."}})
        nid = ((r.json().get("data") or {}).get("createNote") or {}).get("id") if r.ok else None
        if nid:
            requests.post(f"{base}/rest/noteTargets", headers=h, timeout=20,
                          json={"noteId": nid, "targetOpportunityId": opportunity_id})
    except Exception as e:
        logger.warning("[first-touch] dedup note failed for %s: %s", opportunity_id, e)
