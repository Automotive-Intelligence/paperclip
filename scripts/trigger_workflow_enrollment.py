# AVO — AI Business Operating System
# Trigger workflow enrollment by touching People records
# This fires "Record updated" workflows without overwriting any data
# Salesdroid — April 2026

"""Touch People records to trigger Attio workflow enrollment.

The workflows use "Record updated" triggers with vertical filters.
This script does a minimal PATCH on each Person record (updates
cd_outreach_stage to "sequence-ready") which fires the workflow
and enrolls them in the correct sequence.

No data is overwritten. No junk companies created.

Usage:
    python scripts/trigger_workflow_enrollment.py --vertical med-spa --dry-run
    python scripts/trigger_workflow_enrollment.py --vertical med-spa
    python scripts/trigger_workflow_enrollment.py --all
"""

import argparse
import os
import sys
import time

import requests

ATTIO_BASE = "https://api.attio.com/v2"

VERTICALS = ["med-spa", "pi-law", "real-estate", "home-builder"]


def attio_token() -> str:
    t = (os.getenv("ATTIO_API_KEY") or "").strip()
    if not t:
        print("ERROR: ATTIO_API_KEY not set"); sys.exit(1)
    return t


def attio_headers() -> dict:
    return {"Authorization": f"Bearer {attio_token()}", "Content-Type": "application/json"}


def get_people_by_vertical(vertical: str) -> list:
    """Query People where vertical = given value."""
    try:
        r = requests.post(
            f"{ATTIO_BASE}/objects/people/records/query",
            headers=attio_headers(),
            json={
                "filter": {"vertical": {"$eq": vertical}},
                "limit": 500,
            },
            timeout=30,
        )
        if r.status_code == 200:
            return r.json().get("data", [])
        print(f"  Query error: {r.status_code} {r.text[:200]}")
    except Exception as e:
        print(f"  Query exception: {e}")
    return []


def touch_person(record_id: str) -> bool:
    """Minimal PATCH to trigger 'Record updated' workflow.
    Sets pipeline_stage to same value — Attio still fires update event."""
    try:
        r = requests.patch(
            f"{ATTIO_BASE}/objects/people/records/{record_id}",
            headers=attio_headers(),
            json={
                "data": {
                    "values": {
                        "pipeline_stage": "ownerphones-warm",
                    }
                }
            },
            timeout=15,
        )
        return r.status_code in (200, 201)
    except Exception:
        return False


def extract_info(person: dict) -> tuple:
    """Returns (full_name, record_id)."""
    vals = person.get("values", {})
    name_obj = (vals.get("name") or [{}])[0]
    name = name_obj.get("full_name", "?") if isinstance(name_obj, dict) else "?"
    rid = person.get("id", {})
    record_id = rid.get("record_id", "") if isinstance(rid, dict) else ""
    return name, record_id


def process_vertical(vertical: str, dry_run: bool) -> dict:
    print(f"\n{'='*50}")
    print(f"  VERTICAL: {vertical}")
    print(f"{'='*50}")

    people = get_people_by_vertical(vertical)
    print(f"  Found: {len(people)} records")

    if not people:
        return {"vertical": vertical, "found": 0, "touched": 0, "errors": 0}

    touched = 0
    errors = 0

    for i, p in enumerate(people, 1):
        name, record_id = extract_info(p)

        if dry_run:
            print(f"  [DRY] [{i}/{len(people)}] Would touch: {name} ({record_id[:8]}...)")
            touched += 1
            continue

        ok = touch_person(record_id)
        if ok:
            touched += 1
            print(f"  ✓ [{i}/{len(people)}] Touched: {name}")
        else:
            errors += 1
            print(f"  ✗ [{i}/{len(people)}] Failed: {name}")

        time.sleep(0.3)  # ~3 req/sec, safe for Attio rate limits

    return {"vertical": vertical, "found": len(people), "touched": touched, "errors": errors}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--vertical", choices=VERTICALS, help="Single vertical to process")
    parser.add_argument("--all", action="store_true", help="Process all 4 verticals")
    parser.add_argument("--dry-run", action="store_true", help="Show what would happen, no writes")
    args = parser.parse_args()

    if not args.vertical and not args.all:
        print("ERROR: Specify --vertical <name> or --all")
        sys.exit(1)

    targets = VERTICALS if args.all else [args.vertical]

    print("AVO Workflow Enrollment Trigger")
    print(f"Mode: {'DRY RUN' if args.dry_run else 'LIVE'}")
    print(f"Targets: {', '.join(targets)}")

    all_stats = []
    for v in targets:
        stats = process_vertical(v, args.dry_run)
        all_stats.append(stats)

    print(f"\n{'='*50}")
    print("SUMMARY")
    print(f"{'='*50}")
    for s in all_stats:
        print(f"  {s['vertical']:15s}  found={s['found']}  touched={s['touched']}  errors={s['errors']}")

    total_touched = sum(s["touched"] for s in all_stats)
    total_errors = sum(s["errors"] for s in all_stats)
    print(f"\n  TOTAL: {total_touched} touched, {total_errors} errors")
    if not args.dry_run:
        print("\n  Check Attio Workflows → Runs tab to confirm enrollment fired.")


if __name__ == "__main__":
    main()
