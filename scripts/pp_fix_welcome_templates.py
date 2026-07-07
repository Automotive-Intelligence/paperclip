"""Paper & Purpose — surgical fixes to the 5 welcome templates.

Three fixes per template:

1. Unsubscribe tag: replace literal {{% unsubscribe %}} with the correct
   {% unsubscribe %}. The double-brace form was a typo in the loader script;
   Klaviyo's macro is the single-brace control-tag form.

2. Hidden preview text: insert a display:none div with the per-template
   preview string immediately before the first <p> tag in <body>. Gmail and
   Outlook surface this as the inbox preview line. Without it, recipients
   see the first sentence of the email body.

3. Mobile rendering: the inner content table has width="600" and
   padding:40px; (fixed pixel width + thick padding) which renders cramped
   on phones. Switch to width="100%" and padding:40px 16px so it adapts.

Idempotent: re-running this script is safe. The string replacements only
fire if the original (broken) markers are still present. The preview-div
insertion is guarded against re-insertion.

Usage:
    python scripts/pp_fix_welcome_templates.py --dry-run    # show what changes
    python scripts/pp_fix_welcome_templates.py              # apply via PATCH
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv(REPO_ROOT / ".env")
except ImportError:
    pass

from tools.klaviyo import _klaviyo_request, _api_key_for  # noqa: E402

BUSINESS_KEY = "paperandpurpose"

# template_id -> preview_text (lifted verbatim from the metadata sidecar
# written by pp_load_welcome_templates.py: preview_a strings).
TEMPLATES: list[tuple[str, str]] = [
    ("VX9hrw", "So I made the journal I wished I had."),
    ("YhWXde", "12 sections. Gold coils. Watercolor butterflies."),
    ("XEVUAB", "Romans 12:2 takes longer than a month."),
    ("Tt56X2", "A two-minute audio note from Miriam."),
    ("UCtVQS", "Reserve your journal. This is the batch that funds the print run."),
]

PREVIEW_DIV_STYLE = "display:none;max-height:0;overflow:hidden;mso-hide:all;"


# ----------------------------------------------------------------------
# Transform
# ----------------------------------------------------------------------

def preview_div(preview_text: str) -> str:
    """Build the hidden preview-text div for a given preview string."""
    return f'<div style="{PREVIEW_DIV_STYLE}">{preview_text}</div>'


def apply_fixes(html: str, preview_text: str) -> tuple[str, dict[str, bool]]:
    """Apply the three fixes to html. Returns (new_html, changes_applied)."""
    changes = {
        "unsubscribe": False,
        "preview_div": False,
        "table_width": False,
        "table_padding": False,
        "footer_width": False,
    }
    out = html

    # Fix 1: unsubscribe tag.
    if "{{% unsubscribe %}}" in out:
        out = out.replace("{{% unsubscribe %}}", "{% unsubscribe %}")
        changes["unsubscribe"] = True

    # Fix 2: hidden preview div. Skip if already present (idempotency check
    # against the exact preview_text string + the unique display:none style).
    div = preview_div(preview_text)
    if div not in out:
        # Insert immediately before the first <p in the body. We anchor on
        # the first occurrence after <body to avoid any <p in <head> styles
        # (unlikely but cheap to be safe).
        body_idx = out.lower().find("<body")
        if body_idx >= 0:
            p_idx = out.lower().find("<p", body_idx)
            if p_idx >= 0:
                out = out[:p_idx] + div + "\n" + out[p_idx:]
                changes["preview_div"] = True

    # Fix 3a: inner content table width attribute. The content table is the
    # only one with padding:40px in its style, so we use width="600"> as the
    # marker and replace just the first occurrence (which is the content
    # table; the footer table comes later in the document).
    if 'width="600">' in out:
        out = out.replace('width="600">', 'width="100%">', 1)
        changes["table_width"] = True

    # Fix 3b: inner content table padding.
    if "padding:40px;" in out:
        out = out.replace("padding:40px;", "padding:40px 16px;", 1)
        changes["table_padding"] = True

    # Fix 3c: footer table width. After fix 3a flips the content table, the
    # remaining width="600"> (if any) is the unsubscribe footer table. Flip
    # it too so the email renders end-to-end fluid on mobile.
    if 'width="600">' in out:
        out = out.replace('width="600">', 'width="100%">', 1)
        changes["footer_width"] = True

    return out, changes


# ----------------------------------------------------------------------
# API
# ----------------------------------------------------------------------

def get_template(template_id: str) -> dict | str:
    return _klaviyo_request("GET", BUSINESS_KEY, f"templates/{template_id}/")


def patch_template(template_id: str, html: str) -> dict | str:
    """PATCH /api/templates/{id}/ with new html body."""
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
# Verify
# ----------------------------------------------------------------------

def verify(template_id: str, preview_text: str) -> dict[str, bool]:
    resp = get_template(template_id)
    if not isinstance(resp, dict):
        return {"fetch": False}
    html = resp.get("data", {}).get("attributes", {}).get("html", "")
    # Count fluid vs fixed widths. Both content and footer tables should now
    # be width="100%". Any remaining width="600" is a regression.
    fluid_count = html.count('width="100%">')
    fixed_count = html.count('width="600">')
    return {
        "fetch": True,
        "unsubscribe_single_brace": (
            "{% unsubscribe %}" in html and "{{% unsubscribe %}}" not in html
        ),
        "preview_div_present": preview_div(preview_text) in html,
        "all_tables_fluid": fixed_count == 0 and fluid_count >= 3,
        "content_padding_responsive": "padding:40px 16px;" in html
            and "padding:40px;" not in html,
    }


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="Show changes that would be made; do not PATCH.")
    args = parser.parse_args()

    if not _api_key_for(BUSINESS_KEY):
        raise SystemExit("ERROR: KLAVIYO_API_KEY_PAPERANDPURPOSE not set.")

    print(f"Klaviyo welcome-template fix  (mode: {'DRY-RUN' if args.dry_run else 'LIVE PATCH'})")
    print()

    # Pass 1: apply + patch
    patched = []
    for tpl_id, preview_text in TEMPLATES:
        resp = get_template(tpl_id)
        if not isinstance(resp, dict):
            print(f"[{tpl_id}] GET failed: {resp}")
            continue
        attrs = resp.get("data", {}).get("attributes", {})
        name = attrs.get("name", "?")
        html = attrs.get("html", "")
        print(f"[{tpl_id}] {name}  ({len(html)}b)")

        new_html, changes = apply_fixes(html, preview_text)
        for k, v in changes.items():
            print(f"    {'+' if v else ' '} {k}: {'CHANGED' if v else 'no-op (already correct or pattern not found)'}")

        if not any(changes.values()):
            print(f"    -> no changes needed; skipping PATCH")
            patched.append((tpl_id, preview_text, True))
            print()
            continue

        if args.dry_run:
            print(f"    (dry-run) would PATCH {len(new_html)-len(html):+d}b net change")
            patched.append((tpl_id, preview_text, True))
            print()
            continue

        patch_resp = patch_template(tpl_id, new_html)
        if isinstance(patch_resp, dict):
            print(f"    -> PATCH ok")
            patched.append((tpl_id, preview_text, True))
        else:
            print(f"    -> PATCH FAILED: {patch_resp}")
            patched.append((tpl_id, preview_text, False))
        print()

    if args.dry_run:
        print("Dry-run complete; nothing written.")
        return

    # Pass 2: verify
    print("--- VERIFY ---")
    all_pass = True
    for tpl_id, preview_text, patch_ok in patched:
        if not patch_ok:
            print(f"[{tpl_id}] SKIPPED verify (patch failed)")
            all_pass = False
            continue
        v = verify(tpl_id, preview_text)
        ok = all(v.values())
        all_pass = all_pass and ok
        flag = "OK" if ok else "FAIL"
        print(f"[{tpl_id}] verify: {flag}")
        for k, val in v.items():
            print(f"    {'PASS' if val else 'FAIL'}  {k}")

    print()
    print("ALL TEMPLATES VERIFIED" if all_pass else "SOME CHECKS FAILED")


if __name__ == "__main__":
    main()
