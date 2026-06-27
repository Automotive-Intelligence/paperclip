# AVO — AI Business Operating System
# Postal Agent — multi-account connection audit
# Salesdroid — June 2026

"""Audit which Postal Agent inboxes are actually connected.

Phase 1 OAuth had two bugs (PKCE, email-resolution) fixed in #56/#57. Any
account connected *before* those fixes may have silently failed to persist a
token. This script reconciles the six expected account labels against the
postal_tokens / postal_state tables and (optionally) makes a live Gmail
getProfile call to prove each stored refresh token still works.

Usage:
    railway run python scripts/postal_audit.py              # DB rows only
    railway run python scripts/postal_audit.py --check-live # + live token test

Exit code is non-zero if any expected account is missing or broken, so this
can gate a deploy / be wired into CI later.
"""

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.append(str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

from services.database import fetch_all  # noqa: E402

# The six inboxes the Postal Agent is meant to cover (mirrors
# postal_oauth.VALID_ACCOUNT_LABELS — kept here so the audit runs even if that
# module's heavy deps aren't importable).
EXPECTED_ACCOUNTS = ["avi", "wd", "salesdroid", "aipg", "agentempire", "bookd"]


def _token_rows():
    rows = fetch_all(
        "SELECT account_label, email, status, last_reauth_at FROM postal_tokens",
        (),
    )
    return {r[0]: {"email": r[1], "status": r[2], "last_reauth_at": r[3]} for r in rows}


def _state_rows():
    try:
        rows = fetch_all(
            "SELECT account_label, last_history_id, last_synced_at, sync_count, last_error "
            "FROM postal_state",
            (),
        )
    except Exception as e:
        print(f"  (could not read postal_state: {e})")
        return {}
    return {
        r[0]: {"last_history_id": r[1], "last_synced_at": r[2], "sync_count": r[3], "last_error": r[4]}
        for r in rows
    }


def _live_check(account_label: str) -> str:
    """Return 'ok <email>' or 'FAIL <reason>' from a real Gmail getProfile call."""
    try:
        from tools import gmail_multi
        prof = gmail_multi.get_profile(account_label)
        return f"ok ({prof.get('emailAddress')}, historyId={prof.get('historyId')})"
    except Exception as e:
        return f"FAIL ({type(e).__name__}: {str(e)[:120]})"


def main() -> int:
    ap = argparse.ArgumentParser(description="Audit Postal Agent inbox connections")
    ap.add_argument("--check-live", action="store_true",
                    help="Make a live Gmail getProfile call per account to verify the token works")
    args = ap.parse_args()

    tokens = _token_rows()
    states = _state_rows()

    print("=" * 72)
    print("POSTAL AGENT — CONNECTION AUDIT")
    print("=" * 72)

    problems = []
    for acct in EXPECTED_ACCOUNTS:
        tok = tokens.get(acct)
        st = states.get(acct, {})
        if not tok:
            print(f"\n[{acct:11}] ❌ NOT CONNECTED — no postal_tokens row")
            print(f"{'':14}→ connect via /oauth/google/start?account={acct}")
            problems.append(acct)
            continue

        status = tok["status"]
        icon = "✅" if status == "active" else "⚠️"
        print(f"\n[{acct:11}] {icon} {status.upper()}  email={tok['email']}")
        print(f"{'':14}last_reauth={tok['last_reauth_at']}")
        if st:
            print(f"{'':14}synced={st.get('last_synced_at')} count={st.get('sync_count')} "
                  f"history_id={st.get('last_history_id')}")
            if st.get("last_error"):
                print(f"{'':14}⚠️ last_error: {st['last_error'][:160]}")
        else:
            print(f"{'':14}(no postal_state row yet — never synced)")

        if status != "active":
            problems.append(acct)

        if args.check_live:
            result = _live_check(acct)
            live_icon = "✅" if result.startswith("ok") else "❌"
            print(f"{'':14}live token test: {live_icon} {result}")
            if not result.startswith("ok") and acct not in problems:
                problems.append(acct)

    # Unexpected labels present in the DB but not in our expected set.
    extra = sorted(set(tokens) - set(EXPECTED_ACCOUNTS))
    if extra:
        print(f"\nℹ️ Unexpected account labels in postal_tokens: {extra}")

    print("\n" + "=" * 72)
    connected = [a for a in EXPECTED_ACCOUNTS if a in tokens]
    print(f"Connected: {len(connected)}/{len(EXPECTED_ACCOUNTS)}  "
          f"({', '.join(connected) or 'none'})")
    if problems:
        print(f"⚠️ Needs attention: {', '.join(sorted(set(problems)))}")
    else:
        print("✅ All expected inboxes are connected and active.")
    print("=" * 72)

    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
