"""Push a JSON file of social post drafts to Buffer as Ideas.

Pattern intended for CMG (or any persona authoring P&P social content):
author posts in JSON, run this script, Ideas land in the Buffer "My
Organization" Ideas inbox for review.

The created Ideas are channel-independent. When Miriam connects social
channels, the same Ideas can be converted to scheduled Posts via Buffer's
UI (or a later script). For now, this is the preview-and-review path.

Why Ideas (not Posts):
  - Posts require a channelId; no channels are connected yet
  - Ideas live in a per-organization inbox and can be viewed in the Buffer
    dashboard at https://publish.buffer.com/ideas
  - Ideas can be "promoted" to Posts later when channels exist

Input JSON format:
  [
    { "title": "...", "text": "..." },
    { "title": "...", "text": "..." }
  ]

Required env (auto-loaded from paperclip/.env):
  BUFFER_API_KEY

Usage:
  python scripts/pp_push_social_ideas.py path/to/ideas.json
  python scripts/pp_push_social_ideas.py path/to/ideas.json --dry-run

Note: Buffer's API has no deleteIdea mutation. Delete unwanted Ideas in the
Buffer UI at https://publish.buffer.com/ideas.
"""

from __future__ import annotations

import argparse
import json
import os
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

# The single P&P / Calling Digital team Buffer organization.
# Look it up via: buffer_list_organizations() (tools/buffer.py)
ORG_ID = "69ed6e4bb3eb4d0e37ba2f6a"

CREATE_IDEA_MUTATION = """
mutation CreateIdea($input: CreateIdeaInput!) {
  createIdea(input: $input) {
    __typename
    ... on Idea { id organizationId content { title text } }
    ... on InvalidInputError { message }
    ... on UnauthorizedError { message }
    ... on UnexpectedError { message }
    ... on LimitReachedError { message }
  }
}
"""


def push_idea(
    api_key: str,
    org_id: str,
    title: str,
    text: str,
    media: list[dict] | None = None,
    services: list[str] | None = None,
    date: str | None = None,
) -> tuple[bool, str]:
    """Push one Idea to Buffer. Returns (ok, message).

    Optional fields are passed through to Buffer's IdeaContentInput when set:
      media:    list of {url, type, alt?, thumbnailUrl?} per Buffer's IdeaMediaInput.
                `type` is a MediaType enum (lowercase: 'image', 'video', ...).
      services: list of Service enum values to publish the Idea against
                (lowercase: 'instagram', 'facebook', 'twitter', 'linkedin',
                'pinterest', etc).
      date:     ISO 8601 date/datetime string for when this Idea should be
                surfaced. Buffer interprets the format from the field type.
    """
    content: dict[str, object] = {"title": title, "text": text, "aiAssisted": True}
    if media:
        content["media"] = media
    if services:
        content["services"] = services
    if date:
        content["date"] = date

    body = {
        "query": CREATE_IDEA_MUTATION,
        "variables": {
            "input": {
                "organizationId": org_id,
                "content": content,
            }
        },
    }
    r = requests.post(
        "https://api.buffer.com/graphql",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=body,
        timeout=30,
    )
    if r.status_code != 200:
        return False, f"HTTP {r.status_code}: {r.text[:200]}"
    rj = r.json()
    if "errors" in rj:
        return False, rj["errors"][0].get("message", "unknown GraphQL error")
    out = rj.get("data", {}).get("createIdea", {})
    if out.get("__typename") == "Idea":
        return True, f"idea_id={out.get('id')}"
    return False, f"{out.get('__typename')}: {out.get('message', '?')}"


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("input_file", help="Path to JSON file of {title, text} objects")
    p.add_argument("--org-id", default=ORG_ID, help="Buffer organization ID (default = P&P/CD team)")
    p.add_argument("--dry-run", action="store_true", help="Print what would be pushed without calling the API")
    args = p.parse_args()

    api_key = (os.environ.get("BUFFER_API_KEY") or "").strip()
    if not api_key:
        sys.exit("ERROR: BUFFER_API_KEY not in env. Check paperclip/.env.")

    path = Path(args.input_file)
    if not path.exists():
        sys.exit(f"ERROR: input file not found: {path}")
    try:
        items = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        sys.exit(f"ERROR: input is not valid JSON: {e}")
    if not isinstance(items, list):
        sys.exit("ERROR: input must be a JSON list of {title, text} objects.")

    print(f"Loaded {len(items)} idea(s) from {path}")
    print(f"Target org: {args.org_id}")
    print(f"Mode: {'DRY-RUN' if args.dry_run else 'LIVE'}")
    print()

    ok_count = 0
    for i, item in enumerate(items, 1):
        title = (item.get("title") or "").strip()
        text = (item.get("text") or "").strip()
        if not title and not text:
            print(f"  [{i}] SKIP: empty title and text")
            continue
        # Optional pass-through fields per Buffer's IdeaContentInput.
        media = item.get("media") or None
        services = item.get("services") or None
        date = item.get("date") or None

        preview = text.replace("\n", " ")[:60]
        print(f"  [{i}] {title!r}")
        print(f"        {preview}{'...' if len(text) > 60 else ''}")
        extras = []
        if media: extras.append(f"media={len(media)}")
        if services: extras.append(f"services={services}")
        if date: extras.append(f"date={date}")
        if extras:
            print(f"        {' '.join(extras)}")
        if args.dry_run:
            print(f"        (dry-run) would push")
            ok_count += 1
            continue
        ok, msg = push_idea(
            api_key,
            args.org_id,
            title,
            text,
            media=media,
            services=services,
            date=date,
        )
        if ok:
            print(f"        ✓ {msg}")
            ok_count += 1
        else:
            print(f"        ✗ FAIL: {msg}")
        print()

    print(f"Done: {ok_count}/{len(items)} pushed.")
    if not args.dry_run and ok_count > 0:
        print()
        print("Preview in Buffer: https://publish.buffer.com/ideas")


if __name__ == "__main__":
    main()
