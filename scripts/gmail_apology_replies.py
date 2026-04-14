# AVO — AI Business Operating System
# Gmail Apology Reply Script
# Replies in-thread to 71 broken merge-tag emails with a personal apology
# Skips Bhaskar Pandey at Builders Land Source (Michael replied personally)
# Salesdroid — April 2026

"""Send in-thread apology replies to recipients of broken merge-tag emails.

Problem: 71 Marcus Attio sequence emails went out with raw `{{person.first_name}}`
merge tags instead of the recipient's name, because the Attio editor didn't
recognize the copy-pasted merge tag syntax.

Recovery: reply in-thread to each broken email with a personal apology.
Threads preserve context so recipients see what they got and what we're fixing.

Usage:
    python scripts/gmail_apology_replies.py --dry-run                          # Audit only
    python scripts/gmail_apology_replies.py --test-to salesdroid@icloud.com    # Send 1 test
    python scripts/gmail_apology_replies.py --live                             # Send all 70

Skips:
- bhaskar@builderslandsource.com (Michael replied personally)
- Any email with "bhaskar" or "builderslandsource" in the address
"""

import argparse
import email
import email.utils
import imaplib
import logging
import os
import smtplib
import sys
import time
from email.message import EmailMessage
from pathlib import Path
from typing import List, Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("apology")

# ── Config ───────────────────────────────────────────────────────────────────

GMAIL_USER = os.getenv("MAIL_USERNAME_CALLINGDIGITAL", "michael@calling.digital")
GMAIL_PASSWORD = os.getenv("MAIL_PASSWORD_CALLINGDIGITAL", "")
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993

# Emails to skip entirely (already handled manually)
SKIP_ADDRESSES = {
    "bhaskar@builderslandsource.com",
}
SKIP_DOMAINS = {
    "builderslandsource.com",
}
# Safety: skip replies to yourself
SKIP_ADDRESSES.add(GMAIL_USER.lower())

APOLOGY_BODY = """Hi,

You got an email from me recently with what looked like raw code in the subject line:
"{{person.first_name}}, something I noticed about {{company.name}}"

That was supposed to be a personalized email. A merge tag bug broke the template
and it went out to 71 people before I caught it. You were one of them. I'm sorry.

Here's what actually happened: I'm building one of the first AI-powered business
operating systems, and the outreach for my digital marketing agency (Calling
Digital) runs on top of it. I copy-pasted an email template into the sending
tool without realizing the merge tags weren't being recognized as variables.
They went out as literal text to real people instead of your actual first name
and company.

If you want off the list entirely, just reply "remove" and I'll permanently
unsubscribe you immediately. If you want to stay and see the fixed version when
I rebuild it, I'd appreciate that.

Either way, I appreciate your patience. Building in public means you see the wins
and the screwups. This was a screwup.

— Michael Rodriguez
Calling Digital
Dallas, TX
"""


def should_skip(to_addr: str) -> Optional[str]:
    """Return reason string if this recipient should be skipped, else None."""
    if not to_addr:
        return "empty address"
    addr = to_addr.lower().strip()
    if addr in SKIP_ADDRESSES:
        return f"skip-list: {addr}"
    domain = addr.split("@")[-1] if "@" in addr else ""
    if domain in SKIP_DOMAINS:
        return f"skip-domain: {domain}"
    return None


def connect_imap() -> imaplib.IMAP4_SSL:
    if not GMAIL_PASSWORD:
        log.error("MAIL_PASSWORD_CALLINGDIGITAL not set in environment")
        sys.exit(1)
    log.info(f"Connecting to IMAP {IMAP_HOST} as {GMAIL_USER}")
    imap = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    imap.login(GMAIL_USER, GMAIL_PASSWORD)
    return imap


def find_broken_emails(imap: imaplib.IMAP4_SSL) -> List[dict]:
    """Search sent folder for emails with raw merge tag in subject.

    Returns list of dicts with:
        - uid, message_id, to_addr, subject, date, references
    """
    imap.select('"[Gmail]/Sent Mail"', readonly=True)

    # Search for raw `{{person.first_name}}` pattern in subject
    # Gmail IMAP search is limited; we'll search broadly and filter locally
    status, data = imap.search(None, 'SUBJECT', '"{{person.first_name}}"')
    if status != "OK":
        log.error(f"IMAP search failed: {status}")
        return []

    uids = data[0].split()
    log.info(f"Found {len(uids)} candidate emails in Sent Mail")

    results = []
    for uid in uids:
        status, msg_data = imap.fetch(uid, "(RFC822.HEADER)")
        if status != "OK" or not msg_data:
            continue

        # Parse headers
        raw = msg_data[0][1] if isinstance(msg_data[0], tuple) else msg_data[0]
        msg = email.message_from_bytes(raw)

        subject = msg.get("Subject", "")
        if "{{person.first_name}}" not in subject:
            continue  # strict filter

        to_raw = msg.get("To", "")
        _, to_addr = email.utils.parseaddr(to_raw)

        results.append({
            "uid": uid.decode() if isinstance(uid, bytes) else uid,
            "message_id": msg.get("Message-ID", ""),
            "to_addr": to_addr,
            "to_raw": to_raw,
            "subject": subject,
            "date": msg.get("Date", ""),
            "references": msg.get("References", ""),
            "in_reply_to": msg.get("In-Reply-To", ""),
        })

    return results


def build_reply(original: dict, override_to: Optional[str] = None) -> EmailMessage:
    """Build an in-thread reply email with proper threading headers.

    override_to: if provided, sends the reply to this address instead of the
    original recipient (used for --test-to flag).
    """
    msg = EmailMessage()
    msg["From"] = f"Michael Rodriguez <{GMAIL_USER}>"
    if override_to:
        msg["To"] = override_to
    else:
        msg["To"] = original["to_raw"] or original["to_addr"]

    # Clean fixed subject — do NOT try to reuse the original subject (it has raw merge tags)
    msg["Subject"] = "Re: I owe you an apology"

    # Threading headers - makes this appear in the same Gmail thread as the broken email
    if original["message_id"] and not override_to:
        msg["In-Reply-To"] = original["message_id"]
        refs = original["references"]
        if refs:
            msg["References"] = f"{refs} {original['message_id']}"
        else:
            msg["References"] = original["message_id"]

    msg.set_content(APOLOGY_BODY)
    return msg


def send_smtp(msg: EmailMessage) -> bool:
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
            server.starttls()
            server.login(GMAIL_USER, GMAIL_PASSWORD)
            server.send_message(msg)
        return True
    except Exception as e:
        log.error(f"SMTP send failed: {type(e).__name__}: {e}")
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="List what would be sent, no sending")
    parser.add_argument("--live", action="store_true", help="Actually send the replies")
    parser.add_argument("--test-to", type=str, default="", help="Send ONE test apology to this address")
    args = parser.parse_args()

    if not args.dry_run and not args.live and not args.test_to:
        log.error("Specify --dry-run, --test-to <email>, or --live")
        sys.exit(1)

    # Connect and fetch
    imap = connect_imap()
    broken = find_broken_emails(imap)
    imap.logout()

    log.info(f"Total broken emails identified: {len(broken)}")

    # Dedupe by recipient (so each person only gets one apology even if they got multiple broken sends)
    seen_recipients = set()
    to_send = []
    skipped = []
    for item in broken:
        to = (item["to_addr"] or "").lower().strip()

        # Skip check
        reason = should_skip(to)
        if reason:
            skipped.append({"to": to, "reason": reason})
            continue

        # Dedupe
        if to in seen_recipients:
            skipped.append({"to": to, "reason": "duplicate"})
            continue
        seen_recipients.add(to)

        to_send.append(item)

    log.info(f"Unique recipients to apologize to: {len(to_send)}")
    log.info(f"Skipped: {len(skipped)}")
    for s in skipped[:15]:
        log.info(f"  SKIP: {s['to']} ({s['reason']})")
    if len(skipped) > 15:
        log.info(f"  ...and {len(skipped)-15} more skips")

    print()
    print(f"{'='*70}")
    print(f"APOLOGY REPLY PLAN")
    print(f"{'='*70}")
    print(f"Recipients:  {len(to_send)}")
    print(f"Skipped:     {len(skipped)}")
    print(f"From:        {GMAIL_USER}")
    print(f"Mode:        {'LIVE SEND' if args.live else 'DRY RUN'}")
    print()

    print("Will send to (sample 10):")
    for item in to_send[:10]:
        print(f"  -> {item['to_addr']:40s} | subj: {item['subject'][:50]}")
    if len(to_send) > 10:
        print(f"  ...and {len(to_send)-10} more")
    print()

    if args.dry_run:
        print("DRY RUN — no replies sent. Re-run with --live to execute.")
        return

    # Test send — send ONE apology to the test address using the first broken email as template
    if args.test_to:
        if not to_send:
            log.error("No broken emails found to use as template")
            return
        template = to_send[0]
        print(f"TEST SEND — sending ONE apology to {args.test_to}")
        print(f"Using template from broken email originally sent to: {template['to_addr']}")
        print(f"Subject: Re: I owe you an apology")
        print()
        msg = build_reply(template, override_to=args.test_to)
        ok = send_smtp(msg)
        if ok:
            print(f"✓ Test apology sent to {args.test_to}")
            print("Check that inbox, verify the format, then re-run with --live")
        else:
            print(f"✗ Test apology failed")
        return

    # Live send
    print(f"Sending {len(to_send)} apology replies...")
    sent_count = 0
    failed_count = 0
    log_path = Path(__file__).resolve().parent.parent / "logs" / "apology_replies.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a") as logfile:
        logfile.write(f"\n=== APOLOGY RUN {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
        for i, item in enumerate(to_send, 1):
            msg = build_reply(item)
            ok = send_smtp(msg)
            if ok:
                sent_count += 1
                logfile.write(f"  SENT: {item['to_addr']} | subject: {msg['Subject']}\n")
                print(f"  [{i}/{len(to_send)}] ✓ {item['to_addr']}")
            else:
                failed_count += 1
                logfile.write(f"  FAIL: {item['to_addr']}\n")
                print(f"  [{i}/{len(to_send)}] ✗ {item['to_addr']}")
            time.sleep(1.5)  # Gmail sending rate limit friendly

    print()
    print(f"DONE — sent={sent_count} failed={failed_count} log={log_path}")


if __name__ == "__main__":
    main()
