#!/usr/bin/env python3
"""Strip em-dashes from Worship Digital's live Loops campaign emails, on-platform.

Walks WD's Loops campaigns, finds their email messages, demotes em-dashes in
subject / preview / content to commas (matching the no-em-dash policy), and
POSTs the cleaned content back via the Loops connector (optimistic-concurrency
revisionId handled for you). Dry-run by default; backs up each message before any
write.

Usage:
    python scripts/wd_loops_emdash_clean.py              # dry run (no writes)
    python scripts/wd_loops_emdash_clean.py --apply      # apply
    python scripts/wd_loops_emdash_clean.py --email-message-id MSG [--apply]

Requires LOOPS_API_KEY_WD (or LOOPS_API_KEY) in paperclip/.env / Doppler.
"""
from __future__ import annotations

import argparse
import datetime
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
try:
    from dotenv import load_dotenv
    load_dotenv(REPO_ROOT / ".env")
except ImportError:
    pass

from tools import loops  # noqa: E402

BACKUP_DIR = REPO_ROOT / "scripts" / "backups"
H = r"[^\S\n]"  # horizontal whitespace, never newline


def clean(t: str) -> str:
    if not t:
        return t
    t = re.sub(rf"(?<=\d){H}*[—―]{H}*(?=\d)", "-", t)       # numeric range
    t = re.sub(rf"{H}*[—―]{H}*", ", ", t)                    # everything else -> comma
    t = re.sub(rf",{H}*,", ",", t)
    t = re.sub(rf",{H}*([.:;!?])", r"\1", t)
    t = re.sub(rf"{H}+,", ",", t)
    t = re.sub(r",(?=[A-Za-z])", ", ", t)
    return t


def emcount(*vals) -> int:
    return sum((v or "").count("—") + (v or "").count("―") for v in vals)


def discover_email_message_ids(campaign: dict) -> list[str]:
    """Best-effort: pull email-message ids out of a campaign object, whatever
    shape Loops returns (emailMessageId field, nested emailMessage objects, or
    message dicts carrying content/subject)."""
    found: list[str] = []

    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                kl = k.lower()
                if kl in ("emailmessageid", "messageid") and isinstance(v, (str, int)):
                    found.append(str(v))
                if kl in ("emailmessage", "message") and isinstance(v, dict) and v.get("id"):
                    found.append(str(v["id"]))
                if kl == "id" and ("content" in node or "subject" in node or "previewText" in node):
                    found.append(str(v))
                walk(v)
        elif isinstance(node, list):
            for x in node:
                walk(x)

    walk(campaign)
    seen, out = set(), []
    for i in found:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


def process_message(msg_id: str, apply: bool) -> dict:
    msg = loops.get_email_message(msg_id)
    if isinstance(msg, str):
        return {"id": msg_id, "status": "error", "detail": msg}
    subj, prev, body = msg.get("subject"), msg.get("previewText"), msg.get("content")
    before = emcount(subj, prev, body)
    if before == 0:
        return {"id": msg_id, "status": "clean", "em": 0}
    new_subj, new_prev, new_body = clean(subj), clean(prev), clean(body)
    if not apply:
        return {"id": msg_id, "status": "would-fix", "em": before,
                "subject_changed": subj != new_subj}
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    (BACKUP_DIR / f"loops_msg_{msg_id}_{stamp}.json").write_text(
        json.dumps(msg, indent=2, default=str))
    res = loops.update_email_message(
        msg_id,
        content=new_body if body is not None else None,
        subject=new_subj if subj is not None else None,
        preview_text=new_prev if prev is not None else None,
        expected_revision_id=loops._revision_of(msg),
    )
    if isinstance(res, str):
        return {"id": msg_id, "status": "error", "em": before, "detail": res}
    return {"id": msg_id, "status": "fixed", "em": before}


def main(argv) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write changes (default dry-run)")
    ap.add_argument("--email-message-id", action="append", default=[],
                    help="target specific message id(s); skips campaign discovery")
    args = ap.parse_args(argv)

    st = loops.loops_status()
    if not st["configured"]:
        print("Loops not configured: " + st["detail"])
        print("Add LOOPS_API_KEY_WD to paperclip/.env, then re-run.")
        return 2
    if not st["valid"]:
        print("Loops key invalid: " + str(st["detail"]))
        return 2

    msg_ids = list(args.email_message_id)
    if not msg_ids:
        camps = loops.list_campaigns()
        if isinstance(camps, str):
            print(camps)
            return 2
        items = camps.get("campaigns") if isinstance(camps, dict) else camps
        for c in (items or []):
            cid = c.get("id") if isinstance(c, dict) else c
            full = loops.get_campaign(cid)
            if isinstance(full, dict):
                msg_ids += discover_email_message_ids(full)
        msg_ids = list(dict.fromkeys(msg_ids))

    if not msg_ids:
        print("No email messages discovered. Pass --email-message-id explicitly.")
        return 1

    mode = "APPLY" if args.apply else "DRY RUN"
    print(f"=== WD Loops em-dash cleanup [{mode}] — {len(msg_ids)} messages ===")
    total = fixed = 0
    for mid in msg_ids:
        r = process_message(mid, args.apply)
        total += r.get("em", 0)
        if r["status"] in ("fixed",):
            fixed += 1
        print(f"  {r['status']:>9}  {mid}  em={r.get('em', 0)}"
              + (f"  {r.get('detail','')[:80]}" if r.get("detail") else ""))
    print(f"--- em-dashes found: {total} | messages "
          f"{'fixed' if args.apply else 'to fix'}: {fixed if args.apply else '(dry run)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
