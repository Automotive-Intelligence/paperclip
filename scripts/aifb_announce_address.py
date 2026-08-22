"""One-time venue address announcement for AI for Business DFW registrants.

Fulfills the promise the confirmation SMS makes ("exact address by text").
Run on venue-lock day, dry-run first:

    doppler run -p paperclip -c prd -- python3 scripts/aifb_announce_address.py \
        --address "129 S Main St #260, Grapevine" --dry-run
"""
import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from services.aifb_reminders import DETAILS_URL, _send_sms, list_registrants  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--address", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    registrants = list_registrants()
    print(f"{len(registrants)} registrant(s) with phone numbers")
    for r in registrants:
        msg = (
            f"Hey {r['first']}, Michael here. The room for AI for Business DFW "
            f"is locked: {args.address}. Doors at 6:00 on meetup night. "
            f"Details: {DETAILS_URL}"
        )
        if args.dry_run:
            print(f"DRY RUN -> {r['phone']}: {msg}")
        else:
            _send_sms(r["id"], msg)
            print(f"sent -> {r['phone']}")


if __name__ == "__main__":
    main()
