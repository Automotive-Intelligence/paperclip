#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["google-api-python-client>=2.100", "google-auth>=2.20"]
# ///
"""
Pull Google Search Console data (Search Analytics) + inspect indexing status,
KEYLESS via Application Default Credentials (ADC).

Setup once (no service-account key needed, respects the org key-creation policy):
  gcloud auth application-default login
  gcloud auth application-default set-quota-project avo-analytics-501202
  # your Google account must have access to the GSC property (Full or Owner)

Examples:
  # top queries for a property, last 28 days
  uv run gsc_pull.py --site https://worshipdigital.co/ \
    --start 2026-06-01 --end 2026-06-28 --dimensions query --limit 25

  # is a URL indexed?
  uv run gsc_pull.py --site https://automotiveintelligence.io/ \
    --inspect https://automotiveintelligence.io/diagnostic-call

Note: --site must be EXACTLY as verified in Search Console. Domain properties use
the form  sc-domain:example.com  ; URL-prefix properties use the full https URL.
"""
import argparse
import json
import sys

import google.auth
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]


def _service():
    creds, _ = google.auth.default(scopes=SCOPES)
    return build("searchconsole", "v1", credentials=creds, cache_discovery=False)


def search_analytics(site, start, end, dimensions, limit):
    svc = _service()
    body = {
        "startDate": start,
        "endDate": end,
        "dimensions": dimensions,
        "rowLimit": limit,
    }
    resp = svc.searchanalytics().query(siteUrl=site, body=body).execute()
    rows = []
    for r in resp.get("rows", []):
        entry = dict(zip(dimensions, r.get("keys", [])))
        entry.update({
            "clicks": r.get("clicks"),
            "impressions": r.get("impressions"),
            "ctr": round(r.get("ctr", 0), 4),
            "position": round(r.get("position", 0), 2),
        })
        rows.append(entry)
    return {"site": site, "range": {"start": start, "end": end},
            "dimensions": dimensions, "row_count": len(rows), "rows": rows}


def inspect(site, url):
    svc = _service()
    body = {"inspectionUrl": url, "siteUrl": site}
    resp = svc.urlInspection().index().inspect(body=body).execute()
    r = resp.get("inspectionResult", {}).get("indexStatusResult", {})
    return {
        "url": url,
        "coverageState": r.get("coverageState"),
        "verdict": r.get("verdict"),
        "lastCrawlTime": r.get("lastCrawlTime"),
        "googleCanonical": r.get("googleCanonical"),
        "robotsTxtState": r.get("robotsTxtState"),
        "indexingState": r.get("indexingState"),
    }


def main():
    p = argparse.ArgumentParser(description="Keyless GSC pull (ADC).")
    p.add_argument("--site", required=True, help="verified property, e.g. https://site/ or sc-domain:site")
    p.add_argument("--start")
    p.add_argument("--end")
    p.add_argument("--dimensions", default="query", help="comma list: query,page,country,device,date")
    p.add_argument("--limit", type=int, default=25)
    p.add_argument("--inspect", help="a single URL to inspect indexing status for")
    args = p.parse_args()

    if args.inspect:
        print(json.dumps(inspect(args.site, args.inspect), indent=2))
        return
    if not (args.start and args.end):
        print("provide --start and --end for search analytics (or --inspect a url)", file=sys.stderr)
        sys.exit(2)
    dims = [d.strip() for d in args.dimensions.split(",") if d.strip()]
    print(json.dumps(search_analytics(args.site, args.start, args.end, dims, args.limit), indent=2))


if __name__ == "__main__":
    main()
