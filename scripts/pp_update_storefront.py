"""Paper & Purpose — storefront bootstrap update (one-time).

Updates the existing journal product's description with file 43's
paste-ready copy, flips inventory_policy so pre-orders work, and creates
the 3 missing pages (/our-story, /the-journal, /voice-note) with file 44's
paste-ready copy.

Title is intentionally left untouched (Miriam's wording stands per
2026-05-26 decision).

Usage:
    python scripts/pp_update_storefront.py --dry-run   # show what would happen
    python scripts/pp_update_storefront.py             # apply for real

Backs up the current product record to scripts/backups/ before any change,
so the original description is recoverable.

Required env vars (loaded from paperclip/.env):
    SHOPIFY_SHOP_PAPERANDPURPOSE
    SHOPIFY_ADMIN_TOKEN_PAPERANDPURPOSE
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv(REPO_ROOT / ".env")
except ImportError:
    pass

import requests  # noqa: E402

from tools.shopify import _shopify_get  # noqa: E402

BUSINESS_KEY = "paperandpurpose"
PRODUCT_ID = 8683238588504
VARIANT_ID = 43820642107480
API_VERSION = "2024-10"


# ----------------------------------------------------------------------
# HTTP helpers (read uses _shopify_get; write helpers below)
# ----------------------------------------------------------------------

def _shop_and_token() -> tuple[str, str]:
    shop = os.environ.get("SHOPIFY_SHOP_PAPERANDPURPOSE")
    token = os.environ.get("SHOPIFY_ADMIN_TOKEN_PAPERANDPURPOSE")
    if not shop or not token:
        raise SystemExit(
            "ERROR: SHOPIFY_SHOP_PAPERANDPURPOSE and "
            "SHOPIFY_ADMIN_TOKEN_PAPERANDPURPOSE must be set in paperclip/.env"
        )
    return shop, token


def _shopify_write(
    method: str,
    path: str,
    json_body: dict[str, Any] | None = None,
) -> tuple[int, Any]:
    shop, token = _shop_and_token()
    url = f"https://{shop}.myshopify.com/admin/api/{API_VERSION}/{path.lstrip('/')}"
    resp = requests.request(
        method,
        url,
        headers={
            "X-Shopify-Access-Token": token,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        json=json_body,
        timeout=30,
    )
    try:
        body = resp.json()
    except Exception:
        body = resp.text
    return resp.status_code, body


# ----------------------------------------------------------------------
# Content
# ----------------------------------------------------------------------

# File 43, ASSET 1 description, converted to clean storefront HTML.
PRODUCT_BODY_HTML = """\
<p>Eighty-eight days. One verse. A guided path through the renewal of your mind.</p>

<p><strong>Be Transformed by the Renewal of Your Mind</strong> is a guided journal built on Romans 12:2, made for the woman who wants her faith to move. Not a Bible study. Not a stack of blank pages. A guided journey, 88 days long, that walks you through reflection, prayer, and transformation one day at a time.</p>

<p>Twelve named sections meet you where you are: Cast Your Cares, Reflect and Correct, Grateful For, Still Small Voice, End of Day PTLs, and more. You are never staring at an empty page wondering what to write. You are walked through it.</p>

<p>Inside, it is alive with color. Watercolor butterflies and florals on every page, in the soft botanical palette of The Apothecary Press. Most prayer journals are black and white. This one refused.</p>

<h3>What's inside</h3>
<ul>
  <li>392 pages, 88 guided days</li>
  <li>Hardcover, 7.2" by 8.46", sized to hold open in your hands</li>
  <li>Gold spiral coil binding, lays flat</li>
  <li>Keepsake gift-box packaging</li>
  <li>Perforated prayer tear-out cards</li>
  <li>A built-in answered-prayer envelope, so the prayers you release today you can revisit when He answers them</li>
  <li>A keepsake divider with a folder for the ticket stubs, notes, and small mercies you collect along the way</li>
</ul>

<p>Made by a working woman for sisters who are tired and still showing up.</p>

<p><strong>PRE-ORDER:</strong> This is a pre-order. Journals ship October 2026. Every pre-order in this window includes the matching botanical pencil-pouch bundle, free.</p>
"""


OUR_STORY_HTML = """\
<p>I have a confession. I was uninspired by blank pages. For years.</p>

<p>I would buy a beautiful journal at the bookstore. Bring it home. Set it on my nightstand. And then nothing. The blank page would just sit there, staring at me, waiting for me to be profound on cue.</p>

<p>And then there was the other thing. Re-reading past journals gave me anxiety. I would open one from two years ago and want to crawl out of my skin. Because there I was. Spiraling. Crying out. Same prayer. Same fear. Same loop. I never got to see how God answered, because the pages were not built to track it. They were just pages.</p>

<p>So I stopped journaling. For a long time.</p>

<p>But the longing did not go away. The wanting-to-sit-with-Jesus did not go away. The needing-a-soft-place-to-land-after-a-9-to-5 did not go away.</p>

<p>I just needed something different. Something with a guide. Something that did not make me feel like I was failing at journaling. Something that let me hand my worries to Him and then show me later where He met me in it.</p>

<p>I could not find it. So I made it.</p>

<p>This journal is what I would have given my younger self. The one who was tired. The one who did not know where to start. The one who needed someone to say, here, start here, I have got you.</p>

<p>It is anchored in one verse. Romans 12:2. Do not be conformed to this world, but be transformed by the renewal of your mind. That is the whole thing. That is the prayer. That is the 88 days.</p>

<p>I am not a theologian. I am not a pastor. I am a woman with a 9-to-5 and a Mexican abuela's faith and a stack of half-finished journals I am trying to redeem. I made this for sisters like me.</p>

<p>If that is you, I would love for you to be part of this.</p>

<p><a href="/products/be-transformed-guided-mind-renewal-journal"><strong>Pre-order the journal</strong></a></p>
"""


THE_JOURNAL_HTML = """\
<p>This is not a regular journal, and I want you to know exactly what you are saying yes to.</p>

<p><strong>Be Transformed by the Renewal of Your Mind.</strong> An 88-day guided journal. 392 pages. Hardcover. Spiral binding with gold coils. 7.2" by 8.46", sized to hold open in your hands without a fight. It arrives in a keepsake gift box, the kind you would hand to your sister and she would cry before she opened it.</p>

<p>Inside, every page is colorful. Watercolor butterflies and florals throughout. This is the only Christian journal I know of with this much color on the inside. Most are black and white. I refused.</p>

<h3>The twelve guided sections</h3>

<p>You move through twelve named sections across the 88 days. You do not have to fill every one every day. Some days you cast your cares and that is the whole entry. Some days you fill every box. Both count.</p>

<ol>
  <li><strong>Cast Your Cares.</strong> A place to leave the things you have been carrying.</li>
  <li><strong>Reflect and Correct.</strong> Where you tell the truth about the day. Honest, no shame.</li>
  <li><strong>Grateful For.</strong> Three things. Especially on the days you do not feel it.</li>
  <li><strong>Still Small Voice.</strong> What He nudged you with. The scripture that would not leave you alone.</li>
  <li><strong>End of Day PTLs.</strong> Praise the Lord moments. The small mercies you almost missed.</li>
  <li><strong>Prayer Requests.</strong> What you are asking for. Specific. Dated.</li>
  <li><strong>For My People.</strong> The names you are carrying and covering today.</li>
  <li><strong>Word of the Day.</strong> A verse to sit with. A phrase to chew on.</li>
  <li><strong>Heart Check.</strong> What you are actually feeling. Not what you are performing.</li>
  <li><strong>Letting Go.</strong> The thing you keep picking back up. Naming it. Releasing it again.</li>
  <li><strong>He Met Me Here.</strong> Where you saw Him today. In a stranger, a song, the silence.</li>
  <li><strong>The Renewal.</strong> End-of-section reflection. What He is renewing in you.</li>
</ol>

<h3>The keepsake elements</h3>

<ul>
  <li><strong>A divider with a built-in folder</strong> for the keepsakes you collect along the way. Ticket stubs, the note on the counter, the sonogram, whatever the Lord uses to mark these 88 days.</li>
  <li><strong>Perforated prayer tear-out cards.</strong> Write the prayer, tear it out, tuck it in your Bible, tape it to your mirror, hand it to a friend.</li>
  <li><strong>An answered-prayer envelope</strong> built into the back. The prayers you tear out today, you tuck back in here when the Lord answers them. Eighty-eight days from now, you open an envelope full of answered prayers. That is the point.</li>
</ul>

<h3>Why 88 days</h3>

<p>I tried 30. Not long enough. The renewal of your mind does not fit in a month. I tried 365. Too long, nobody finishes. 88 came out of prayer. Long enough for a habit to take root, short enough that you can see the finish line from the start. You can do hard things for 88 days.</p>

<p>This is a journal for the woman, not the theologian. Every prompt pulls you toward reflection and action, not commentary. Just: what is the Lord saying to me, what am I carrying, what needs to change, what did He answer.</p>

<p><a href="/products/be-transformed-guided-mind-renewal-journal"><strong>Pre-order the journal</strong></a></p>
"""


VOICE_NOTE_HTML = """\
<p>I recorded something for you. Two minutes, voice-memo style, from my couch.</p>

<p>I wanted to tell you a couple of things I was not sure I wanted to put in writing. So I just said them out loud instead.</p>

<p><em>[Audio embed goes here. Miriam to upload the 2-minute voice note via Shopify admin (Online Store, Files, upload MP3) and replace this placeholder with the audio player.]</em></p>

<p>If audio is not your thing right now, here is the short version. I am working a 9-to-5. This is the dream project, the thing I do at night, the thing I prayed about for two years before I touched a design file. I am not a polished founder. I am getting on camera slowly, voice first, because that is what is honest.</p>

<p>Thank you for being here for the quiet beginning of this.</p>

<p><a href="/products/be-transformed-guided-mind-renewal-journal"><strong>Pre-order the journal</strong></a></p>
"""


PAGES_TO_CREATE = [
    {
        "title": "Our Story",
        "handle": "our-story",
        "body_html": OUR_STORY_HTML,
        "published": True,
    },
    {
        "title": "The Journal",
        "handle": "the-journal",
        "body_html": THE_JOURNAL_HTML,
        "published": True,
    },
    {
        "title": "A Voice Note From Miriam",
        "handle": "voice-note",
        "body_html": VOICE_NOTE_HTML,
        "published": True,
    },
]


# ----------------------------------------------------------------------
# Operations
# ----------------------------------------------------------------------

def backup_current_product() -> Path:
    """Snapshot the current product to scripts/backups/ before any change."""
    backup_dir = REPO_ROOT / "scripts" / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    current = _shopify_get(BUSINESS_KEY, f"products/{PRODUCT_ID}.json")
    if isinstance(current, str) and current.startswith("ERROR"):
        raise SystemExit(f"Backup failed: {current}")
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = backup_dir / f"pp_product_{PRODUCT_ID}_{ts}.json"
    path.write_text(json.dumps(current, indent=2))
    return path


def update_product(dry_run: bool) -> None:
    """Update product description + flip variant to accept pre-orders."""
    body = {
        "product": {
            "id": PRODUCT_ID,
            "body_html": PRODUCT_BODY_HTML,
            "variants": [
                {
                    "id": VARIANT_ID,
                    "inventory_policy": "continue",
                }
            ],
        }
    }
    if dry_run:
        print(f"  (dry-run) would PUT products/{PRODUCT_ID}.json")
        print(f"           body_html: {len(PRODUCT_BODY_HTML)} chars (file 43 description)")
        print(f"           variant {VARIANT_ID} inventory_policy: continue (accepts pre-orders)")
        return
    status, resp = _shopify_write("PUT", f"products/{PRODUCT_ID}.json", json_body=body)
    if status >= 400:
        print(f"  FAIL product update: HTTP {status}: {str(resp)[:300]}")
    else:
        updated = resp.get("product", {}) if isinstance(resp, dict) else {}
        var = (updated.get("variants") or [{}])[0]
        print(f"  product updated: HTTP {status}")
        print(f"    body_html length: {len(updated.get('body_html') or '')} chars")
        print(f"    variant inventory_policy: {var.get('inventory_policy')!r}")


def existing_page_handles() -> set[str]:
    resp = _shopify_get(BUSINESS_KEY, "pages.json", params={"limit": 250})
    if isinstance(resp, str) and resp.startswith("ERROR"):
        raise SystemExit(resp)
    return {(p.get("handle") or "").lower() for p in resp.get("pages", [])}


def create_pages(dry_run: bool) -> None:
    """Create any pages from PAGES_TO_CREATE that don't already exist."""
    existing = existing_page_handles()
    print(f"  existing handles: {sorted(existing)}")
    for page in PAGES_TO_CREATE:
        h = page["handle"]
        if h in existing:
            print(f"  /pages/{h}: already exists, SKIP")
            continue
        if dry_run:
            print(f"  (dry-run) would POST page: /pages/{h}  ({len(page['body_html'])} chars body)")
            continue
        status, resp = _shopify_write("POST", "pages.json", json_body={"page": page})
        if status >= 400:
            print(f"  /pages/{h}: FAIL HTTP {status}: {str(resp)[:300]}")
        else:
            created = resp.get("page", {}) if isinstance(resp, dict) else {}
            print(f"  /pages/{h}: HTTP {status}  id={created.get('id')}  published={bool(created.get('published_at'))}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would happen without making changes.")
    args = parser.parse_args()

    print(f"=== Paper & Purpose storefront bootstrap (dry-run={args.dry_run}) ===")
    print()

    print("Step 1: backup current product record")
    if args.dry_run:
        print("  (dry-run) would write scripts/backups/pp_product_<id>_<ts>.json")
    else:
        path = backup_current_product()
        print(f"  saved: {path}")
    print()

    print("Step 2: update product description + flip inventory_policy")
    update_product(args.dry_run)
    print()

    print("Step 3: create missing pages")
    create_pages(args.dry_run)
    print()

    print("Step 4: verify final state")
    if args.dry_run:
        print("  (dry-run) would re-fetch product + pages")
    else:
        prod = _shopify_get(BUSINESS_KEY, f"products/{PRODUCT_ID}.json")
        if isinstance(prod, dict):
            p = prod.get("product", {})
            v = (p.get("variants") or [{}])[0]
            print(f"  product title:           {p.get('title')!r}")
            print(f"  product body_html:       {len(p.get('body_html') or '')} chars")
            print(f"  variant inventory_policy: {v.get('inventory_policy')!r}")
        pages = _shopify_get(BUSINESS_KEY, "pages.json", params={"limit": 250})
        if isinstance(pages, dict):
            for pg in pages.get("pages", []):
                print(f"  /pages/{pg.get('handle')}  title={pg.get('title')!r}  published={bool(pg.get('published_at'))}")

    print()
    print("Done.")


if __name__ == "__main__":
    main()
