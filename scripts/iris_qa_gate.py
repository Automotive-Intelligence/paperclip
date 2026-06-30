"""scripts/iris_qa_gate.py — Iris's visual QA gate, one command.

Wraps `tools/screenshot.py` + an Anthropic API call instantiated as Iris
(the Creative Director persona) so any live URL can be visually gated
without a manual claude.ai handoff.

Usage:
    python scripts/iris_qa_gate.py <URL> --brand <key> [--question "..."]
                                          [--model claude-opus-4-8]
                                          [--width 1280]
                                          [--json]

Workflow:
    1. Capture URL via tools/screenshot.py (keyless thumio/microlink, or
       screenshotone if SCREENSHOT_API_KEY is set).
    2. Load Iris's instantiation prompt + Foundation Bible + brand visual
       brief (where one exists) into the system prompt.
    3. Send (instruction + screenshot bytes) to Anthropic API; Iris
       critiques top-to-bottom per her charter.
    4. Print Iris's verdict to stdout (markdown), or JSON wrapper with
       --json for programmatic callers.

Auth:
    - OPENROUTER_API_KEY (Doppler paperclip/prd; matches the pattern used by
      services/flag_responder.py + services/persona_executor.py). Iris calls
      Claude via OpenRouter's vision-enabled chat completions API.
    - SCREENSHOT_PROVIDER + optional SCREENSHOT_API_KEY — for non-default.

Limitations:
    - Public URLs only. Vercel preview SSO will redirect the screenshot
      provider away — use a public preview URL or wait until production.
      A future --vercel-bypass-token flag could fix this.

Per `marketing_deliverables/77b_ai_receptionist_universal_sop.md` § Gotcha 9
and the dispatch flag posted in infrastructure_state.md 2026-06-29.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from pathlib import Path
from typing import Optional

# Allow running from any cwd: anchor on the script's own location.
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from tools.screenshot import capture_url  # noqa: E402


_AVO_TELEMETRY = Path(os.environ.get("AVO_TELEMETRY_PATH", str(Path.home() / "avo-telemetry")))
_DELIVERABLES = _AVO_TELEMETRY / "marketing_deliverables"

# Per-brand visual briefs that supplement Iris's charter + the Foundation Bible.
# Missing briefs fall back to Iris's charter alone (still critiques against the
# Foundation Bible + brand kit if it can read it from the screenshot itself).
_BRAND_BIBLES = {
    "aipg":  ["95_iris_aesthetic_direction_theaiphoneguy.md",
              "96_iris_theaiphoneguy_v0_redesign_prompt.md"],
    "avi":   ["99_iris_avi_site_visual_overhaul_v0.md",
              "avi_iris_creative_direction_kickoff_2026-06-29.md"],
    "bae":   ["95_iris_bae_visual_brief.md"],
    "wd":    [],   # no dedicated brief; Iris uses charter + Foundation Bible
    "bookd": [],
    "pp":    [],
}

_DEFAULT_MODEL = os.environ.get("IRIS_QA_MODEL", "anthropic/claude-opus-4-8")
_DEFAULT_QUESTION = (
    "Does this clear the better-than-Fifth-Avenue bar applied to this brand's "
    "own truth? If so, say approve. If not, name the specific fixes you want "
    "shipped in priority order (most load-bearing first). View top-to-bottom; "
    "every section, every component, including spacing, color use, typography, "
    "and information hierarchy. Be specific enough that a B&T engineer can "
    "execute without guessing."
)


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def _load_iris_context(brand_key: str) -> str:
    """Stitch Iris's instantiation prompt + Foundation Bible + brand bibles."""
    iris_prompt = _read(_DELIVERABLES / "99_iris_instantiation_prompt.md")
    if not iris_prompt:
        raise FileNotFoundError(
            f"Iris instantiation prompt not found at "
            f"{_DELIVERABLES / '99_iris_instantiation_prompt.md'}. "
            "Set AVO_TELEMETRY_PATH or check the deliverable exists."
        )
    foundation = _read(_DELIVERABLES / "00_FOUNDATION_BIBLE.md")
    brand_files = _BRAND_BIBLES.get(brand_key.lower(), [])
    brand_briefs = "\n\n---\n\n".join(
        f"# Brand brief: {fname}\n\n{_read(_DELIVERABLES / fname)}"
        for fname in brand_files if _read(_DELIVERABLES / fname)
    )

    parts = [iris_prompt]
    if foundation:
        parts.append("# Foundation Bible\n\n" + foundation)
    if brand_briefs:
        parts.append(brand_briefs)
    return "\n\n---\n\n".join(parts)


def _capture(url: str, width: int, provider: Optional[str]) -> bytes:
    result = capture_url(url, full_page=True, width=width, provider=provider)
    if not result.get("ok"):
        raise RuntimeError(f"screenshot failed: {result.get('error', 'unknown')}")
    png_path = Path(result["path"])
    return png_path.read_bytes()


def _call_iris(*, system: str, brand: str, url: str, question: str,
               png_b64: str, model: str, max_tokens: int) -> str:
    """Call Iris via OpenRouter's vision-enabled chat completions endpoint.

    Mirrors the pattern in services/flag_responder.py so paperclip's existing
    OpenRouter wiring (Doppler-stored OPENROUTER_API_KEY) just works.
    """
    import requests
    api_key = (os.getenv("OPENROUTER_API_KEY") or os.getenv("LLM_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError(
            "OPENROUTER_API_KEY not set. Run via "
            "`doppler run --project paperclip --config prd -- python3 scripts/iris_qa_gate.py ...` "
            "or set OPENROUTER_API_KEY directly. (Matches the pattern in "
            "services/flag_responder.py.)"
        )

    user_text = (
        f"Visual QA gate request.\n\n"
        f"Brand: {brand}\n"
        f"Live URL: {url}\n\n"
        f"Gate question:\n{question}\n\n"
        f"Full-page screenshot below — view it top-to-bottom and critique "
        f"per your charter. Approve if it clears the bar; otherwise give "
        f"prioritized, B&T-actionable fixes."
    )

    response = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/salesdroid/paperclip",
            "X-Title": "Iris QA Gate",
        },
        json={
            "model": model,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": [
                    {"type": "text", "text": user_text},
                    {"type": "image_url", "image_url": {
                        "url": f"data:image/png;base64,{png_b64}",
                    }},
                ]},
            ],
        },
        timeout=180,
    )
    if not response.ok:
        raise RuntimeError(
            f"OpenRouter HTTP {response.status_code}: {response.text[:500]}"
        )
    body = response.json()
    if not body.get("choices"):
        raise RuntimeError(f"OpenRouter returned no choices: {body}")
    return body["choices"][0]["message"]["content"]


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Iris visual QA gate — screenshot a URL and get her verdict.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("url", help="Live URL to gate (https:// required or assumed).")
    parser.add_argument("--brand", required=True,
                        help=f"Brand key. Known: {sorted(_BRAND_BIBLES)}")
    parser.add_argument("--question", default=None,
                        help="Custom gate question (overrides default).")
    parser.add_argument("--model", default=_DEFAULT_MODEL,
                        help=f"Anthropic model id (default {_DEFAULT_MODEL}).")
    parser.add_argument("--width", type=int, default=1280,
                        help="Screenshot viewport width (default 1280).")
    parser.add_argument("--provider", default=None,
                        help="Screenshot provider: thumio|microlink|screenshotone. "
                             "Default = SCREENSHOT_PROVIDER env or thumio.")
    parser.add_argument("--max-tokens", type=int, default=4096,
                        help="Max tokens for Iris's response (default 4096).")
    parser.add_argument("--json", action="store_true",
                        help="Wrap output as JSON {url, brand, model, verdict}.")
    args = parser.parse_args(argv)

    brand_key = args.brand.lower()
    if brand_key not in _BRAND_BIBLES:
        # Don't hard-error; Iris can still gate via charter + Foundation Bible
        # against whatever the screenshot itself shows. Just warn to stderr.
        print(f"[iris-qa] warning: unknown brand '{brand_key}'. "
              f"Proceeding with charter + Foundation Bible only.", file=sys.stderr)
        _BRAND_BIBLES[brand_key] = []

    system_prompt = _load_iris_context(brand_key)
    print(f"[iris-qa] capturing {args.url} (width={args.width}, full_page=true) ...",
          file=sys.stderr)
    png_bytes = _capture(args.url, args.width, args.provider)
    print(f"[iris-qa] captured {len(png_bytes)} bytes; sending to Iris ({args.model}) ...",
          file=sys.stderr)
    png_b64 = base64.b64encode(png_bytes).decode("ascii")

    verdict = _call_iris(
        system=system_prompt,
        brand=brand_key,
        url=args.url,
        question=args.question or _DEFAULT_QUESTION,
        png_b64=png_b64,
        model=args.model,
        max_tokens=args.max_tokens,
    )

    if args.json:
        print(json.dumps({
            "url": args.url,
            "brand": brand_key,
            "model": args.model,
            "verdict": verdict,
        }, indent=2))
    else:
        print(verdict)
    return 0


if __name__ == "__main__":
    sys.exit(main())
