#!/usr/bin/env python3
"""
indexnow_submit.py — Polaris (Growth & Analytics) discovery-feed tool.

Submits URLs to IndexNow (https://www.indexnow.org), which instantly notifies
Bing, Yandex, Seznam, Naver and other participating engines that pages are new
or updated. Google does NOT use IndexNow (Google relies on fresh sitemap lastmod
+ GSC), so this is the "everything except Google" half of the discovery feed.

Prereq: the key file must be live at https://<host>/<key>.txt containing exactly
the key string. This script does NOT deploy that file (that ships with the site);
it only submits.

Usage:
  # submit every URL in a live sitemap
  python3 indexnow_submit.py --host automotiveintelligence.io \
      --key 58a2657414ebc06e6ad6d9c5806da61d --sitemap

  # submit an explicit list
  python3 indexnow_submit.py --host buildagentempire.com \
      --key <key> --url https://buildagentempire.com/

The key here (58a2657414ebc06e6ad6d9c5806da61d) is derived deterministically as
sha256("avo-indexnow-key-2026")[:32] so it is reproducible, not secret-random.
"""
import argparse
import json
import sys
import ssl
import urllib.request
import urllib.error
import re

try:
    import certifi
    _SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except Exception:  # certifi missing — fall back to system default
    _SSL_CTX = ssl.create_default_context()

INDEXNOW_ENDPOINT = "https://api.indexnow.org/indexnow"


def urls_from_sitemap(host: str) -> list[str]:
    sm = f"https://{host}/sitemap.xml"
    req = urllib.request.Request(sm, headers={"User-Agent": "polaris-indexnow/1.0"})
    with urllib.request.urlopen(req, timeout=20, context=_SSL_CTX) as r:
        body = r.read().decode("utf-8", "replace")
    return re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", body)


def submit(host: str, key: str, urls: list[str], key_location: str | None) -> int:
    payload = {
        "host": host,
        "key": key,
        "keyLocation": key_location or f"https://{host}/{key}.txt",
        "urlList": urls,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        INDEXNOW_ENDPOINT,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8",
                 "User-Agent": "polaris-indexnow/1.0"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30, context=_SSL_CTX) as r:
            print(f"IndexNow {host}: HTTP {r.status} ({len(urls)} urls) "
                  f"[200/202 = accepted]")
            return r.status
    except urllib.error.HTTPError as e:
        print(f"IndexNow {host}: HTTP {e.code} — {e.read().decode('utf-8','replace')[:300]}",
              file=sys.stderr)
        return e.code


def main() -> int:
    ap = argparse.ArgumentParser(description="Submit URLs to IndexNow")
    ap.add_argument("--host", required=True, help="bare host, e.g. example.com")
    ap.add_argument("--key", required=True, help="IndexNow key (matches /<key>.txt)")
    ap.add_argument("--sitemap", action="store_true",
                    help="pull the URL list from https://<host>/sitemap.xml")
    ap.add_argument("--url", action="append", default=[],
                    help="explicit URL to submit (repeatable)")
    ap.add_argument("--key-location", default=None,
                    help="override key file URL (default https://<host>/<key>.txt)")
    args = ap.parse_args()

    urls = list(args.url)
    if args.sitemap:
        urls += urls_from_sitemap(args.host)
    urls = sorted(set(urls))
    if not urls:
        print("no URLs to submit (pass --sitemap or --url)", file=sys.stderr)
        return 2

    status = submit(args.host, args.key, urls, args.key_location)
    return 0 if status in (200, 202) else 1


if __name__ == "__main__":
    raise SystemExit(main())
