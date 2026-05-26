"""Paper & Purpose — coming-soon contacts CSV importer to Klaviyo.

One-time bootstrap import of the pre-launch contacts collected outside
Shopify (Squarespace coming-soon page, manual signups, Mailchimp export,
etc.) into a Klaviyo subscriber list, in time for the 2026-05-29 pre-sale
welcome flow.

Future ongoing additions flow through Klaviyo's native Shopify integration
and don't need this script. This script is for the bootstrap moment only.

Usage:

    # First, sanity check the CSV columns:
    python scripts/pp_import_contacts.py --csv path/to/contacts.csv --inspect

    # Dry-run shows what would happen, including which list it would
    # create/use and first 3 rows transformed:
    python scripts/pp_import_contacts.py \\
        --csv path/to/contacts.csv \\
        --list-name "Pre-launch interest list" \\
        --dry-run

    # Live import (idempotent on email; safe to re-run):
    python scripts/pp_import_contacts.py \\
        --csv path/to/contacts.csv \\
        --list-name "Pre-launch interest list"

Required env var:
    KLAVIYO_API_KEY_PAPERANDPURPOSE  Klaviyo private API key for the P&P
                                     account. Get from Klaviyo Settings →
                                     API Keys. Needs the "Full Access" or
                                     at minimum List + Profile write scopes.

Idempotency: Klaviyo's profile API is keyed on email; re-running this
script on the same CSV will update existing profiles rather than
duplicate them. Adding a profile to a list it is already on is a no-op.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path
from typing import Any

# Ensure we can import the project's tools module.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Load Paperclip's .env so KLAVIYO_API_KEY_PAPERANDPURPOSE is visible
# to _api_key_for(). The CrewAI runtime loads this automatically; a
# standalone script needs to do it explicitly.
try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv(REPO_ROOT / ".env")
except ImportError:
    # dotenv is part of Paperclip's deps but tolerate its absence.
    pass

# Reuse the existing Klaviyo tool's HTTP helpers so we hit the same
# auth, error-handling, and base-URL surface that Sofia uses. We call
# _klaviyo_request directly (not the @tool-decorated wrappers, which
# are Tool objects and not directly callable outside CrewAI).
from tools.klaviyo import _klaviyo_request, _api_key_for  # noqa: E402

BUSINESS_KEY = "paperandpurpose"

# Column-name candidates. The CSV will be checked against these in order;
# whichever matches first wins. Case-insensitive.
EMAIL_COLS = ["email", "email_address", "e-mail", "Email", "EmailAddress"]
FIRST_COLS = ["first_name", "firstname", "first", "given_name", "First Name"]
LAST_COLS = ["last_name", "lastname", "last", "family_name", "surname", "Last Name"]


# ----------------------------------------------------------------------
# CSV handling
# ----------------------------------------------------------------------

def _detect_column(fieldnames: list[str], candidates: list[str]) -> str | None:
    """Return the first candidate that appears in fieldnames, case-insensitive."""
    lower_to_real = {f.lower(): f for f in fieldnames}
    for cand in candidates:
        if cand.lower() in lower_to_real:
            return lower_to_real[cand.lower()]
    return None


def _read_csv(csv_path: Path) -> tuple[list[dict[str, str]], dict[str, str | None]]:
    """Return (rows, column_map). column_map = {email, first, last} -> real col name or None."""
    with csv_path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise SystemExit(f"ERROR: CSV {csv_path} has no header row.")
        col_map = {
            "email": _detect_column(reader.fieldnames, EMAIL_COLS),
            "first": _detect_column(reader.fieldnames, FIRST_COLS),
            "last": _detect_column(reader.fieldnames, LAST_COLS),
        }
        if col_map["email"] is None:
            raise SystemExit(
                f"ERROR: CSV {csv_path} has no recognizable email column. "
                f"Looked for: {EMAIL_COLS}. Found columns: {reader.fieldnames}."
            )
        rows = [r for r in reader]
    return rows, col_map


def _normalize(row: dict[str, str], col_map: dict[str, str | None]) -> dict[str, str]:
    email = (row.get(col_map["email"] or "", "") or "").strip().lower()
    first = (row.get(col_map["first"] or "", "") or "").strip() if col_map["first"] else ""
    last = (row.get(col_map["last"] or "", "") or "").strip() if col_map["last"] else ""
    return {"email": email, "first_name": first, "last_name": last}


# ----------------------------------------------------------------------
# Klaviyo write path
# ----------------------------------------------------------------------

def _find_or_create_list(list_name: str, dry_run: bool) -> str | None:
    """Return the Klaviyo list_id for list_name, creating it if absent.

    Calls _klaviyo_request directly so this works as a standalone script
    (outside the CrewAI Tool wrapper). Paths follow the convention used
    inside tools/klaviyo.py: no leading slash, no /api prefix.
    """
    resp = _klaviyo_request("GET", BUSINESS_KEY, "lists/")
    if isinstance(resp, str) and resp.startswith("ERROR"):
        raise SystemExit(resp)
    items = resp.get("data", []) if isinstance(resp, dict) else []
    for item in items:
        attrs = item.get("attributes", {})
        if attrs.get("name") == list_name:
            list_id = item.get("id")
            print(f"  list found: {list_name!r} -> id={list_id}")
            return list_id

    if dry_run:
        print(f"  (dry-run) would create list: {list_name!r}")
        return None

    created = _klaviyo_request(
        "POST",
        BUSINESS_KEY,
        "lists/",
        json_body={
            "data": {
                "type": "list",
                "attributes": {"name": list_name},
            }
        },
    )
    if isinstance(created, str) and created.startswith("ERROR"):
        raise SystemExit(created)
    list_id = created.get("data", {}).get("id") if isinstance(created, dict) else None
    if not list_id:
        raise SystemExit(f"ERROR: list creation returned no id: {created}")
    print(f"  list created: {list_name!r} -> id={list_id}")
    return list_id


def _upsert_profile_and_subscribe(list_id: str, profile: dict[str, str]) -> dict[str, Any] | str:
    """Upsert profile by email and subscribe to list_id.

    Uses Klaviyo's subscription-jobs endpoint which handles both new and
    existing profiles in a single call. This is the public-API-supported
    path for subscribing a known email to a list with implicit consent.
    """
    body = {
        "data": {
            "type": "profile-subscription-bulk-create-job",
            "attributes": {
                "profiles": {
                    "data": [
                        {
                            "type": "profile",
                            "attributes": {
                                "email": profile["email"],
                                **(
                                    {"first_name": profile["first_name"]}
                                    if profile["first_name"]
                                    else {}
                                ),
                                **(
                                    {"last_name": profile["last_name"]}
                                    if profile["last_name"]
                                    else {}
                                ),
                                "subscriptions": {
                                    "email": {
                                        "marketing": {"consent": "SUBSCRIBED"}
                                    }
                                },
                            },
                        }
                    ]
                },
                "historical_import": False,
            },
            "relationships": {
                "list": {"data": {"type": "list", "id": list_id}}
            },
        }
    }
    return _klaviyo_request(
        "POST",
        BUSINESS_KEY,
        "profile-subscription-bulk-create-jobs/",
        json_body=body,
    )


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", required=True, type=Path,
                        help="Path to the CSV of contacts to import.")
    parser.add_argument("--list-name", default="Pre-launch interest list",
                        help="Klaviyo list name. Created if it does not exist.")
    parser.add_argument("--inspect", action="store_true",
                        help="Just inspect the CSV columns and first 3 rows; no API calls.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would happen, but do not write to Klaviyo.")
    parser.add_argument("--sleep", type=float, default=0.2,
                        help="Seconds to wait between Klaviyo calls (rate limit safety).")
    args = parser.parse_args()

    csv_path: Path = args.csv
    if not csv_path.exists():
        raise SystemExit(f"ERROR: CSV not found at {csv_path}")

    rows, col_map = _read_csv(csv_path)
    print(f"CSV: {csv_path}")
    print(f"  rows: {len(rows)}")
    print(f"  email column: {col_map['email']!r}")
    print(f"  first column: {col_map['first']!r}")
    print(f"  last column: {col_map['last']!r}")

    if args.inspect:
        print()
        print("First 3 normalized rows:")
        for r in rows[:3]:
            print(f"  {_normalize(r, col_map)}")
        return

    # Credential check before any list / profile work.
    if not _api_key_for(BUSINESS_KEY):
        raise SystemExit(
            "ERROR: KLAVIYO_API_KEY_PAPERANDPURPOSE not set in paperclip env. "
            "Get it from Klaviyo Settings -> API Keys (private key with "
            "List + Profile write scopes), then export or add to ~/paperclip/.env."
        )

    list_id = _find_or_create_list(args.list_name, dry_run=args.dry_run)
    if args.dry_run and list_id is None:
        print()
        print(f"(dry-run) would import {len(rows)} contacts.")
        print("First 3 normalized rows that would be sent:")
        for r in rows[:3]:
            print(f"  {_normalize(r, col_map)}")
        return

    print()
    print(f"Importing {len(rows)} contacts into list id {list_id} ...")
    ok = 0
    skip = 0
    err = 0
    for i, raw in enumerate(rows, start=1):
        profile = _normalize(raw, col_map)
        if not profile["email"] or "@" not in profile["email"]:
            skip += 1
            continue

        if args.dry_run:
            if i <= 3:
                print(f"  (dry-run) would subscribe: {profile}")
            ok += 1
            continue

        result = _upsert_profile_and_subscribe(list_id, profile)
        if isinstance(result, str) and result.startswith("ERROR:"):
            err += 1
            print(f"  [{i}/{len(rows)}] FAIL {profile['email']}: {result[:200]}")
        else:
            ok += 1
            if i % 25 == 0 or i == len(rows):
                print(f"  [{i}/{len(rows)}] subscribed (running total: ok={ok}, skip={skip}, err={err})")
        time.sleep(args.sleep)

    print()
    print(f"Done. ok={ok}  skipped={skip}  errors={err}")


if __name__ == "__main__":
    main()
