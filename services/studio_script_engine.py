"""Studio SCRIPT engine — SPEC A (file 145), delivered EMAIL-FIRST.

Every Sunday 17:30 CT (app.py cron) this produces the coming Tuesday's shoot
sheet and emails it to Michael. The email BODY carries the scripts; the commit
to avo-telemetry is the archive, not the delivery. That order is the whole
point: two gated sheets (07-21, 08-04) rotted unread on disk because "commit +
flag" was treated as delivery. A sheet Michael has not received does not exist.

Behavior:
  1. If `scripts/SHOOT_<next-tuesday>.md` already exists in avo-telemetry
     (hand-authored, e.g. the founding slate), email THAT verbatim. Human
     sheets always win; the engine never overwrites one.
  2. Otherwise generate 5 scripts (one per brand) with llm_json against the
     file-84 5-part structure + per-brand file-107 pillar + file-117 format
     constraints, run the deterministic gate, retry once with the violations
     fed back, then commit + email.
  3. Every failure is LOUD: a failed run emails the failure itself, and a
     failed email opens a GitHub issue (the standing alert rail). A silent
     Sunday is the exact defect this engine exists to fix.

Gate (deterministic, absolute on purpose — nuanced rules rot):
  no em-dash anywhere · every script carries all 5 parts · combined script
  length 100-230 words · no "$"-figures, no percent-stat other than the
  take-QC'd Invoca 3% line · no #fyp/#viral/#foryou · Book'd carries no
  income/earnings/outcome/guarantee language · title must not overlap an
  existing shot script (token-Jaccard).
"""
from __future__ import annotations

import base64
import logging
import os
import pathlib
import re
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import requests

from services.studio_social_llm import LLMError, llm_json

logger = logging.getLogger(__name__)

_CENTRAL = ZoneInfo("America/Chicago")
_REPO = "salesdroid/avo-telemetry"
_SCRIPTS_DIR = "marketing_deliverables/116_video_leg_activation/scripts"
_ISSUE_REPO = os.getenv("ALERT_REPO", "Automotive-Intelligence/paperclip")

_EM_DASH = "—"
_BANNED_TAGS_RE = re.compile(r"#(?:fyp|viral|foryou)\b", re.I)
_PCT_RE = re.compile(r"\d+(?:\.\d+)?\s*(?:%|percent)", re.I)
_DOLLAR_RE = re.compile(r"\$\s*\d")
_BOOKD_BANNED_RE = re.compile(r"\b(?:income|earn(?:ings)?|guarantee[ds]?)\b", re.I)
_ALLOWED_PCTS = {"3%", "3 percent"}  # the take-QC'd Invoca voicemail line, only

_SCRIPT_PARTS = ("hook", "meat", "climax", "comment_ask", "follow_ask")

def _money_pages() -> Dict[str, List[str]]:
    """Real destinations, read from config/slipstream_brands.yaml rather than
    duplicated here. The engine used to ask the model for a "destination" while
    telling it nothing, so every script came back with "link in bio" and the
    funnel got no traffic it could attribute."""
    import yaml
    cfg = yaml.safe_load(
        (pathlib.Path(__file__).resolve().parent.parent /
         "config" / "slipstream_brands.yaml").read_text())
    out = {}
    for slug, key in (("autointelligence", "avi"), ("aiphoneguy", "aipg"),
                      ("worshipdigital", "wd"), ("agentempire", "bae"),
                      ("bookd", "bookd")):
        b = cfg["brands"].get(slug) or {}
        dom = b.get("domain", "")
        out[key] = [f"https://{dom}{p}" for p in (b.get("money_pages") or [])]
    return out


_PHONE = {"aipg": "(817) 670-9689"}   # the ONE tracked rail Meta counts itself

_BRANDS: List[Dict[str, str]] = [
    {"key": "avi", "name": "AvI", "format": "on camera",
     "pillar": "file 107 AvI: the orchestration wedge / evaluating dealership AI",
     "world": "showroom floor, service drive, CRM screens; business casual",
     "voice": "restrained, diagnostic, anti-hype; 'I sell cars for a living and I build this for stores like mine'"},
    {"key": "aipg", "name": "AIPG", "format": "VO ONLY, no face",
     "pillar": "file 107 AIPG: true cost of missed calls / how AI answering works",
     "world": "the TRADE OWNER'S world: vans, ladders, ringing phones; b-roll from the 8-clip CLEAN pool only (file 117: never a desk take)",
     "voice": "plainspoken, trade-owner-to-trade-owner, 'it's just math'; no pricing ever"},
    {"key": "wd", "name": "WD", "format": "on camera",
     "pillar": "file 107 WD: is your marketing actually working / the free sample",
     "world": "a real small business; kitchen table, plain office; founder-next-door",
     "voice": "transparent, owner-to-owner, anti-buzzword; founder run"},
    {"key": "bae", "name": "BAE", "format": "on camera",
     "pillar": "file 107 BAE: building in the margins of a day job",
     "world": "6:40am home desk, coffee, terminal, real bugs; tee or hoodie",
     "voice": "builder field-notes, honest, ledger-only numbers, no income claims"},
    {"key": "bookd", "name": "BOOK'D", "format": "on camera",
     "pillar": "file 107 Book'd: speed-to-lead / the compliance moat",
     "world": "agent-professional desk; Michael fronts as co-founder",
     "voice": "plain, agent-to-agent; NEVER pricing, NEVER income/earnings/outcome/guarantee claims"},
]


# ---------------------------------------------------------------- dates
def next_tuesday(today: Optional[date] = None) -> date:
    """The coming shoot Tuesday. Run on a Tuesday -> next week's (shoot day's
    sheet must already exist by then)."""
    d = today or datetime.now(_CENTRAL).date()
    days = (1 - d.weekday()) % 7
    return d + timedelta(days=days or 7)


# ---------------------------------------------------------------- repo I/O
def _gh_headers(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}


def fetch_repo_file(path: str, token: str) -> Optional[str]:
    r = requests.get(f"https://api.github.com/repos/{_REPO}/contents/{path}",
                     headers=_gh_headers(token), timeout=30)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return base64.b64decode(r.json()["content"]).decode("utf-8")


def list_existing_titles(token: str) -> List[str]:
    """Dedupe set: every .md filename in the scripts dir, slug -> words."""
    r = requests.get(f"https://api.github.com/repos/{_REPO}/contents/{_SCRIPTS_DIR}",
                     headers=_gh_headers(token), timeout=30)
    r.raise_for_status()
    names = [f["name"][:-3] for f in r.json() if f["name"].endswith(".md")]
    return [re.sub(r"^(?:aipg|avi|bae|bookd|wd)_\d+_", "", n).replace("_", " ")
            for n in names if not n.startswith("SHOOT")]


# ---------------------------------------------------------------- the gate
def _title_overlap(title: str, existing: List[str]) -> Optional[str]:
    t = set(re.findall(r"[a-z']+", title.lower())) - {"the", "a", "an", "of", "your", "you"}
    for ex in existing:
        e = set(re.findall(r"[a-z']+", ex.lower())) - {"the", "a", "an", "of", "your", "you"}
        if t and e and len(t & e) / len(t | e) > 0.6:
            return ex
    return None


def gate_videos(videos: List[Dict[str, Any]], existing_titles: List[str]) -> List[str]:
    """Deterministic violations. Empty list == pass."""
    v: List[str] = []
    for i, vid in enumerate(videos):
        label = f"video {i + 1} ({vid.get('brand', '?')})"
        blob = " ".join(str(vid.get(k, "")) for k in
                        (*_SCRIPT_PARTS, "title", "onscreen_hook")) + " " + \
               " ".join(str(c) for c in (vid.get("captions") or {}).values())
        if _EM_DASH in blob:
            v.append(f"{label}: em-dash present")
        for part in _SCRIPT_PARTS:
            if not str(vid.get(part, "")).strip():
                v.append(f"{label}: missing [{part.upper()}]")
        words = len(" ".join(str(vid.get(p, "")) for p in _SCRIPT_PARTS).split())
        if not 100 <= words <= 230:
            v.append(f"{label}: script is {words} words (need 100-230)")
        if _DOLLAR_RE.search(blob):
            v.append(f"{label}: dollar figure in copy (pricing is banned)")
        for m in _PCT_RE.findall(blob):
            if m.strip().lower().replace(" ", "") not in {p.replace(" ", "") for p in _ALLOWED_PCTS}:
                v.append(f"{label}: unapproved stat '{m}' (only the Invoca 3% line is take-QC'd)")
        if _BANNED_TAGS_RE.search(blob):
            v.append(f"{label}: banned hashtag (#fyp/#viral/#foryou)")
        if vid.get("brand", "").lower() in ("bookd", "book'd") and _BOOKD_BANNED_RE.search(blob):
            v.append(f"{label}: Book'd compliance language (income/earnings/guarantee)")
        dest = str(vid.get("destination", ""))
        if "https://" not in dest and not re.search(r"\d{3}[)\-.\s]\s*\d{3}[-.\s]\d{4}", dest):
            v.append(f"{label}: destination {dest!r} is not a real URL or tracked "
                     f"number; 'link in bio' cannot be attributed and the loader "
                     f"refuses it at schedule time")
        hit = _title_overlap(str(vid.get("title", "")), existing_titles)
        if hit:
            v.append(f"{label}: title overlaps existing script '{hit}' (angle already shot)")
    return v


# ---------------------------------------------------------------- generate
_SYSTEM = """You write 60-second social video scripts for a founder-operator, Michael, who
sells cars six days a week and runs five brands. Anti-guru, anti-hype, operator voice.
Absolute rules: NO em-dashes anywhere. NO statistics except the one cleared line
(fewer than 3% of callers leave a voicemail, Invoca). NO pricing or dollar figures.
Book'd: no income, earnings, outcome, or guarantee language. Every script uses the
5-part structure: hook (pattern interrupt, 1-2 sentences), meat (3-5 sentences of
value), climax (the payoff line), comment_ask (one question inviting buyers AND
non-buyers), follow_ask (ends with: For more like this, follow BRAND). Total 140-190
words. One CTA per video matched to its stage. Return ONLY JSON:
{"videos":[{"brand","stage","title","seconds","format","pillar","cta","destination",
"hook","meat","climax","comment_ask","follow_ask","onscreen_hook",
"broll":["shot", ...],"captions":{"tiktok","instagram","facebook","linkedin",
"youtube_title","youtube_desc"}}]}
onscreen_hook: max 9 words, works sound-off. TikTok caption max 5 hashtags, others
max 6, never #fyp #viral #foryou."""


def generate_videos(existing_titles: List[str], week_note: str = "") -> List[Dict[str, Any]]:
    """One call PER BRAND, not one call for all five.

    Asking for five complete scripts (5-part structure + 8 deliverables each) in
    a single response exhausted the token budget: the model returned
    stop_reason=max_tokens with no text, four retries running, and the Sunday
    cron died there every week. Per-brand calls each fit comfortably, and a
    brand that fails no longer takes the whole sheet down with it.
    """
    pages = _money_pages()
    videos: List[Dict[str, Any]] = []
    failed: List[str] = []
    # The plan pairs AWARENESS with CONVERSION. Left to itself the model wrote
    # 4 awareness to 1 conversion, which does not serve an MRR north star, so
    # the stage is ASSIGNED and alternates week to week.
    week = int(datetime.now(_CENTRAL).strftime("%V"))
    for idx, b in enumerate(_BRANDS):
        stage = "conversion" if (idx + week) % 2 == 0 else "awareness"
        dests = pages.get(b["key"], [])
        phone = _PHONE.get(b["key"])
        user = (
            f"Write ONE script for {b['name']} for this week's Tuesday shoot.\n"
            f"format={b['format']}; pillar={b['pillar']}; world={b['world']}; "
            f"voice={b['voice']}\n"
            f"STAGE (assigned, do not change): {stage}.\n"
            f"DESTINATION: you MUST use one of these real URLs verbatim, chosen "
            f"to fit the stage, and put it in `destination` as a full https:// "
            f"link: {dests or 'none configured'}.\n"
            + (f"For a conversion piece the strongest ask is the tracked line "
               f"{phone}, spoken and on screen; Meta counts that call itself.\n"
               if phone and stage == "conversion" else "")
            + f"NEVER write 'link in bio' or invent a page. An untagged "
              f"destination cannot be attributed and the post will be refused "
              f"at schedule time.\n{week_note}\n\n"
            f"Angles already shot, do NOT overlap these:\n"
            + "\n".join(f"- {x}" for x in existing_titles)
            + "\n\nReturn ONLY: {\"videos\":[{ ...one object... }]}"
        )
        try:
            out = llm_json(_SYSTEM, user, max_tokens=6000)
            got = out.get("videos") or []
            if got:
                videos.extend(got[:1])
            else:
                failed.append(b["name"])
        except LLMError as e:
            logger.warning("[script-engine] %s failed: %s", b["name"], e)
            failed.append(b["name"])
    if not videos:
        raise LLMError(f"every brand failed: {', '.join(failed)}")
    if failed:
        logger.warning("[script-engine] brands missing from this sheet: %s",
                       ", ".join(failed))
    return videos


# ---------------------------------------------------------------- render
def render_sheet(videos: List[Dict[str, Any]], shoot: date, dedupe_names: List[str]) -> str:
    n_vo = sum(1 for v in videos if "vo" in str(v.get("format", "")).lower())
    n_cam = len(videos) - n_vo
    L = [f"# SHOOT · Tue {shoot.isoformat()} · {n_cam} on-camera + {n_vo} VO "
         f"(~{max(2, round(len(videos) * 1.5))} min total)", "",
         "**Dedupe verified against:** " + ", ".join(sorted(dedupe_names)[:40]), "",
         "**Rules:** restarts fine, the flub engine keeps the last read. No em-dashes. "
         "Only take-QC'd stats. No pricing on camera. Book'd: no income, earnings, "
         "outcome, or guarantee claims.", "", "---", ""]
    for i, v in enumerate(videos, 1):
        cap = v.get("captions") or {}
        L += [f"## {i}. {v.get('brand', '?').upper()} · {str(v.get('stage', '?')).upper()} · "
              f"\"{v.get('title', '')}\" (~{v.get('seconds', 60)}s, {v.get('format', 'on camera')})",
              f"**Pillar:** {v.get('pillar', '')} · **CTA:** {v.get('cta', '')} · "
              f"**Destination:** {v.get('destination', '')}", ""]
        for part in _SCRIPT_PARTS:
            L.append(f"**[{part.upper().replace('_', ' ')}]** {v.get(part, '')}")
        L += ["", f"**On-screen hook:** {v.get('onscreen_hook', '')}",
              "**B-roll:** " + " · ".join(v.get("broll") or []), "", "### Post kit"]
        for k in ("tiktok", "instagram", "facebook", "linkedin"):
            if cap.get(k):
                L.append(f"- **{k.title()}:** {cap[k]}")
        if cap.get("youtube_title"):
            L.append(f"- **YT Shorts:** {cap['youtube_title']} · {cap.get('youtube_desc', '')}")
        L.append("")
    return "\n".join(L)


# ---------------------------------------------------------------- delivery
def _md_to_html(md: str) -> str:
    """Minimal, dependency-free markdown for a phone-readable email."""
    import html as _html
    out: List[str] = []
    for line in md.split("\n"):
        s = _html.escape(line)
        s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
        if s.startswith("### "):
            out.append(f"<h3 style='margin:14px 0 4px'>{s[4:]}</h3>")
        elif s.startswith("## "):
            out.append(f"<h2 style='margin:22px 0 6px;border-top:1px solid #ddd;padding-top:14px'>{s[3:]}</h2>")
        elif s.startswith("# "):
            out.append(f"<h1 style='margin:0 0 8px'>{s[2:]}</h1>")
        elif s.strip() in ("---", ""):
            out.append("<div style='height:8px'></div>")
        else:
            out.append(f"<p style='margin:2px 0;line-height:1.5'>{s}</p>")
    return ("<div style='font-family:-apple-system,Segoe UI,sans-serif;font-size:15px;"
            "max-width:640px;margin:0 auto;color:#111'>" + "\n".join(out) + "</div>")


def send_scripts_email(subject: str, md_body: str, *, dry_run: bool = False) -> bool:
    key = (os.getenv("RESEND_API_KEY") or "").strip()
    frm = os.getenv("SCRIPT_ENGINE_FROM", "AVO Studio <briefing@mail.automotiveintelligence.io>")
    # BOTH recipients, matching BRIEFING_RECIPIENTS on the morning-briefing rail
    # that demonstrably lands. Sending to michael@ alone reported delivered at
    # Resend while nothing appeared in the mailbox Michael actually reads, so
    # every shoot sheet since 08-06 went nowhere he could see it.
    to = [a.strip() for a in os.getenv(
        "SCRIPT_ENGINE_TO",
        "michael@automotiveintelligence.io,salesdroid@gmail.com").split(",") if a.strip()]
    if dry_run:
        logger.info("[script-engine] DRY RUN: would email %r (%d chars) to %s",
                    subject, len(md_body), ", ".join(to))
        return True
    if not key:
        logger.error("[script-engine] RESEND_API_KEY missing; cannot deliver")
        return False
    r = requests.post("https://api.resend.com/emails", timeout=20,
                      headers={"Authorization": f"Bearer {key}",
                               "Content-Type": "application/json"},
                      json={"from": frm, "to": to, "subject": subject,
                            "html": _md_to_html(md_body)})
    if r.status_code not in (200, 201):
        logger.error("[script-engine] Resend %s: %s", r.status_code, r.text[:300])
        return False
    return True


def _alert_issue(title: str, body: str, token: str) -> bool:
    """Backup rail (GitHub issue -> email) when the email itself fails."""
    try:
        r = requests.post(f"https://api.github.com/repos/{_ISSUE_REPO}/issues",
                          headers=_gh_headers(token), timeout=30,
                          json={"title": title, "body": body,
                                "labels": ["studio", "script-engine"]})
        return r.status_code == 201
    except Exception:
        logger.exception("[script-engine] issue rail failed too")
        return False


# ---------------------------------------------------------------- run
def run(dry_run: bool = False, today: Optional[date] = None) -> Dict[str, Any]:
    shoot = next_tuesday(today)
    token = (os.getenv("SLIPSTREAM_GH_TOKEN") or os.getenv("GITHUB_TOKEN") or "").strip()
    receipt: Dict[str, Any] = {"shoot_date": shoot.isoformat(), "dry_run": dry_run}
    if not token:
        receipt.update(ok=False, error="no GitHub token")
        return receipt

    sheet_path = f"{_SCRIPTS_DIR}/SHOOT_{shoot.isoformat()}.md"
    try:
        existing_sheet = fetch_repo_file(sheet_path, token)
    except Exception as e:
        existing_sheet = None
        receipt["fetch_warning"] = str(e)[:200]

    if existing_sheet:
        # A hand-authored sheet always wins; the engine only delivers it.
        subject = f"🎬 Tuesday shoot scripts · {shoot.strftime('%b %-d')} (gated sheet ready)"
        emailed = send_scripts_email(subject, existing_sheet, dry_run=dry_run)
        receipt.update(ok=emailed, source="existing_sheet", path=sheet_path, emailed=emailed)
        if not emailed:
            _alert_issue(f"script-engine: email FAILED for {sheet_path}",
                         f"Sheet exists and is gated but the email did not deliver. "
                         f"Michael shoots Tuesday {shoot} and has NO scripts.\n\n"
                         f"https://github.com/{_REPO}/blob/main/{sheet_path}", token)
        return receipt

    try:
        existing_titles = list_existing_titles(token)
        videos = generate_videos(existing_titles)
        violations = gate_videos(videos, existing_titles)
        if violations:
            videos = generate_videos(
                existing_titles,
                week_note="Your previous draft FAILED the gate. Fix exactly these and "
                          "change nothing else:\n" + "\n".join(violations))
            violations = gate_videos(videos, existing_titles)
        if violations:
            raise ValueError("gate failed twice: " + "; ".join(violations[:6]))
        sheet = render_sheet(videos, shoot, existing_titles)
        if not dry_run:
            from services.studio_social_engine import _commit_files_to_main
            _commit_files_to_main({sheet_path: sheet},
                                  f"studio: script engine sheet for {shoot}", token)
        subject = f"🎬 Tuesday shoot scripts · {shoot.strftime('%b %-d')} ({len(videos)} videos)"
        emailed = send_scripts_email(subject, sheet, dry_run=dry_run)
        receipt.update(ok=emailed, source="generated", videos=len(videos),
                       path=sheet_path, emailed=emailed)
        if not emailed:
            _alert_issue(f"script-engine: email FAILED for {sheet_path}", sheet[:2000], token)
        return receipt
    except Exception as e:
        # LOUD failure: the failure email is the product when the run breaks.
        logger.exception("[script-engine] run failed")
        msg = (f"# Script engine FAILED for Tuesday {shoot}\n\n"
               f"**Error:** {e}\n\nNo scripts were generated. Reply here or rerun "
               f"POST /admin/run-script-engine after the fix. A silent Sunday is the "
               f"failure mode this engine exists to prevent, so you are hearing about it.")
        emailed = send_scripts_email(f"🔴 Script engine FAILED · shoot {shoot.strftime('%b %-d')}",
                                     msg, dry_run=dry_run)
        if not emailed:
            _alert_issue(f"script-engine: RUN FAILED for {shoot}", msg, token)
        receipt.update(ok=False, error=str(e)[:400], failure_emailed=emailed)
        return receipt
