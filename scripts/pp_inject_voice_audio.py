"""Paper & Purpose — drop the voice-note audio player into /pages/voice-note.

Replaces the "[Audio embed goes here...]" placeholder paragraph on the
voice-note page with a branded HTML5 <audio> player pointing at a hosted
audio URL.

The audio file must first live at a public CDN URL. Recommended host:
Shopify admin -> Content -> Files -> Upload (gives a cdn.shopify.com URL).
A Google Drive share link will NOT work as an <audio src> (auth-gated,
not a direct media URL).

Usage:
    python scripts/pp_inject_voice_audio.py \\
        --url "https://cdn.shopify.com/s/files/.../voice-note.mp3" \\
        --dry-run

    python scripts/pp_inject_voice_audio.py \\
        --url "https://cdn.shopify.com/s/files/.../voice-note.mp3"

    # if the file is .m4a / .wav, pass the matching mime:
    #   --mime audio/mp4   (for .m4a)
    #   --mime audio/wav   (for .wav)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
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
VOICE_NOTE_ID = "119881498712"
OUTPUTS = REPO_ROOT / "scripts" / "outputs"

# Matches the placeholder paragraph. Accepts both legacy and current formats:
#   legacy:  <p><em>[Audio embed goes here ...]</em></p>
#   current: <p>[AUDIO_PLACEHOLDER: Miriam voice note MP3 — ...]</p>
# Anchored on the opening "[Audio" or "[AUDIO_PLACEHOLDER:" prefix.
PLACEHOLDER_RE = re.compile(
    r"<p>\s*(?:<em>\s*)?\[(?:Audio embed goes here|AUDIO_PLACEHOLDER:).*?\](?:\s*</em>)?\s*</p>",
    re.S,
)


def player_html(url: str, mime: str) -> str:
    """Branded, accessible audio player with a download fallback."""
    return (
        '<div style="margin:24px 0;">\n'
        '  <audio controls preload="none" style="width:100%; max-width:480px;">\n'
        f'    <source src="{url}" type="{mime}">\n'
        '    Your browser does not support the audio element.\n'
        f'    <a href="{url}">Tap here to listen to the voice note</a>.\n'
        '  </audio>\n'
        '</div>'
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", required=True, help="Public CDN URL of the audio file.")
    ap.add_argument("--mime", default="audio/mpeg",
                    help="MIME type (audio/mpeg for .mp3, audio/mp4 for .m4a, audio/wav for .wav).")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    page = _shopify_get(BUSINESS_KEY, f"pages/{VOICE_NOTE_ID}.json")
    if not isinstance(page, dict):
        raise SystemExit(f"ERROR fetching voice-note page: {page}")
    body = page["page"]["body_html"]

    if not PLACEHOLDER_RE.search(body):
        if 'controls' in body and '<audio' in body:
            raise SystemExit("Player already present (no placeholder found). Nothing to do.")
        raise SystemExit("Placeholder paragraph not found — page may have changed. Aborting.")

    new_body = PLACEHOLDER_RE.sub(player_html(args.url, args.mime), body)

    print(f"voice-note page {VOICE_NOTE_ID}")
    print(f"  url:  {args.url}")
    print(f"  mime: {args.mime}")
    print(f"  body: {len(body)}b -> {len(new_body)}b")

    if args.dry_run:
        print("\n  (dry-run) player block that would be inserted:")
        print("  " + player_html(args.url, args.mime).replace("\n", "\n  "))
        return

    # backup
    OUTPUTS.mkdir(parents=True, exist_ok=True)
    bp = OUTPUTS / f"pp_voicenote_backup_{time.strftime('%Y%m%d-%H%M%S')}.json"
    bp.write_text(json.dumps(page["page"], indent=2))
    print(f"  backup -> {bp}")

    shop = _shop_for(BUSINESS_KEY)
    token = _token_for(BUSINESS_KEY)
    url = f"https://{shop}.myshopify.com/admin/api/{_api_version()}/pages/{VOICE_NOTE_ID}.json"
    r = requests.put(
        url,
        headers={"X-Shopify-Access-Token": token, "Content-Type": "application/json"},
        json={"page": {"id": int(VOICE_NOTE_ID), "body_html": new_body}},
        timeout=30,
    )
    print(f"  PUT: HTTP {r.status_code}")

    live = _shopify_get(BUSINESS_KEY, f"pages/{VOICE_NOTE_ID}.json")["page"]["body_html"]
    ok = "<audio" in live and args.url in live and not PLACEHOLDER_RE.search(live)
    print(f"  VERIFY: {'OK — player live, placeholder gone' if ok else 'CHECK FAILED'}")


if __name__ == "__main__":
    main()
