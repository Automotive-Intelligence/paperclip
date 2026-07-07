#!/usr/bin/env python3
"""
pp_build_icp_campaign.py — reusable cold-email campaign builder for Instantly v2.

The "template" engine: one ICP config in -> one Instantly campaign (sequence,
schedule, sending accounts, conservative ramp) out, left PAUSED. Idempotent
(find-by-name -> update, never duplicate). Reusable for any brand/ICP by adding
a config to ICP_CONFIGS.

Built for Paper & Purpose DataMoon intent launch (2026-06-18). NOT Klaviyo —
Klaviyo bans third-party/intent lists. Instantly permits uploaded lists.

Usage (always via Doppler so the key is injected):
  doppler run --project paperclip --config prd -- python3 scripts/pp_build_icp_campaign.py build --dry-run
  doppler run --project paperclip --config prd -- python3 scripts/pp_build_icp_campaign.py build
  doppler run --project paperclip --config prd -- python3 scripts/pp_build_icp_campaign.py load-leads --icp returning_woman
  doppler run --project paperclip --config prd -- python3 scripts/pp_build_icp_campaign.py status

Nothing is ever launched by this script. Campaigns are created PAUSED; sending
requires a manual launch after warmup + copy approval.
"""

import argparse
import csv
import json
import os
import sys
import time

import requests

BASE = "https://api.instantly.ai/api/v2"
AGENT_ENV = "INSTANTLY_API_KEY_PAPERANDPURPOSE"

# All 9 provisioned P&P sending mailboxes (shared across both campaigns; Instantly rotates).
SENDING_ACCOUNTS = [
    "miriam@getpaperandpurpose.com", "hello@getpaperandpurpose.com", "hi@getpaperandpurpose.com",
    "miriam@allpaperandpurpose.org", "hello@allpaperandpurpose.org", "hi@allpaperandpurpose.org",
    "miriam@ultrapaperandpurpose.com", "hello@ultrapaperandpurpose.com", "hi@ultrapaperandpurpose.com",
]

CTA = "https://paperandpurpose.co/products/be-transformed-guided-mind-renewal-journal"
FOOTER = ("\n\n--\nPaper & Purpose · 9901 Brodie Ln #160, Austin, TX 78748\n"
          "Not for you? Just reply \"no thanks\" and I'll never email again.")

LEADS_DIR = os.path.expanduser("~/avo-telemetry/marketing_deliverables/pp_intent_leads")

# Conservative warmup-era settings. Bump after deliverability proves out.
COMMON = {
    "daily_limit": 90,            # per-campaign emails/day (2 campaigns -> ~180/day, < ~270 capacity)
    "daily_max_leads": 40,        # new first-touches/day per campaign (gentle ramp)
    "email_gap": 12,              # minutes between sends per inbox
    "random_wait_max": 8,
    "stop_on_reply": True,
    "stop_on_auto_reply": True,
    "open_tracking": False,       # cold deliverability: tracking pixels hurt; keep off
    "link_tracking": False,
    "text_only": False,           # was True, but text_only flattened our paragraph markup
                                  # to a wall on SEND (caught 2026-06-28). Send light HTML
                                  # (paragraph divs only, no images/CSS/tracking) so breaks
                                  # render while staying plain-looking + deliverable.
    "insert_unsubscribe_header": True,
}

SCHEDULE = {
    "schedules": [{
        "name": "Weekdays 9-5 CT",
        "timing": {"from": "09:00", "to": "17:00"},
        "days": {"1": True, "2": True, "3": True, "4": True, "5": True, "0": False, "6": False},
        "timezone": "America/Chicago",
    }]
}


def _html(text):
    """Instantly stores the body as HTML with three verified quirks:
    (1) it silently drops the ENTIRE body on any '&' (raw, &amp;, &#38; all nuke it,
        2026-06-22) so we swap '&' -> 'and' (brand reads "Paper and Purpose", natural for
        a personal note);
    (2) it strips loose text sitting between top-level <br> tags;
    (3) plain '\\n' newlines COLLAPSE to spaces when the mail renders -> wall of text
        (caught 2026-06-28 on a test send).
    Fix: emit one <div> per line (blank lines -> spacer divs). <div>-wrapped text survives
    the sanitizer AND renders as real line breaks/paragraphs, so it reads as a clean,
    personal, plain-style cold email. Bare URLs inside the divs auto-link in clients."""
    text = text.replace(" & ", " and ").replace("&", "and")
    out = []
    for ln in text.split("\n"):
        out.append("<div><br></div>" if ln.strip() == "" else f"<div>{ln}</div>")
    return "".join(out)


def _steps(intro_subj, intro_body, f2_subj, f2_body, f3_subj, f3_body):
    """Three-touch sequence: day 0 intro, +3 days, +4 days. Footer appended to each."""
    return [{
        "steps": [
            {"type": "email", "delay": 3, "variants": [{"subject": intro_subj, "body": _html(intro_body + FOOTER)}]},
            {"type": "email", "delay": 4, "variants": [{"subject": f2_subj, "body": _html(f2_body + FOOTER)}]},
            {"type": "email", "delay": 0, "variants": [{"subject": f3_subj, "body": _html(f3_body + FOOTER)}]},
        ]
    }]


# Copy below is Miriam's finalized A/B series (approved 2026-06-22), her wording preserved.
# Em dashes converted to commas/periods per Michael's house rule. [journal link] -> CTA,
# [First name] -> {{firstName}}. Footer (address + "no thanks" opt-out) auto-appended by _steps.
ICP_CONFIGS = {
    "returning_woman": {
        "name": "PP ICP-A Returning Woman (DataMoon cold)",
        "lead_file": "pp_returning_woman_clean.csv",
        "sequences": _steps(
            "a gentle question, {{firstName}}",
            ("My name is Miriam, a woman of faith who knows what it's like to sit down with God and still "
             "walk away with the same anxious, cluttered mind I came with.\n\n"
             "I wasn't missing quiet time. I was missing transformation. I could journal, pray, and read, "
             "and still struggle to take my thoughts captive the way Romans 12:2 actually calls us to. And "
             "every beautiful journal I bought looked stunning on the outside and left me completely on my "
             "own on the inside.\n\nSo I built what I couldn't find.\n\n"
             "Be Transformed is a 90-day guided journal rooted in Romans 12:2. Every page is already prompted "
             "for mind renewal, so you never stare at a blank page wondering what to write. You just show up, "
             "and God meets you there.\n\n"
             "The first run ships in October, and every pre-order helps make that happen. This is a small, "
             "independent launch, no big publisher, no warehouse of extras. When you pre-order, you're not "
             "just claiming your journal. You're helping get it into hands that need it.\n\n"
             "If you want to see what's inside before you decide: " + CTA + "\n\n"
             "He's not done with your mind yet. Neither are you.\n\nIn Christ,\nMiriam\nFounder, Paper & Purpose"),
            "why 90 days",
            ("{{firstName}},\n\nNo pressure to overhaul your whole life. No guilt about the season you fell off. "
             "Just this season, this journal, one prompted page at a time.\n\n"
             "Be Transformed is a 90-day guided journal, but it's not a one-and-done. Four journals take you "
             "through an entire year of mind renewal, season by season. And when each one is filled? It's a "
             "hardcover keepsake you'll actually want to hold onto.\n\n"
             "The first run is limited and ships in October. Pre-orders are what make this launch possible, so "
             "if this is speaking to you, I don't want you to miss it.\n\n"
             "Take a full look here: " + CTA + "\n\n"
             "He's not done with your mind yet. Neither are you.\n\nIn Christ,\nMiriam\nFounder, Paper & Purpose"),
            "no pressure, {{firstName}}",
            ("{{firstName}},\n\nI promise I won't keep filling your inbox. I just believe in this too much to "
             "not say it one more time.\n\n"
             "If this is the season you've been waiting for, to finally take your thoughts captive, to show up "
             "consistently, to actually feel like your quiet time is doing something, Be Transformed was made "
             "for exactly that moment.\n\n"
             "The first run is small and ships in October. Your pre-order is what gets it there.\n\n"
             "If it's a yes: " + CTA + "\n\n"
             "If not, no hard feelings, I'm just glad our paths crossed.\n\n"
             "I'll see you in the pages,\nMiriam\nFounder, Paper & Purpose"),
        ),
    },
    "christian_girly": {
        "name": "PP ICP-B Christian Girly (DataMoon cold)",
        "lead_file": "pp_christian_girly_clean.csv",
        "sequences": _steps(
            "made you something, {{firstName}}",
            ("{{firstName}},\n\nI'm Miriam, not a big brand, not a marketing team. Just one girl who got tired "
             "of faith journals that felt like homework or looked like a tax form.\n\n"
             "I wanted something that looked beautiful on my nightstand and actually did something on the "
             "inside. Something that sounded like a friend, not a curriculum. Something rooted in Romans 12:2 "
             "that would help me take my thoughts captive on the days my brain wouldn't quiet down.\n\n"
             "I couldn't find it. So I made it.\n\n"
             "Be Transformed is a 90-day guided journal, every page already prompted, so you just show up and "
             "God meets you there. No blank page paralysis. No figuring it out alone.\n\n"
             "The first run is small, independent, and ships in October. Pre-orders are open now and honestly, "
             "each one is what makes this launch happen.\n\n"
             "Wanna see what's inside? " + CTA + "\n\n"
             "I'll see you in the pages,\nMiriam\nFounder, Paper & Purpose"),
            "the gift part",
            ("{{firstName}},\n\nReal talk, people are already buying these in pairs. One for themselves, one for "
             "their best friend, their sister, their small group.\n\n"
             "And honestly? I get it. Be Transformed is the kind of gift that actually means something. Every "
             "page is already prompted, so you're not handing someone a blank journal and hoping for the best. "
             "You're handing her something that walks with her.\n\n"
             "And one of my favorite things in the journal is the answered prayers envelope. You write it, you "
             "wait, and when God comes through, you seal it up as proof. A built-in prayer board right inside "
             "your journal.\n\n"
             "The first run ships in October and it's limited. Don't let it sell out before you grab yours, or "
             "hers.\n\n" + CTA + "\n\n"
             "I'll see you in the pages,\nMiriam\nFounder, Paper & Purpose"),
            "last note, promise",
            ("Hey {{firstName}},\n\nNot gonna flood your inbox. Just wanted to leave you with this.\n\n"
             "If you've been feeling that quiet nudge toward something more, more consistency, more peace, more "
             "of Him, I don't think that's an accident. Be Transformed was built for exactly that nudge.\n\n"
             "First run ships in October and it's limited. Here it is if you're ready: " + CTA + "\n\n"
             "I'll see you in the pages,\nMiriam"),
        ),
    },
}


def _key():
    k = (os.getenv(AGENT_ENV) or "").strip()
    if not k:
        sys.exit(f"ERROR: {AGENT_ENV} not in env. Run via: doppler run --project paperclip --config prd -- ...")
    return k


def _req(method, path, body=None, params=None):
    h = {"Authorization": f"Bearer {_key()}", "Content-Type": "application/json"}
    url = BASE + path
    for attempt in range(6):
        try:
            r = requests.request(method, url, headers=h, json=body, params=params, timeout=30)
        except requests.exceptions.RequestException as e:
            # network drop (lid close / wifi blip): back off and retry, don't crash the run
            time.sleep(min(30, 5 * (attempt + 1)))
            continue
        if r.status_code == 429:
            time.sleep(3 + attempt * 3)
            continue
        if r.status_code not in (200, 201):
            return {"_error": True, "status": r.status_code, "body": r.text[:500]}
        return r.json() if r.text.strip() else {}
    return {"_error": True, "status": "network", "body": "exhausted retries"}


def find_campaign(name):
    res = _req("GET", "/campaigns", params={"limit": 100})
    for c in (res.get("items") or []):
        if (c.get("name") or "").lower() == name.lower():
            return c
    return None


def build(dry_run=False):
    print(f"=== BUILD (dry_run={dry_run}) ===")
    for icp, cfg in ICP_CONFIGS.items():
        body = {
            "name": cfg["name"],
            "campaign_schedule": SCHEDULE,
            "email_list": SENDING_ACCOUNTS,
            "sequences": cfg["sequences"],
            **COMMON,
        }
        existing = find_campaign(cfg["name"])
        if dry_run:
            print(f"\n[{icp}] would {'UPDATE' if existing else 'CREATE'} '{cfg['name']}'")
            print(f"  accounts={len(SENDING_ACCOUNTS)} steps={len(cfg['sequences'][0]['steps'])} "
                  f"daily_limit={COMMON['daily_limit']} text_only={COMMON['text_only']}")
            print(f"  step1 subj: {cfg['sequences'][0]['steps'][0]['variants'][0]['subject']}")
            continue
        if existing:
            cid = existing["id"]
            res = _req("PATCH", f"/campaigns/{cid}", body=body)
            print(f"[{icp}] UPDATED {cid}: {'ERR ' + str(res) if res.get('_error') else 'ok (PAUSED)'}")
        else:
            res = _req("POST", "/campaigns", body=body)
            if res.get("_error"):
                print(f"[{icp}] CREATE FAILED: {res}")
            else:
                print(f"[{icp}] CREATED {res.get('id')} (PAUSED, not launched)")


def load_leads(icp):
    cfg = ICP_CONFIGS[icp]
    camp = find_campaign(cfg["name"])
    if not camp:
        sys.exit(f"Campaign for {icp} not found. Run build first.")
    cid = camp["id"]
    path = os.path.join(LEADS_DIR, cfg["lead_file"])
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    print(f"[{icp}] uploading {len(rows)} leads -> campaign {cid}")
    added = failed = 0
    for i, row in enumerate(rows, 1):
        b = {
            "email": row["email"], "campaign": cid,
            "first_name": (row.get("first_name") or "").strip() or None,
            "last_name": (row.get("last_name") or "").strip() or None,
            "skip_if_in_campaign": True,
        }
        b = {k: v for k, v in b.items() if v is not None}
        res = _req("POST", "/leads", body=b)
        if res.get("_error"):
            failed += 1
        else:
            added += 1
        if i % 250 == 0:
            print(f"  {i}/{len(rows)}  added={added} failed={failed}")
        time.sleep(0.15)  # ~6/sec, gentle on the API
    print(f"[{icp}] DONE added={added} failed={failed}")


def status():
    res = _req("GET", "/campaigns", params={"limit": 100})
    for c in (res.get("items") or []):
        print(f"  {c.get('id')}  status={c.get('status')}  {c.get('name')}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build"); b.add_argument("--dry-run", action="store_true")
    l = sub.add_parser("load-leads"); l.add_argument("--icp", required=True, choices=list(ICP_CONFIGS))
    sub.add_parser("status")
    a = ap.parse_args()
    if a.cmd == "build":
        build(dry_run=a.dry_run)
    elif a.cmd == "load-leads":
        load_leads(a.icp)
    elif a.cmd == "status":
        status()
