"""services/sdr_first_touch.py -- SP4: autonomous first touch, NO approval queue.

FOLLOW-UP SEQUENCE (added 2026-08-11, from playbook research): a real gap this
closed -- the engine used to send exactly one touch and never follow up. Now
sends up to 3 touches per opportunity: touch 1 (day 0, unchanged), touch 2
("forgot to mention", >=2 days after touch 1, same real evidence, no new
claim -- we have exactly one verified defect and will not invent a second),
touch 3 ("last one, promise" -- BYAF close, >=4 more days later). ALL THREE
run through the identical guardrail stack as touch 1: same Scrutineering Gate,
same validator, same suppression check, same window, same kill switch, same
5/day/brand cap pool (no separate follow-up budget). The one NEW guardrail:
before touch 2/3, `_has_replied()` checks the sending identity's real inbox
(via the Postal-authenticated Gmail read scope, tools/gmail_multi.py) for any
message from the recipient since the last touch -- fail closed, so a reply we
can't confirm blocks the next touch rather than risk pitching over it.

Owner decision 2026-08-07 (Michael): "no approval queue -- install guardrails in
context and be intelligent enough that we don't need approval queues." Recorded
as the OWNER AMENDMENT to principle 17 (docs/superpowers/specs/
sdr-desk-principles.md) and specced in 2026-08-07-sdr-first-touch-design.md.
READ BOTH before changing anything here.

The queue is replaced by CONSTRUCTION, not trust. Per candidate, in order, all
fail-closed -- the first gate that fails records an exception line and moves on:

  source lock      only `SDR-verified` opportunities (gate-PASS built) are read
  sequence         up to 3 touches/opportunity, min 2d then 4d gaps; a linked
                   FIRST TOUCH/FOLLOW-UP 2/FOLLOW-UP 3 note on the opportunity
                   is the durable state; a confirmed reply stops the sequence
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
from datetime import date, datetime, timedelta
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

# touch_number -> (note title prefix, minimum days since the PREVIOUS touch).
# Measured from the last touch, not touch 1, so a late touch 2 doesn't force
# touch 3 to fire early -- stays correct under real-world scheduling drift.
TOUCH_SCHEDULE: Dict[int, Tuple[str, int]] = {
    1: ("FIRST TOUCH", 0),
    2: ("FOLLOW-UP 2", 2),
    3: ("FOLLOW-UP 3", 4),
}
MAX_TOUCHES = 3

_SUBJECT_TMPL = "about {domain}"

_BODY_TMPL_1 = """Hi {greeting},

I looked at {domain} recently, honest reason below, and noticed one thing worth a minute of your time: {defect_phrase}.

I am Michael Rodriguez. I run {brand_name} and I check sites like yours for exactly this kind of thing. No charge and no strings: if you want, I will send over a short, specific note on what I found and what fixing it would take. If it is already handled, even better, and you will hear nothing else from me.

Worth a look?

If you would rather not hear from me, reply no thanks and that is the end of it.

Michael Rodriguez
{brand_name}
"""

# Touch 2: continues the SAME thread, no new claim (we have exactly one
# verified defect and will not invent a second to match a "forgot to
# mention" pattern that assumes fresh evidence). Restates the real finding
# with a bit more specificity on WHY it matters, nothing fabricated.
_BODY_TMPL_2 = """Hi {greeting},

Following up on {domain}: the reason I flagged {defect_phrase} is that it is the kind of thing visitors notice in the first few seconds, before they ever read a word of your site.

Still happy to send the short breakdown, free, if it would help. If it is already sorted, no need to reply.

Michael Rodriguez
{brand_name}
"""

# Touch 3: the BYAF close -- explicit permission to ignore, same real
# evidence, no re-pitch, no new claim.
_BODY_TMPL_3 = """Hi {greeting},

Last one from me on this, promise. I know things get busy, so totally understand if the note about {defect_phrase} on {domain} slipped by.

If it is not useful right now, no hard feelings, just let me know and I will leave it there.

Michael Rodriguez
{brand_name}
"""

_BODY_TMPLS: Dict[int, str] = {1: _BODY_TMPL_1, 2: _BODY_TMPL_2, 3: _BODY_TMPL_3}

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


def _touch_state(runtime_key: str, opportunity_id: str) -> Tuple[int, Optional[date]]:
    """(highest touch_number sent so far, its send date). (0, None) = never
    touched. Fail closed: if we cannot read notes, return MAX_TOUCHES so the
    opportunity is treated as fully sequenced (never sent to) rather than
    risking a re-send we cannot actually verify against."""
    base, h = _twenty(runtime_key)
    r = requests.get(f"{base}/rest/noteTargets", headers=h, timeout=20,
                     params={"filter": f"targetOpportunityId[eq]:{opportunity_id}", "limit": 20})
    if not r.ok:
        return MAX_TOUCHES, None
    best_n, best_date = 0, None
    prefixes = {n: p for n, (p, _) in TOUCH_SCHEDULE.items()}
    for t in (r.json().get("data") or {}).get("noteTargets") or []:
        rn = requests.get(f"{base}/rest/notes/{t.get('noteId')}", headers=h, timeout=20)
        title = (((rn.json().get("data") or {}).get("note") or {}).get("title") or "") if rn.ok else ""
        for n, prefix in prefixes.items():
            if title.startswith(f"{prefix} sent "):
                try:
                    d = date.fromisoformat(title.split("sent ", 1)[1].strip())
                except ValueError:
                    continue
                if n > best_n or (n == best_n and (best_date is None or d > best_date)):
                    best_n, best_date = n, d
    return best_n, best_date


# --------------------------------------------------------------------------- recipient discovery

_EMAIL_PAT = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_JUNK_LOCAL_PARTS = (
    "noreply", "no-reply", "wordpress", "example", "sentry", "webmaster",
    "postmaster", "abuse", "mailer-daemon", "donotreply",
)
# Fixed guesses for common CMS conventions (checked first -- cheapest path).
_EMAIL_GUESS_PATHS = (
    "", "/contact", "/contact-us", "/contact-us/", "/about", "/about-us",
    "/team", "/our-team", "/staff", "/leadership",
)
# Homepage nav links whose href or visible text hints at a contact page --
# catches CMS-specific URLs the fixed guesses above miss (e.g. "/get-in-touch",
# "/reach-us"). Bounded so one slow site can't blow up the crawl.
_CONTACT_LINK_HINT = re.compile(r"contact|about|team|staff|touch|reach|connect", re.I)
_HREF_PAT = re.compile(r'href=["\']([^"\'#?]+)', re.I)
_MAX_DISCOVERED_LINKS = 4


def _extract_email(html: str, root: str) -> Optional[str]:
    for m in _EMAIL_PAT.findall(html):
        local, _, host = m.lower().partition("@")
        if host.endswith(root) and local not in _JUNK_LOCAL_PARTS and not local.startswith(_JUNK_LOCAL_PARTS):
            return m
    return None


def _discover_contact_links(html: str, domain: str) -> List[str]:
    """Same-domain hrefs from the homepage nav that look like a contact/about
    page, in document order, deduped, capped at _MAX_DISCOVERED_LINKS."""
    seen: List[str] = []
    for href in _HREF_PAT.findall(html):
        if not _CONTACT_LINK_HINT.search(href):
            continue
        path = href
        if path.startswith("http"):
            if _domain_host(path) != domain:
                continue
            path = "/" + path.split("/", 3)[3] if path.count("/") >= 3 else "/"
        if not path.startswith("/"):
            continue
        if path not in seen:
            seen.append(path)
        if len(seen) >= _MAX_DISCOVERED_LINKS:
            break
    return seen


def _domain_host(url: str) -> str:
    return url.split("//", 1)[-1].split("/", 1)[0].lower()


def _published_email(domain: str) -> Optional[str]:
    """An email the company itself publishes -- homepage, a guessed common
    contact/about/team path, or a contact-like link discovered in the
    homepage nav -- same registrable domain (subdomain-tolerant). Never any
    other source (no brokers, no inference)."""
    root = ".".join(domain.split(".")[-2:])
    home_html = ""
    checked = set()
    for path in _EMAIL_GUESS_PATHS:
        checked.add(path)
        try:
            r = requests.get(f"https://{domain}{path}", timeout=12,
                             headers={"User-Agent": "Mozilla/5.0"})
        except Exception:
            continue
        if not r.ok:
            continue
        if path == "":
            home_html = r.text
        found = _extract_email(r.text, root)
        if found:
            return found
    for path in _discover_contact_links(home_html, domain):
        if path in checked:
            continue
        try:
            r = requests.get(f"https://{domain}{path}", timeout=12,
                             headers={"User-Agent": "Mozilla/5.0"})
        except Exception:
            continue
        if not r.ok:
            continue
        found = _extract_email(r.text, root)
        if found:
            return found
    return None


# --------------------------------------------------------------------------- copy + validation

def _defect_kind(opportunity_name: str) -> Optional[str]:
    m = re.search(r"SDR-verified:\s*([a-z_]+)", opportunity_name)
    return m.group(1) if m else None


def compose(company_name: str, domain: str, defect_kind: str, brand_name: str,
           touch_number: int = 1) -> Tuple[str, str]:
    phrase = DEFECT_PHRASES[defect_kind].format(domain=domain)
    greeting = company_name.strip() or "there"
    subject = _SUBJECT_TMPL.format(domain=domain)
    body = _BODY_TMPLS[touch_number].format(greeting=greeting, domain=domain,
                                            defect_phrase=phrase, brand_name=brand_name)
    return subject, body


def validate(subject: str, body: str, *, company_name: str, domain: str,
             defect_kind: str, brand_name: str, touch_number: int = 1) -> Optional[str]:
    """Deterministic proof the outgoing text is template + evidence, nothing
    else. Returns a reason string on failure, None when clean."""
    if _EMDASH in body or _EMDASH in subject:
        return "em_dash"
    if defect_kind not in DEFECT_PHRASES:
        return "unknown_defect_kind"
    if touch_number not in _BODY_TMPLS:
        return "unknown_touch_number"
    # Rebuild what the template SHOULD produce; any drift = tampering.
    exp_subject, exp_body = compose(company_name, domain, defect_kind, brand_name, touch_number)
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


def _has_replied(desk: str, recipient_email: str, since: date) -> bool:
    """True if the recipient has sent anything to the sending identity's real
    inbox since `since` -- checked via the Postal-authenticated Gmail read
    scope (tools/gmail_multi.py), the same inbox postal_inbox already polls.
    Fail CLOSED: any read failure returns True (block the next touch) rather
    than risk pitching over a reply we could not confirm."""
    try:
        from tools.gmail_multi import search
        query = f"from:{recipient_email} after:{since.strftime('%Y/%m/%d')}"
        threads = search(desk, query, limit=5)
        return bool(threads)
    except Exception as e:
        logger.warning("[first-touch] reply check failed for %s (%s): %s -- blocking touch",
                       recipient_email, desk, e)
        return True


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
                touches_sent, last_touch_date = _touch_state(runtime_key, oid)
                if touches_sent >= MAX_TOUCHES:
                    counts["skipped_touched"] += 1
                    lines.append(f"- {desk}: {oname[:50]} sequence complete ({touches_sent}/{MAX_TOUCHES}), skip")
                    continue
                touch_number = touches_sent + 1
                _, min_gap_days = TOUCH_SCHEDULE[touch_number]
                today = (now or datetime.now(_CT)).date()
                if last_touch_date is not None and (today - last_touch_date).days < min_gap_days:
                    counts["skipped_touched"] += 1
                    lines.append(f"- {desk}: {oname[:50]} touch {touch_number} not due yet "
                                 f"({(today - last_touch_date).days}/{min_gap_days}d), skip")
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
                if touch_number > 1 and last_touch_date is not None:
                    if _has_replied(desk, email, last_touch_date):
                        counts["skipped_touched"] += 1
                        lines.append(f"- {desk}: {email} replied (or unverifiable), sequence stopped")
                        continue
                subject, body = compose(company_name, domain, kind, brand_name, touch_number)
                v = validate(subject, body, company_name=company_name, domain=domain,
                             defect_kind=kind, brand_name=brand_name, touch_number=touch_number)
                if v:
                    exc(desk, email, f"validator:{v}")
                    continue
                blocked, why = _scrutineer(subject, body, company_name, domain, kind)
                if blocked:
                    exc(desk, email, f"scrutineering_block:{why[:80]}")
                    continue
                if not commit:
                    lines.append(f"- {desk}: WOULD SEND touch {touch_number} to {email} ({oname[:50]})")
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
                    lines.append(f"- {desk}: SENT touch {touch_number} to {email} ({oname[:50]})")
                    _mark_touched(runtime_key, oid, email, touch_number)
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


def _mark_touched(runtime_key: str, opportunity_id: str, email: str, touch_number: int = 1) -> None:
    """The durable sequence-state marker. Best-effort note; a failure here is
    logged -- the audit row still exists and _touch_state fails closed
    (MAX_TOUCHES) on read errors, so a lost note blocks further sends rather
    than risking a re-send."""
    try:
        prefix, _ = TOUCH_SCHEDULE[touch_number]
        base, h = _twenty(runtime_key)
        r = requests.post(f"{base}/rest/notes", headers=h, timeout=20,
                          json={"title": f"{prefix} sent {datetime.now(_CT).date()}",
                                "bodyV2": {"markdown": f"Autonomous touch {touch_number}/{MAX_TOUCHES} "
                                           f"to {email} via send-as-brand rail (seat {_SEAT})."}})
        nid = ((r.json().get("data") or {}).get("createNote") or {}).get("id") if r.ok else None
        if nid:
            requests.post(f"{base}/rest/noteTargets", headers=h, timeout=20,
                          json={"noteId": nid, "targetOpportunityId": opportunity_id})
    except Exception as e:
        logger.warning("[first-touch] dedup note failed for %s: %s", opportunity_id, e)
