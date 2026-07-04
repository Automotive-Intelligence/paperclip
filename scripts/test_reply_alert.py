"""Manual end-to-end test for the hot-reply alert (P1): SMS + email.

notify_hot_reply fires BOTH an SMS (Twilio) and an email (Resend). This script
calls it once with dummy data and reports what each channel did, so we can prove
the paths as creds land. Email is the important one during the Twilio A2P 10DLC
review window (SMS is blocked at the carrier with error 30034 until approved).

For each channel this prints one of:
  SENT           provider accepted the send
  SKIPPED-no-config   the channel's creds/config are absent (clean no-op)

Note: the two channels share an in-process de-dupe. To see BOTH channels report
independently, this probes each channel's config directly (does not rely on the
combined notify_hot_reply return, which is True if EITHER channel fired).

Usage:
    # dry (no creds) — proves both guards no-op without error:
    python scripts/test_reply_alert.py

    # live — pull env from Doppler and actually send:
    doppler run -p paperclip -c prd -- python scripts/test_reply_alert.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.notifier import (  # noqa: E402
    notify_hot_reply,
    _twilio_config,
    _send_hot_reply_sms,
    _send_hot_reply_email,
    _get_hot_lead_logger,
)


DUMMY = dict(
    brand="avi",
    person_ref="dummy.prospect@example-dealer.com",
    channel="cold_email",
    snippet="Yes, interested. Can you send times to talk this week?",
)


def main() -> int:
    logger = _get_hot_lead_logger("hot_reply")

    sms_configured = _twilio_config() is not None
    email_configured = bool((os.environ.get("RESEND_API_KEY") or "").strip())

    print("=== Config presence ===")
    print(f"  SMS (Twilio):  {'configured' if sms_configured else 'NOT configured'}")
    print(f"  Email (Resend):{'configured' if email_configured else 'NOT configured'}")
    print()

    # Exercise each channel directly so both report independently (the shared
    # dedupe in notify_hot_reply would otherwise mask the second call in a rerun).
    print("=== Per-channel result ===")

    if sms_configured:
        sms_ok = _send_hot_reply_sms(logger, **DUMMY)
        print(f"  SMS:   {'SENT (Twilio accepted; carrier may hold until A2P)' if sms_ok else 'FAILED (see log)'}")
    else:
        print("  SMS:   SKIPPED-no-config")

    if email_configured:
        email_ok = _send_hot_reply_email(logger, **DUMMY)
        print(f"  Email: {'SENT (Resend accepted)' if email_ok else 'FAILED (see log)'}")
    else:
        print("  Email: SKIPPED-no-config")

    print()
    if not sms_configured and not email_configured:
        print("Both channels SKIPPED-no-config — notify_hot_reply no-op'd cleanly. Safe to deploy.")
    # Also prove the public entrypoint runs without raising (dedupe may suppress
    # the actual sends if a channel already fired above within the TTL).
    notify_hot_reply(**DUMMY)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
