"""Paper & Purpose — create or update /pages/share via Shopify Admin API.

A landing page for supporters with ready-to-share copy and links for the
Be Transformed journal pre-sale. Final URL: paperandpurpose.co/pages/share

Idempotent:
  - GET /pages.json?handle=share — if exists, PUT body_html on that page
  - If not, POST a new page with handle="share"
  - Prints the final URL either way

Auth/token pattern matches pp_refresh_shopify_token.py and the other P&P
scripts in this directory: loads .env via dotenv, uses
tools.shopify._shop_for() / _token_for() / _api_version(), direct
requests.post / requests.put for writes (tools.shopify is read-only).

Required env (in .env):
    SHOPIFY_SHOP_PAPERANDPURPOSE        = paper-purpose-4
    SHOPIFY_ADMIN_TOKEN_PAPERANDPURPOSE = shpat_...  (re-mint via
                                          pp_refresh_shopify_token.py if 24h+)

Required Shopify Admin scope: write_content (covers Pages). Confirmed present.

Usage:
    python scripts/pp_create_share_page.py --dry-run    # show plan, no writes
    python scripts/pp_create_share_page.py              # create or update live
"""

from __future__ import annotations

import argparse
import html as html_lib
import sys
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv(REPO_ROOT / ".env", override=True)
except ImportError:
    pass

from tools.shopify import _shop_for, _token_for, _api_version, _shopify_get  # noqa: E402

BUSINESS_KEY = "paperandpurpose"
PAGE_HANDLE = "share"
PAGE_TITLE = "Help Us Spread the Word"

PRODUCT_URL = "https://paperandpurpose.co/products/be-transformed-guided-mind-renewal-journal"
HOMEPAGE_URL = "https://paperandpurpose.co/"

# ---- share content (no em-dashes anywhere) ----

SHARE_TEXT_1 = (
    "Hey! A family member of mine just launched her dream. Be Transformed, "
    "a 90-day Christian journal for women, Romans 12:2. Two years of work, "
    "all on her own. Pre-sale is live now, ships this fall. Thought of you.\n\n"
    f"{PRODUCT_URL}"
)

SHARE_TEXT_2 = (
    "Sharing something special. Be Transformed is a 90-day Christian guided "
    "journal for women anchored in Romans 12:2. A woman built it on her own "
    "over two years. 392 pages, hardcover, with watercolor artwork, tear-out "
    "prayer cards, and a keepsake envelope for answered prayers. The pre-sale "
    "is live now and it ships this fall. If it's for you or someone you know, "
    "take a look. A share helps more than you'd think.\n\n"
    f"{PRODUCT_URL}\n"
    f"{HOMEPAGE_URL}"
)

SHARE_TEXT_3 = (
    "Sharing this from a friend. It's called Be Transformed, a 90-day "
    "Christian journal for women anchored in Romans 12:2. A woman built it on "
    "her own over two years. Beautiful hardcover with watercolor pages, prayer "
    "cards, and an envelope for answered prayers. Pre-sale is live now, ships "
    "this fall. If it's for you or someone you know.\n\n"
    f"{PRODUCT_URL}"
)

SUBHEAD = (
    "Thank you for helping Be Transformed find the women it was made for. "
    "Tap any message below to copy it, then share it however you like."
)


# Brand palette (cream / sage / deep-green, no em-dashes, serif headings):
#   #F2EDE4  Bone Cream    page background
#   #FFFFFF  White         block background (contrast inside cream)
#   #9CA88E  Dusty Sage    block borders + button hover state
#   #4A5340  Forest Olive  headings + primary button + links
#   #2A2A26  Antique Charcoal  body text


def build_body_html() -> str:
    """Build the full share-page body_html with inline CSS + JS.

    Uses class names prefixed with `pp-share-` to minimize collision with
    the active Shopify theme's CSS. All assets inline since Shopify pages
    don't easily load external assets.
    """
    text_1 = html_lib.escape(SHARE_TEXT_1)
    text_2 = html_lib.escape(SHARE_TEXT_2)
    text_3 = html_lib.escape(SHARE_TEXT_3)

    return f"""<style>
  .pp-share-page {{
    max-width: 720px;
    margin: 24px auto 40px;
    padding: 24px;
    background: #F2EDE4;
    font-family: Georgia, 'Cormorant Garamond', serif;
    color: #2A2A26;
    line-height: 1.6;
  }}
  .pp-share-page .pp-subhead {{
    color: #4A5340;
    font-style: italic;
    margin: 0 0 32px;
    font-size: 18px;
    line-height: 1.5;
  }}
  .pp-share-page .pp-share-block {{
    border: 1px solid #9CA88E;
    border-radius: 4px;
    padding: 24px;
    margin: 0 0 24px;
    background: #FFFFFF;
  }}
  .pp-share-page .pp-share-block h2 {{
    font-family: 'Cormorant Garamond', Georgia, serif;
    font-size: 22px;
    font-weight: 400;
    color: #4A5340;
    margin: 0 0 16px;
  }}
  .pp-share-page .pp-share-text {{
    white-space: pre-wrap;
    font-size: 16px;
    color: #2A2A26;
    line-height: 1.7;
    margin: 0 0 20px;
    font-family: Georgia, 'Cormorant Garamond', serif;
  }}
  .pp-share-page .pp-copy-btn {{
    display: inline-block;
    background: #4A5340;
    color: #F2EDE4;
    border: none;
    padding: 10px 24px;
    font-family: Arial, sans-serif;
    font-size: 14px;
    font-weight: bold;
    letter-spacing: 0.04em;
    border-radius: 4px;
    cursor: pointer;
    transition: background 0.2s;
  }}
  .pp-share-page .pp-copy-btn:hover {{
    background: #2A2A26;
  }}
  .pp-share-page .pp-copy-btn.pp-copied {{
    background: #9CA88E;
  }}
  .pp-share-page .pp-links {{
    margin: 40px 0 0;
    padding: 24px 0 0;
    border-top: 1px solid #9CA88E;
  }}
  .pp-share-page .pp-links h2 {{
    font-family: 'Cormorant Garamond', Georgia, serif;
    font-weight: 400;
    font-size: 22px;
    color: #4A5340;
    margin: 0 0 16px;
  }}
  .pp-share-page .pp-links p {{
    margin: 0 0 12px;
    font-size: 16px;
  }}
  .pp-share-page .pp-links a {{
    color: #4A5340;
    text-decoration: underline;
    word-break: break-all;
  }}
  .pp-share-page .pp-links strong {{
    color: #4A5340;
  }}
</style>

<div class="pp-share-page">
  <p class="pp-subhead">{html_lib.escape(SUBHEAD)}</p>

  <article class="pp-share-block">
    <h2>Text a friend</h2>
    <div class="pp-share-text">{text_1}</div>
    <button class="pp-copy-btn" type="button" onclick="ppCopyShare(this)">Copy</button>
  </article>

  <article class="pp-share-block">
    <h2>Facebook or LinkedIn post</h2>
    <div class="pp-share-text">{text_2}</div>
    <button class="pp-copy-btn" type="button" onclick="ppCopyShare(this)">Copy</button>
  </article>

  <article class="pp-share-block">
    <h2>Forward it onward</h2>
    <div class="pp-share-text">{text_3}</div>
    <button class="pp-copy-btn" type="button" onclick="ppCopyShare(this)">Copy</button>
  </article>

  <section class="pp-links">
    <h2>Links</h2>
    <p><strong>Reserve a copy:</strong> <a href="{PRODUCT_URL}">{PRODUCT_URL}</a></p>
    <p><strong>Explore the brand:</strong> <a href="{HOMEPAGE_URL}">{HOMEPAGE_URL}</a></p>
  </section>
</div>

<script>
(function() {{
  function ppCopyShare(btn) {{
    var block = btn.closest('.pp-share-block');
    if (!block) {{ return; }}
    var textEl = block.querySelector('.pp-share-text');
    if (!textEl) {{ return; }}
    var text = textEl.innerText.trim();
    var done = function() {{
      btn.textContent = 'Copied';
      btn.classList.add('pp-copied');
      setTimeout(function() {{
        btn.textContent = 'Copy';
        btn.classList.remove('pp-copied');
      }}, 2000);
    }};
    var fail = function() {{
      btn.textContent = 'Try again';
      setTimeout(function() {{ btn.textContent = 'Copy'; }}, 2000);
    }};
    if (navigator.clipboard && navigator.clipboard.writeText) {{
      navigator.clipboard.writeText(text).then(done).catch(fail);
      return;
    }}
    // Fallback for older browsers and non-HTTPS contexts
    try {{
      var range = document.createRange();
      range.selectNodeContents(textEl);
      var sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(range);
      document.execCommand('copy');
      sel.removeAllRanges();
      done();
    }} catch (e) {{
      fail();
    }}
  }}
  window.ppCopyShare = ppCopyShare;
}})();
</script>"""


# ---- Shopify API helpers ----

def find_existing_page(handle: str) -> dict | None:
    """Return the page dict if a page with this handle exists, else None."""
    resp = _shopify_get(BUSINESS_KEY, f"pages.json?handle={handle}")
    if isinstance(resp, str):
        raise SystemExit(f"ERROR: page lookup failed: {resp}")
    for p in resp.get("pages", []):
        if p.get("handle") == handle:
            return p
    return None


def create_page(title: str, handle: str, body_html: str) -> dict:
    """POST a new page. Published live."""
    shop = _shop_for(BUSINESS_KEY)
    token = _token_for(BUSINESS_KEY)
    url = f"https://{shop}.myshopify.com/admin/api/{_api_version()}/pages.json"
    payload = {
        "page": {
            "title": title,
            "handle": handle,
            "body_html": body_html,
            "published": True,
        }
    }
    r = requests.post(
        url,
        headers={"X-Shopify-Access-Token": token, "Content-Type": "application/json"},
        json=payload,
        timeout=30,
    )
    if r.status_code not in (200, 201):
        raise SystemExit(f"ERROR: create failed HTTP {r.status_code}: {r.text[:400]}")
    return r.json()["page"]


def update_page(page_id: int, title: str, body_html: str) -> dict:
    """PUT new body_html (and title in case it drifted) on existing page."""
    shop = _shop_for(BUSINESS_KEY)
    token = _token_for(BUSINESS_KEY)
    url = f"https://{shop}.myshopify.com/admin/api/{_api_version()}/pages/{page_id}.json"
    payload = {
        "page": {
            "id": page_id,
            "title": title,
            "body_html": body_html,
            "published": True,
        }
    }
    r = requests.put(
        url,
        headers={"X-Shopify-Access-Token": token, "Content-Type": "application/json"},
        json=payload,
        timeout=30,
    )
    if r.status_code != 200:
        raise SystemExit(f"ERROR: update failed HTTP {r.status_code}: {r.text[:400]}")
    return r.json()["page"]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true",
                    help="Show what would be done; do not write to Shopify.")
    args = ap.parse_args()

    # Auth check
    shop = _shop_for(BUSINESS_KEY)
    token = _token_for(BUSINESS_KEY)
    if not shop:
        raise SystemExit("ERROR: SHOPIFY_SHOP_PAPERANDPURPOSE env var not set.")
    if not token:
        raise SystemExit("ERROR: SHOPIFY_ADMIN_TOKEN_PAPERANDPURPOSE env var not set "
                         "(or expired — re-mint via pp_refresh_shopify_token.py).")
    print(f"Shop:       {shop}.myshopify.com  (custom domain: paperandpurpose.co)")
    print(f"API ver:    {_api_version()}")
    print(f"Mode:       {'DRY-RUN' if args.dry_run else 'LIVE'}")
    print()

    body_html = build_body_html()
    print(f"Built body_html: {len(body_html):,}b")
    print()

    existing = find_existing_page(PAGE_HANDLE)
    if existing:
        print(f"Found existing page id={existing.get('id')}  handle={existing.get('handle')!r}")
        print(f"  current title: {existing.get('title')!r}")
        print(f"  current body:  {len(existing.get('body_html') or '')}b")
        print(f"  current url:   https://paperandpurpose.co/pages/{existing.get('handle')}")
        if args.dry_run:
            print(f"\n  (dry-run) would UPDATE this page in place")
            return
        result = update_page(existing["id"], PAGE_TITLE, body_html)
        print(f"\n[UPDATED]")
    else:
        print(f"No existing page with handle={PAGE_HANDLE!r}")
        if args.dry_run:
            print(f"\n  (dry-run) would CREATE new page titled {PAGE_TITLE!r}")
            return
        result = create_page(PAGE_TITLE, PAGE_HANDLE, body_html)
        print(f"\n[CREATED]")

    print(f"  id:     {result.get('id')}")
    print(f"  title:  {result.get('title')}")
    print(f"  handle: {result.get('handle')}")
    print(f"  body:   {len(result.get('body_html') or '')}b")
    print(f"  URL:    https://paperandpurpose.co/pages/{result.get('handle')}")


if __name__ == "__main__":
    main()
