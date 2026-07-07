"""Paper & Purpose — upload header PNG to Klaviyo CDN, inject into 5 templates.

Steps:
  1. Upload assets/pp_email_header.png to Klaviyo via POST /api/image-upload/
     (multipart form, returns a CDN URL).
  2. For each of the 5 welcome templates: GET HTML, insert the header
     <div><img ...></div> block immediately after the inner content table's
     opening <td>, PATCH the template.
  3. Verify each template now contains the header img tag.

Idempotent: re-running is safe.
  - Image upload is skipped if an image named "pp_email_header" already
    exists in the account.
  - Template injection is skipped if the per-template header marker is
    already present.

Usage:
    python scripts/pp_inject_email_header.py --dry-run   # show plan
    python scripts/pp_inject_email_header.py             # apply

Required env: KLAVIYO_API_KEY_PAPERANDPURPOSE (Full Access key).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv(REPO_ROOT / ".env")
except ImportError:
    pass

from tools.klaviyo import _klaviyo_request, _api_key_for, KLAVIYO_REVISION  # noqa: E402

BUSINESS_KEY = "paperandpurpose"
IMAGE_PATH = REPO_ROOT / "assets" / "pp_email_header.png"
IMAGE_NAME = "pp_email_header"  # used to de-dupe on re-run

TEMPLATE_IDS = ["VX9hrw", "YhWXde", "XEVUAB", "Tt56X2", "UCtVQS"]

# Marker substring we look for to know if the header has already been
# injected (prevents double-insertion on re-run).
HEADER_MARKER = 'alt="Paper &amp; Purpose"'


# ----------------------------------------------------------------------
# Image upload
# ----------------------------------------------------------------------

def find_existing_image(name: str) -> str | None:
    """Return the CDN image_url of an image with name=name, else None."""
    resp = _klaviyo_request("GET", BUSINESS_KEY, f"images/?filter=equals(name,'{name}')")
    if not isinstance(resp, dict):
        return None
    for item in resp.get("data", []):
        attrs = item.get("attributes", {})
        if attrs.get("name") == name:
            return attrs.get("image_url")
    return None


def upload_image(path: Path, name: str) -> str:
    """Upload a local image via multipart, return its hosted CDN URL.

    Klaviyo's /api/image-upload/ endpoint takes a multipart form with the
    file under field 'file' and optional 'name' / 'hidden' fields.
    """
    key = _api_key_for(BUSINESS_KEY)
    if not key:
        raise SystemExit("ERROR: KLAVIYO_API_KEY_PAPERANDPURPOSE not set.")
    if not path.exists():
        raise SystemExit(f"ERROR: image not found at {path}")

    url = "https://a.klaviyo.com/api/image-upload/"
    headers = {
        "Authorization": f"Klaviyo-API-Key {key}",
        "revision": KLAVIYO_REVISION,
        "accept": "application/vnd.api+json",
    }
    with path.open("rb") as fh:
        files = {
            "file": (path.name, fh, "image/png"),
        }
        data = {
            "name": name,
            "hidden": "false",
        }
        r = requests.post(url, headers=headers, files=files, data=data, timeout=60)

    if r.status_code not in (200, 201):
        raise SystemExit(f"ERROR: image upload failed HTTP {r.status_code}: {r.text[:500]}")

    body = r.json()
    image_url = body.get("data", {}).get("attributes", {}).get("image_url")
    if not image_url:
        raise SystemExit(f"ERROR: upload succeeded but no image_url in response: {body}")
    return image_url


# ----------------------------------------------------------------------
# Template injection
# ----------------------------------------------------------------------

def build_header_block(image_url: str) -> str:
    """Build the header HTML block (the spec from the task)."""
    return (
        '<div style="margin:-40px -16px 32px -16px; border-radius:4px 4px 0 0; overflow:hidden;">\n'
        f'  <img src="{image_url}" '
        'width="600" '
        'alt="Paper &amp; Purpose" '
        'style="display:block; width:100%; max-width:600px; height:auto; border:0;">\n'
        '</div>\n'
    )


def inject_header(html: str, header_block: str) -> tuple[str, bool]:
    """Insert header_block immediately after the inner content table's opening <td>.

    Anchor pattern: the inner content table's <tr><td> opening, which is
    unique in our templates (the structure is <table ... width="100%">
    <tr><td> followed by the body content).

    Returns (new_html, inserted).
    """
    if HEADER_MARKER in html:
        return html, False

    # Anchor: the FIRST literal "<tr><td>" in the document. The outer
    # wrapper's td has align="center" attributes and is split across lines,
    # so it does NOT match this compact pattern. The first match is the
    # inner content table's cell — the one that holds the preview-text div
    # and <p> body content. Inserting right after this opens the cell with
    # the header above the preview div, which is what we want.
    needle = "<tr><td>"
    anchor = html.find(needle)
    if anchor < 0:
        return html, False

    insert_at = anchor + len(needle)
    new_html = html[:insert_at] + "\n" + header_block + html[insert_at:]
    return new_html, True


def get_template(template_id: str) -> dict | str:
    return _klaviyo_request("GET", BUSINESS_KEY, f"templates/{template_id}/")


def patch_template(template_id: str, html: str) -> dict | str:
    body = {
        "data": {
            "type": "template",
            "id": template_id,
            "attributes": {"html": html},
        }
    }
    return _klaviyo_request(
        "PATCH",
        BUSINESS_KEY,
        f"templates/{template_id}/",
        json_body=body,
    )


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------

def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dry-run", action="store_true", help="Show plan; no writes.")
    args = p.parse_args()

    if not _api_key_for(BUSINESS_KEY):
        raise SystemExit("ERROR: KLAVIYO_API_KEY_PAPERANDPURPOSE not set.")

    print(f"P&P email header injection  (mode: {'DRY-RUN' if args.dry_run else 'LIVE'})")
    print()

    # Step 1: ensure image is hosted.
    print(f"[1/3] Checking for existing Klaviyo-hosted image named {IMAGE_NAME!r}...")
    image_url = find_existing_image(IMAGE_NAME)
    if image_url:
        print(f"      reused: {image_url}")
    else:
        print(f"      none found; uploading {IMAGE_PATH.name} ({IMAGE_PATH.stat().st_size}b)")
        if args.dry_run:
            image_url = "https://<CDN_URL_AFTER_UPLOAD>/pp_email_header.png"
            print(f"      (dry-run) would upload and receive a CDN URL")
        else:
            image_url = upload_image(IMAGE_PATH, IMAGE_NAME)
            print(f"      uploaded: {image_url}")
    print()

    # Step 2: inject into each template.
    print(f"[2/3] Injecting header into {len(TEMPLATE_IDS)} templates...")
    header_block = build_header_block(image_url)
    patched: list[tuple[str, bool]] = []
    for tid in TEMPLATE_IDS:
        resp = get_template(tid)
        if not isinstance(resp, dict):
            print(f"  [{tid}] GET failed: {resp}")
            patched.append((tid, False))
            continue
        attrs = resp.get("data", {}).get("attributes", {})
        name = attrs.get("name", "?")
        html = attrs.get("html", "")

        new_html, inserted = inject_header(html, header_block)
        if not inserted:
            print(f"  [{tid}] {name}: header already present, skipping")
            patched.append((tid, True))
            continue

        if args.dry_run:
            print(f"  [{tid}] {name}: would inject (+{len(new_html)-len(html)}b)")
            patched.append((tid, True))
            continue

        patch_resp = patch_template(tid, new_html)
        if isinstance(patch_resp, dict):
            print(f"  [{tid}] {name}: PATCH ok (+{len(new_html)-len(html)}b)")
            patched.append((tid, True))
        else:
            print(f"  [{tid}] {name}: PATCH FAILED: {patch_resp}")
            patched.append((tid, False))
    print()

    if args.dry_run:
        print("Dry-run complete; nothing written.")
        return

    # Step 3: verify.
    print("[3/3] Verifying header presence in each template...")
    all_ok = True
    for tid, patch_ok in patched:
        if not patch_ok:
            print(f"  [{tid}] SKIPPED verify (patch failed)")
            all_ok = False
            continue
        resp = get_template(tid)
        if not isinstance(resp, dict):
            print(f"  [{tid}] verify GET failed")
            all_ok = False
            continue
        html = resp.get("data", {}).get("attributes", {}).get("html", "")
        present = HEADER_MARKER in html and image_url in html
        flag = "OK" if present else "FAIL"
        if not present:
            all_ok = False
        print(f"  [{tid}] verify: {flag}  marker={HEADER_MARKER in html}  url={image_url in html}")

    print()
    print("ALL TEMPLATES NOW HAVE HEADER" if all_ok else "SOME CHECKS FAILED")
    print()
    print(f"Hosted image URL: {image_url}")


if __name__ == "__main__":
    main()
