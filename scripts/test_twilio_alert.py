"""Manual end-to-end test for the hot-reply SMS alert (P1).

Calls core.notifier.notify_hot_reply once with dummy data so we can prove the
full path AFTER Twilio creds + A2P land in Doppler paperclip/prd.

Behavior:
  - If TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN / TWILIO_FROM / MICHAEL_PHONE are
    all set, this actually texts Michael and prints "SENT".
  - If any of those env vars is missing, the helper no-ops cleanly (logs only)
    and this prints "SKIPPED (no creds) — helper is safe to deploy".

Usage:
    # dry (no creds set) — just proves the guard no-ops without error:
    python scripts/test_twilio_alert.py

    # live (after creds land) — pull env from Doppler and actually text:
    doppler run -p paperclip -c prd -- python scripts/test_twilio_alert.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.notifier import notify_hot_reply  # noqa: E402


def main() -> int:
    # Mirror the helper's own guard so the test reports accurately whether creds
    # are complete. Supports either a Standard API key (preferred) or the
    # account Auth Token.
    from core.notifier import _twilio_config  # noqa: E402
    creds_present = _twilio_config() is not None

    sent = notify_hot_reply(
        brand="avi",
        person_ref="dummy.prospect@example-dealer.com",
        channel="cold_email",
        snippet="Yes, interested. Can you send times to talk this week?",
    )

    if sent:
        print("SENT — Twilio accepted the message. Check Michael's phone.")
        return 0
    if creds_present:
        print("NOT SENT — creds were present but the Twilio POST did not confirm. Check logs/hot_reply_hot_leads.log.")
        return 1
    print("SKIPPED (no creds) — helper no-op'd cleanly. Safe to deploy. Set TWILIO_* + MICHAEL_PHONE to send for real.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
