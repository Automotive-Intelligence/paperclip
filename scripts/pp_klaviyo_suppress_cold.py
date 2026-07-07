#!/usr/bin/env python3
"""
One-off: suppress the DataMoon cold-import emails in Klaviyo so the account
drops back under the free-tier active-profile limit. Protects real subscribers
by excluding members of the genuine opted-in lists. Suppression is REVERSIBLE.

Run: cd ~/paperclip && python3 scripts/pp_klaviyo_suppress_cold.py [--go]
Without --go: dry run (counts only, no writes).
"""
import csv, os, sys, time
import requests
from dotenv import load_dotenv

load_dotenv()
KEY = (os.getenv("KLAVIYO_API_KEY_PAPERANDPURPOSE") or "").strip()
assert KEY, "KLAVIYO_API_KEY_PAPERANDPURPOSE not set"
BASE = "https://a.klaviyo.com/api"
H = {"Authorization": f"Klaviyo-API-Key {KEY}", "revision": "2024-10-15",
     "accept": "application/json", "content-type": "application/json"}
COLD_FILE = os.path.expanduser("~/avo-telemetry/marketing_deliverables/pp_intent_leads/ad_audiences/pp_meta_ALL.csv")
# genuine opted-in lists to PROTECT (never suppress their members)
PROTECT_LISTS = {"SsMyVv": "Email List", "USLsqg": "Pre-launch", "SnaiA7": "Preview",
                 "WEEnz6": "Text Messaging", "WaPbWx": "Site Visitor Retargeting"}

def req(method, path, body=None):
    r = requests.request(method, BASE + path, headers=H, json=body, timeout=30)
    try:
        j = r.json() if r.text.strip() else {}
    except Exception:
        j = {"error": r.text[:300]}
    return r.status_code, j

def list_members(lid):
    emails = set(); path = f"/lists/{lid}/profiles/?page[size]=100&fields[profile]=email"
    while path:
        st, j = req("GET", path)
        if st != 200: break
        for d in j.get("data", []):
            em = (d.get("attributes", {}).get("email") or "").strip().lower()
            if em: emails.add(em)
        nxt = (j.get("links", {}) or {}).get("next")
        path = nxt.replace(BASE, "") if nxt else None
    return emails

def main(go):
    cold = set()
    with open(COLD_FILE, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            e = (r.get("email") or "").strip().lower()
            if e: cold.add(e)
    protect = set()
    for lid, name in PROTECT_LISTS.items():
        m = list_members(lid); protect |= m
        print(f"  protect list {name} ({lid}): {len(m)} members")
    targets = sorted(cold - protect)
    print(f"\nCold emails: {len(cold)} | protected (real subscribers): {len(protect)} | TO SUPPRESS: {len(targets)}")
    if not go:
        print("\nDRY RUN (no writes). Re-run with --go to suppress.")
        return
    # suppress in batches of 100
    sup = 0; failed = 0
    for i in range(0, len(targets), 100):
        batch = targets[i:i+100]
        body = {"data": {"type": "profile-suppression-bulk-create-job",
                "attributes": {"profiles": {"data": [
                    {"type": "profile", "attributes": {"email": e}} for e in batch]}}}}
        st, j = req("POST", "/profile-suppression-bulk-create-jobs/", body)
        if st in (200, 201, 202):
            sup += len(batch)
        else:
            failed += len(batch)
            if i == 0:
                print(f"FIRST BATCH FAILED ({st}): {j}")
                print("Stopping so we don't hammer a bad schema.")
                return
        if (i // 100) % 10 == 0:
            print(f"  suppressed {sup}/{len(targets)} (failed {failed})")
        time.sleep(0.3)
    print(f"\nDONE. Suppressed {sup}, failed {failed}.")

if __name__ == "__main__":
    main("--go" in sys.argv)
