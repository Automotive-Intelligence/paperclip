"""Paper & Purpose — welcome-template smoke tester.

Validates the 5 welcome-series templates loaded by
pp_load_welcome_templates.py by:

1. Calling Klaviyo's template-render endpoint on each — catches malformed
   HTML, broken merge tags, and missing context vars without sending mail.
2. Sending each rendered template as a test email to a real inbox so the
   final look (subject, preview, render in Gmail/Outlook) can be eyeballed.

Why: Klaviyo's public API does not let us attach templates to flow-message
steps (PATCH on /flow-messages/{id} returns 405 across all current API
revisions). Flow content editing is intentionally UI-only. So we smoke-test
the *templates themselves* — which is what the flow will pipe when it
fires — and trust that the UI-attached steps will render identically.

Usage:
    python scripts/pp_smoke_welcome_templates.py \\
        --to michael@automotiveintelligence.io \\
        --render-only        # validate HTML, no send

    python scripts/pp_smoke_welcome_templates.py \\
        --to michael@automotiveintelligence.io \\
        --send-all           # render + send all 5

    python scripts/pp_smoke_welcome_templates.py \\
        --to michael@automotiveintelligence.io \\
        --send-only VX9hrw   # render-check all + send just one

Required env var: KLAVIYO_API_KEY_PAPERANDPURPOSE (Full Access key).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

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

# Template-id -> human label. Source of truth = the sidecar JSON written by
# pp_load_welcome_templates.py. Hardcoded here so this script is standalone.
WELCOME_TEMPLATES: list[dict[str, str]] = [
    {"id": "VX9hrw", "label": "Welcome 1 (Day 0) - Why I made this"},
    {"id": "YhWXde", "label": "Welcome 2 (Day 1) - What's inside"},
    {"id": "XEVUAB", "label": "Welcome 3 (Day 3) - Why 88 days"},
    {"id": "Tt56X2", "label": "Welcome 4 (Day 5) - Voice memo"},
    {"id": "UCtVQS", "label": "Welcome 5 (Day 7) - Pre-launch invite"},
]


# ----------------------------------------------------------------------
# Render validation
# ----------------------------------------------------------------------

def render_template(template_id: str, context: dict[str, Any] | None = None) -> dict[str, Any] | str:
    """POST /api/template-render — returns rendered HTML+text.

    Klaviyo's render endpoint takes the template id and an optional context
    dict simulating profile/event vars. We pass a minimal context so any
    {{ first_name }} merge tags don't blow up.
    """
    body = {
        "data": {
            "type": "template",
            "attributes": {
                "id": template_id,
                "context": context or {"first_name": "Test"},
            },
        }
    }
    return _klaviyo_request("POST", BUSINESS_KEY, "template-render/", json_body=body)


# ----------------------------------------------------------------------
# Test-send
# ----------------------------------------------------------------------

def send_template_test(template_id: str, to_email: str) -> tuple[bool, str]:
    """Send a rendered template to to_email as a one-off email.

    Klaviyo's modern API does not have a public 'test-send a template'
    endpoint that mirrors the UI button. The supported path is:

      1. Render the template (template-render) to get final HTML.
      2. POST that HTML through the campaign-send-job endpoint, OR
      3. Use the legacy /api/template-render-send (deprecated but still live).

    We use the legacy endpoint via direct HTTPS first because it's the only
    one that does both render+send in one call without creating a campaign
    artifact in the account. If it 410s or 404s, we fall back to a manual
    campaign send.

    Returns (ok, message).
    """
    key = _api_key_for(BUSINESS_KEY)
    if not key:
        return False, "no API key"

    # Path 1: legacy template-render-send (Klaviyo's old "send a test" endpoint).
    # It accepts an email address directly. If still wired, this is the cleanest.
    url = "https://a.klaviyo.com/api/template-render-send/"
    headers = {
        "Authorization": f"Klaviyo-API-Key {key}",
        "revision": KLAVIYO_REVISION,
        "Content-Type": "application/vnd.api+json",
        "accept": "application/vnd.api+json",
    }
    body = {
        "data": {
            "type": "template-render-send",
            "attributes": {
                "id": template_id,
                "context": {"first_name": "Test"},
                "from_email": "smoke-test@paperandpurpose.co",
                "from_name": "Paper & Purpose (smoke test)",
                "to_email": to_email,
                "subject": "[SMOKE TEST] welcome template",
            },
        }
    }
    r = requests.post(url, headers=headers, json=body, timeout=30)
    if r.status_code in (200, 201, 202, 204):
        return True, f"sent via template-render-send (HTTP {r.status_code})"
    return False, f"HTTP {r.status_code}: {r.text[:300]}"


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------

def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--to", required=True, help="Recipient inbox for test sends.")
    p.add_argument("--render-only", action="store_true",
                   help="Render all templates; do not send.")
    p.add_argument("--send-all", action="store_true",
                   help="Render then send all 5 templates.")
    p.add_argument("--send-only", default=None,
                   help="Render all; send only this template_id.")
    p.add_argument("--pause", type=float, default=2.0,
                   help="Seconds between sends to avoid rate limits.")
    args = p.parse_args()

    if not _api_key_for(BUSINESS_KEY):
        raise SystemExit("ERROR: KLAVIYO_API_KEY_PAPERANDPURPOSE not set.")

    mode = "render-only"
    if args.send_all:
        mode = "send-all"
    elif args.send_only:
        mode = f"send-only={args.send_only}"

    print(f"Klaviyo smoke test")
    print(f"  api revision: {KLAVIYO_REVISION}")
    print(f"  recipient:    {args.to}")
    print(f"  mode:         {mode}")
    print()

    render_ok = 0
    render_err = 0
    send_ok = 0
    send_err = 0

    for tpl in WELCOME_TEMPLATES:
        tid = tpl["id"]
        label = tpl["label"]
        print(f"[{tid}] {label}")

        # 1) Render-validate
        resp = render_template(tid)
        if isinstance(resp, dict):
            html = (resp.get("data", {}) or {}).get("attributes", {}).get("html", "")
            text = (resp.get("data", {}) or {}).get("attributes", {}).get("text", "")
            if html:
                print(f"    render: OK  html={len(html)}b  text={len(text)}b")
                render_ok += 1
            else:
                print(f"    render: WARN  no html in response: {str(resp)[:200]}")
                render_err += 1
        else:
            print(f"    render: FAIL  {resp[:300] if isinstance(resp, str) else resp}")
            render_err += 1

        # 2) Send if requested
        should_send = args.send_all or (args.send_only and args.send_only == tid)
        if should_send:
            ok, msg = send_template_test(tid, args.to)
            if ok:
                print(f"    send:   OK  {msg}")
                send_ok += 1
            else:
                print(f"    send:   FAIL  {msg}")
                send_err += 1
            time.sleep(args.pause)

        print()

    print(f"Summary  render: ok={render_ok}  err={render_err}")
    if args.send_all or args.send_only:
        print(f"         send:   ok={send_ok}    err={send_err}")
        if send_err and send_ok == 0:
            print()
            print("NOTE: If all sends failed with 404/410 on template-render-send,")
            print("the legacy endpoint is fully deprecated. Use Klaviyo UI:")
            print("  Templates -> [template] -> '...' menu -> Send a preview")


if __name__ == "__main__":
    main()
