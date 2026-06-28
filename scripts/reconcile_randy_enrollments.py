"""scripts/reconcile_randy_enrollments.py — re-enroll Randy's stuck GHL contacts.

ONE-SHOT cleanup for the 29 contacts CRO flagged 2026-06-27T21:45Z as stuck:
GHL has the tyler-prospect-* tag (so Randy keeps "finding" them as new) but
NOT the sequence-active tag (so Randy keeps re-enrolling them, and the PUT
keeps silently failing per the R2 fix).

R2 (PR #101) fixed the silent-failure path going forward — now every PUT
checks its response and logs LEAK: on failure. But the 29 already-stuck
contacts still need a one-shot reconciliation to actually land the
sequence-active tag in GHL.

USAGE
    # Dry run — list stuck contacts, don't write
    python scripts/reconcile_randy_enrollments.py --dry-run

    # Apply — re-PUT sequence-active on every stuck contact, log per-contact result
    python scripts/reconcile_randy_enrollments.py --apply

DESIGN
- Pulls every contact with a tyler-prospect-* tag via the same GHL search
  Randy uses (so the script + Randy see the same population).
- Filters to those WITHOUT sequence-active.
- For each, re-PUTs the tag list with sequence-active appended.
- Logs per-contact: contact_id, name, vertical, PUT status, error_detail.
- Final summary: attempted / succeeded / failed.

IDEMPOTENT. Safe to re-run — contacts already enrolled get skipped.
"""

import argparse
import os
import sys
import time

import requests

from rivers.ai_phone_guy.sequences import TAG_TO_VERTICAL

GHL_BASE = "https://services.leadconnectorhq.com"
_REQUEST_TIMEOUT = 15


def _ghl_key() -> str:
    t = (os.getenv("GHL_API_KEY") or "").strip()
    if not t:
        print("ERROR: GHL_API_KEY not set", file=sys.stderr)
        sys.exit(1)
    return t


def _ghl_location() -> str:
    t = (os.getenv("GHL_LOCATION_ID") or "").strip()
    if not t:
        print("ERROR: GHL_LOCATION_ID not set", file=sys.stderr)
        sys.exit(1)
    return t


def _ghl_headers() -> dict:
    return {
        "Authorization": f"Bearer {_ghl_key()}",
        "Content-Type": "application/json",
        "Version": "2021-07-28",
    }


def _find_stuck_contacts() -> list:
    """Pull every contact with a tyler-prospect-* tag, filter to those
    WITHOUT sequence-active. Returns list of contact dicts."""
    stuck = []
    for tag, vertical in TAG_TO_VERTICAL.items():
        params = {"locationId": _ghl_location(), "query": tag, "limit": 100}
        try:
            resp = requests.get(
                f"{GHL_BASE}/contacts/",
                headers=_ghl_headers(),
                params=params,
                timeout=_REQUEST_TIMEOUT,
            )
            if resp.status_code != 200:
                print(f"  WARN: GHL search failed for tag={tag!r}: {resp.status_code} {resp.text[:200]}")
                continue
            contacts = resp.json().get("contacts", [])
            for c in contacts:
                tags = c.get("tags", []) or []
                if tag in tags and "sequence-active" not in tags:
                    c["_vertical"] = vertical
                    c["_trigger_tag"] = tag
                    stuck.append(c)
        except Exception as e:
            print(f"  WARN: GHL search error for tag={tag!r}: {e}")
    return stuck


def _add_sequence_active(contact: dict) -> tuple[bool, str]:
    """Re-PUT this contact with sequence-active appended. Returns (ok, detail)."""
    cid = contact.get("id", "")
    current_tags = list(contact.get("tags", []) or [])
    if "sequence-active" in current_tags:
        return True, "already had sequence-active (concurrent fix?)"
    current_tags.append("sequence-active")
    try:
        resp = requests.put(
            f"{GHL_BASE}/contacts/{cid}",
            headers=_ghl_headers(),
            json={"tags": current_tags},
            timeout=_REQUEST_TIMEOUT,
        )
        if resp.status_code in (200, 201, 204):
            return True, f"status={resp.status_code}"
        return False, f"status={resp.status_code} body={resp.text[:200]}"
    except Exception as e:
        return False, f"exception: {e}"


def main():
    p = argparse.ArgumentParser(description="Reconcile Randy's stuck GHL enrollments.")
    p.add_argument("--dry-run", action="store_true", help="List stuck contacts, don't write.")
    p.add_argument("--apply", action="store_true", help="Apply the fix — re-PUT sequence-active.")
    p.add_argument("--rate-limit-sec", type=float, default=0.25,
                   help="Sleep between PUTs to avoid rate-limit (default 0.25s = ~4 req/s).")
    args = p.parse_args()

    if not args.dry_run and not args.apply:
        print("ERROR: pass --dry-run OR --apply", file=sys.stderr)
        sys.exit(2)

    print("=== Randy stuck-enrollment reconciliation ===")
    print(f"Mode: {'DRY RUN' if args.dry_run else 'APPLY'}")
    print()
    print("Pulling tyler-prospect-* tagged contacts from GHL...")
    stuck = _find_stuck_contacts()
    print(f"Found {len(stuck)} stuck contacts (tagged, but no sequence-active).")
    print()

    if not stuck:
        print("Nothing to reconcile. Done.")
        return

    print(f"{'idx':>4}  {'contact_id':<36}  {'vertical':<15}  {'name':<30}")
    print("-" * 95)
    for i, c in enumerate(stuck):
        first = c.get("firstName", "") or ""
        last = c.get("lastName", "") or ""
        name = f"{first} {last}".strip() or c.get("email", "")[:30] or "(no name)"
        print(f"{i+1:>4}  {c.get('id', ''):<36}  {c.get('_vertical', ''):<15}  {name[:30]:<30}")

    if args.dry_run:
        print()
        print("DRY RUN — no writes. Re-run with --apply to fix.")
        return

    print()
    print(f"Applying fix — re-PUTting sequence-active tag with {args.rate_limit_sec}s between calls...")
    print()

    succeeded = 0
    failed = 0
    per_failure: list = []

    for i, c in enumerate(stuck):
        ok, detail = _add_sequence_active(c)
        cid = c.get("id", "")
        first = c.get("firstName", "") or ""
        last = c.get("lastName", "") or ""
        name = f"{first} {last}".strip() or c.get("email", "")[:30] or "(no name)"
        flag = "✓" if ok else "✗"
        print(f"  {flag} {i+1:>3}/{len(stuck)}  {cid:<36}  {name[:25]:<25}  {detail}")
        if ok:
            succeeded += 1
        else:
            failed += 1
            per_failure.append({"contact_id": cid, "name": name, "detail": detail})
        time.sleep(args.rate_limit_sec)

    print()
    print("=== Reconciliation summary ===")
    print(f"  Attempted: {len(stuck)}")
    print(f"  Succeeded: {succeeded}")
    print(f"  Failed:    {failed}")
    if per_failure:
        print()
        print("Failures (re-run after diagnosis):")
        for f in per_failure:
            print(f"  - {f['contact_id']}  {f['name']}  →  {f['detail']}")

    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
