"""Push scheduled Posts (not just Ideas) to Buffer for Paper & Purpose.

Upgrade of pp_push_social_ideas.py — pushes real channel-bound Posts
with image assets, first comments, custom scheduling, and tag-style
source identifiers.

Input JSON format (list of post specs):
[
  {
    "channel": "facebook" | "instagram" | "tiktok",
    "text": "...",                    # post body
    "image_url": "https://...",       # required for IG; recommended FB
    "link_url": "https://...",        # optional (FB link attachment)
    "first_comment": "...",           # optional (IG/FB only)
    "due_at": "2026-06-11T08:00:00-05:00",  # ISO datetime
    "save_to_draft": false,           # if true, lands in Drafts not Queue
    "source": "pp_blog_fanout_v1"     # tag-style tracker in post source
  }
]

For TikTok: text is used as the caption; Buffer creates a notification
post (no auto-publish without video). save_to_draft=true is recommended
until a video asset pipeline lands.

Required env (auto-loaded from paperclip/.env):
  BUFFER_API_KEY

Usage:
  python scripts/pp_buffer_post.py path/to/posts.json
  python scripts/pp_buffer_post.py path/to/posts.json --dry-run
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

ORG_ID = "69ed6e4bb3eb4d0e37ba2f6a"

# Channel ID lookup — keyed by service string in input JSON
CHANNEL_IDS = {
    "tiktok":    "6a205be0c687a22dd4584d1f",
    "facebook":  "6a205c0cc687a22dd4584db5",
    "instagram": "6a205c29c687a22dd4584e07",
}

CREATE_POST_MUTATION = """
mutation CreatePost($input: CreatePostInput!) {
  createPost(input: $input) {
    __typename
    ... on PostActionSuccess { post { id status dueAt } }
    ... on InvalidInputError { message }
    ... on UnauthorizedError { message }
    ... on UnexpectedError { message }
    ... on LimitReachedError { message }
    ... on NotFoundError { message }
    ... on RestProxyError { message link code }
  }
}
"""


def build_input(post: dict) -> dict:
    channel = post["channel"].lower()
    channel_id = CHANNEL_IDS[channel]
    text = post.get("text", "").strip()
    image_url = post.get("image_url")
    link_url = post.get("link_url")
    first_comment = post.get("first_comment")
    due_at = post.get("due_at")
    save_to_draft = bool(post.get("save_to_draft", False))
    source = post.get("source", "pp_buffer_post.py")

    assets: list[dict] = []
    image_urls = post.get("image_urls") or ([image_url] if image_url else [])
    for u in image_urls:
        assets.append({"image": {"url": u}})

    metadata: dict = {}
    if channel == "instagram":
        # Buffer's IG only accepts post/story/reel. Carousel is auto-detected
        # by the number of image assets, so always pass type=post for feed.
        metadata["instagram"] = {
            "type": post.get("ig_type", "post"),
            "shouldShareToFeed": True,
        }
        if first_comment:
            metadata["instagram"]["firstComment"] = first_comment
    elif channel == "facebook":
        metadata["facebook"] = {"type": "post"}
        if first_comment:
            metadata["facebook"]["firstComment"] = first_comment
        if link_url:
            metadata["facebook"]["linkAttachment"] = {"url": link_url}
    elif channel == "tiktok":
        metadata["tiktok"] = {"isAiGenerated": True}
        if post.get("tiktok_title"):
            metadata["tiktok"]["title"] = post["tiktok_title"]

    # Scheduling: customScheduled needs dueAt; default to addToQueue if no due_at
    if save_to_draft:
        mode = "addToQueue"
    elif due_at:
        mode = "customScheduled"
    else:
        mode = "addToQueue"

    # Instagram and TikTok require notification mode (no auto-publish).
    # Facebook supports automatic.
    scheduling_type = "automatic" if channel == "facebook" else "notification"

    inp: dict = {
        "channelId": channel_id,
        "schedulingType": scheduling_type,
        "text": text,
        "metadata": metadata,
        "assets": assets,
        "mode": mode,
        "source": source,
        "aiAssisted": True,
        "saveToDraft": save_to_draft,
    }
    if due_at and not save_to_draft:
        inp["dueAt"] = due_at
    return inp


def push_post(api_key: str, post_input: dict) -> tuple[bool, str]:
    body = {"query": CREATE_POST_MUTATION, "variables": {"input": post_input}}
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
        return False, f"HTTP {r.status_code}: {r.text[:300]}"
    rj = r.json()
    if "errors" in rj:
        return False, rj["errors"][0].get("message", "unknown GraphQL error")
    out = rj.get("data", {}).get("createPost", {}) or {}
    tn = out.get("__typename")
    if tn == "PostActionSuccess":
        p = out.get("post", {}) or {}
        return True, f"post_id={p.get('id')} status={p.get('status')} due={p.get('dueAt') or '(queue)'}"
    return False, f"{tn}: {out.get('message', '?')}"


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("input_file", help="Path to JSON file of post specs")
    p.add_argument("--dry-run", action="store_true", help="Print payloads without calling Buffer")
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
    if isinstance(items, dict) and "posts" in items:
        items = items["posts"]
    if not isinstance(items, list):
        sys.exit("ERROR: input must be a JSON list (or {posts:[...]}) of post objects.")

    print(f"Loaded {len(items)} post(s) from {path}")
    print(f"Mode: {'DRY-RUN' if args.dry_run else 'LIVE'}")
    print()

    ok_count = 0
    for i, item in enumerate(items, 1):
        ch = item.get("channel", "?")
        text_preview = item.get("text", "").replace("\n", " ")[:70]
        due = item.get("due_at") or ("DRAFT" if item.get("save_to_draft") else "QUEUE")
        print(f"  [{i}] {ch.upper()} @ {due}")
        print(f"        {text_preview}{'...' if len(item.get('text','')) > 70 else ''}")
        if args.dry_run:
            payload = build_input(item)
            print(f"        (dry-run) payload keys: {list(payload.keys())}")
            ok_count += 1
            continue
        try:
            payload = build_input(item)
        except KeyError as e:
            print(f"        ✗ FAIL: missing field {e}")
            continue
        ok, msg = push_post(api_key, payload)
        if ok:
            print(f"        ✓ {msg}")
            ok_count += 1
        else:
            print(f"        ✗ FAIL: {msg}")
        print()

    print(f"Done: {ok_count}/{len(items)} pushed.")
    if not args.dry_run and ok_count > 0:
        print()
        print("Review in Buffer: https://publish.buffer.com/")


if __name__ == "__main__":
    main()
