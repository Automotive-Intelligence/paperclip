"""tools/social_load.py — THE one social loader (file 121, Phase 1: The Pipe).

Every scheduled social post rides through load_jobs(): Zernio for own brands,
Buffer DRAFTS for P&P (the draft is the client gate). Guarantees:
  * DRY-RUN by default; commit is explicit.
  * Queue guard: refuses a post when the rail already has one for the same
    brand+platform+day (override: allow_stack=True).
  * UTM discipline: every http(s) link in every post is tagged. No UTM, no schedule.
  * Registry: one JSONL row per scheduled post (the Phase-3 attribution join key).
  * WD hard block until config/social_load.json wd_rename_done flips true.

Spec: ~/avo-telemetry/marketing_deliverables/121_social_publishing_os.md
"""
from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone as _tz
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from zoneinfo import ZoneInfo

_URL_RE = re.compile(r"https?://[^\s\)\]\}>\"']+")
_CENTRAL = ZoneInfo("America/Chicago")
_TCO_LEN = 23  # Twitter wraps every URL to a fixed-width t.co link


def tweet_length(text: str) -> int:
    """Twitter weighted length for English brand copy: each URL counts as 23
    (t.co), so UTM params do not lengthen the tweet. Code-point length is exact
    for Latin copy; CJK weighting is not needed for our brands."""
    return len(_URL_RE.sub("x" * _TCO_LEN, text))


# ---------------------------------------------------------------- UTM
def add_utm(url: str, platform: str, brand: str, content_id: str,
            entry_point: str, slot: str) -> str:
    parts = urlsplit(url)
    q = parse_qsl(parts.query, keep_blank_values=True)
    q += [("utm_source", platform), ("utm_medium", "social"),
          ("utm_campaign", f"{brand}_{content_id}"),
          ("utm_content", f"{entry_point}-{slot}")]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(q), parts.fragment))


def tag_links(text: str, platform: str, brand: str, content_id: str,
              entry_point: str, slot: str) -> str:
    return _URL_RE.sub(
        lambda m: add_utm(m.group(0), platform, brand, content_id, entry_point, slot),
        text)


# ---------------------------------------------------------------- registry
def registry_path() -> str:
    return os.environ.get("SOCIAL_REGISTRY_PATH") or os.path.expanduser(
        "~/avo-telemetry/social_registry.jsonl")


def append_registry(row: dict) -> None:
    path = registry_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    out = {"ts": datetime.now(_tz.utc).isoformat(timespec="seconds"), **row}
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(out, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------- routing
class WdBlockedError(RuntimeError):
    """WD content refused: handles are still @callingdigital (file 120)."""


class NoRailError(RuntimeError):
    """Brand has no distribution rail yet (e.g. Book'd until Ryan's key)."""


class MediaUnreachableError(RuntimeError):
    """A post's source media URL could not be downloaded at SCHEDULE time.

    Zernio fetches media only at PUBLISH time (days after we schedule), so an
    external URL that 404s by then fails the post silently. We re-host every
    non-media.zernio.com URL onto Zernio's CDN up front; if the source can't be
    fetched we raise THIS (naming the URL + content_id) rather than scheduling a
    job with dead media. (3 WD posts failed this way: agency-pricing-hero.png.)"""


BRAND_ALIASES = {
    "automotive intelligence": "avi",
    "the ai phone guy": "aipg", "ai phone guy": "aipg",
    "worship digital": "wd",
    "agent empire": "agent_empire",
    "book'd": "bookd",
    "paper & purpose": "paperandpurpose", "paper and purpose": "paperandpurpose",
}
_CFG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "config", "social_load.json")


def canonical_brand(brand: str) -> str:
    b = brand.strip().lower()
    return BRAND_ALIASES.get(b, b)


def load_config() -> dict:
    try:
        return json.load(open(_CFG_PATH, encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def route_for_brand(brand: str, cfg: Optional[dict] = None) -> str:
    b = canonical_brand(brand)
    if b == "paperandpurpose":
        return "buffer"
    if b == "bookd":
        raise NoRailError("Book'd has no rail: Ryan posts himself or issues a scoped key (file 121).")
    if b == "wd":
        conf = cfg if cfg is not None else load_config()
        if not conf.get("wd_rename_done"):
            raise WdBlockedError(
                "WD is hard-blocked: handles are still @callingdigital. "
                "Complete marketing_deliverables/120_wd_handle_rename_runbook.md, "
                "then flip wd_rename_done in config/social_load.json.")
    return "zernio"


# ---------------------------------------------------------------- queue guard
def _rail_local_day(iso_utc: str) -> Optional[str]:
    if not iso_utc:
        return None
    try:
        dt = datetime.fromisoformat(iso_utc.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.astimezone(_CENTRAL).date().isoformat()


def find_conflicts(existing: List[dict], platform: str, day: str,
                   account_id: Optional[str] = None) -> List[dict]:
    """Rows in the rail's live queue already occupying brand+platform+local-day.

    Zernio rows carry scheduledFor (UTC) + platforms[{platform, accountId}] where
    accountId may be an object (extract ._id). Buffer rows carry dueAt/scheduledAt
    + channelId. The rail's calendar is the single source of truth; this guard is
    why nobody cross-checks documents (file 121)."""
    hits = []
    for p in existing:
        if (p.get("status") or "").lower() not in ("scheduled", "pending", "queued", "draft"):
            continue
        when = p.get("scheduledFor") or p.get("dueAt") or p.get("scheduledAt") or ""
        if _rail_local_day(str(when)) != day:
            continue
        plats = p.get("platforms") or []
        if plats:                                    # zernio shape
            for c in plats:
                if c.get("platform") != platform:
                    continue
                aid = c.get("accountId")
                aid = aid.get("_id") if isinstance(aid, dict) else aid
                if account_id is None or str(aid) == str(account_id):
                    hits.append(p)
                    break
        else:                                        # buffer shape
            if account_id is None or str(p.get("channelId")) == str(account_id):
                hits.append(p)
    return hits


# ---------------------------------------------------------------- media re-host
_ZERNIO_CDN_HOST = "media.zernio.com"


def _default_media_fetch(url: str):
    """Download media bytes at schedule time. Returns (bytes, mime_type).

    Raises on any non-200/unreachable so the caller can fail loud (see
    MediaUnreachableError). Lazy `requests` import keeps module import light."""
    import mimetypes
    import requests

    resp = requests.get(url, timeout=30)
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}")
    mime = (resp.headers.get("Content-Type") or "").split(";")[0].strip()
    if not mime:
        mime = mimetypes.guess_type(urlsplit(url).path)[0] or "application/octet-stream"
    return resp.content, mime


def _default_media_upload(file_bytes: bytes, filename: str, mime_type: str) -> str:
    """Push bytes to Zernio's CDN, returning a persistent media.zernio.com URL.

    Same helper studio_publish.py / services.social_pipeline already use. Lazy
    import so Zernio-free (buffer-only) runs never load it."""
    from tools.zernio import upload_media_to_zernio
    return upload_media_to_zernio(file_bytes=file_bytes, filename=filename,
                                  mime_type=mime_type)


def rehost_media_urls(media_urls: List[str], *, content_id: str, brand: str,
                      fetch, upload) -> List[str]:
    """Return media_urls with every non-CDN URL re-hosted onto media.zernio.com.

    For each URL whose host is not media.zernio.com we download the bytes NOW and
    re-upload them to Zernio's CDN, replacing the URL with the returned persistent
    one. URLs already on media.zernio.com pass through untouched (never re-uploaded).
    A download failure raises MediaUnreachableError naming the URL + content_id, so
    a job with dead media fails at SCHEDULE time instead of silently at publish."""
    out: List[str] = []
    for url in media_urls:
        host = (urlsplit(url).hostname or "").lower()
        if host == _ZERNIO_CDN_HOST:
            out.append(url)
            continue
        try:
            file_bytes, mime_type = fetch(url)
        except Exception as e:
            raise MediaUnreachableError(
                f"media unreachable at schedule time for content_id={content_id} "
                f"(brand={brand}): {url} — {type(e).__name__}: {e}. "
                "Refusing to schedule a Zernio post whose media would 404 at "
                "publish time.") from e
        filename = os.path.basename(urlsplit(url).path) or f"{content_id}.bin"
        out.append(upload(file_bytes=file_bytes, filename=filename, mime_type=mime_type))
    return out


# ---------------------------------------------------------------- orchestrator
@dataclass
class PostJob:
    brand: str
    platform: str            # zernio id ("twitter") or buffer service ("instagram")
    content: str
    scheduled_for: str       # local ISO "YYYY-MM-DDTHH:MM:SS"
    content_id: str
    entry_point: str         # "studio" | "blog_engine" | "adhoc"
    tz: str = "America/Chicago"
    media_urls: List[str] = field(default_factory=list)
    account_id: Optional[str] = None      # zernio account; resolved by the caller
    business_key: str = ""                # buffer lane (e.g. "paperandpurpose")


def _real_rails() -> Dict[str, Any]:
    """Late imports so tests never touch the network or need API keys.

    The Buffer rail (P&P only) pulls in crewai via tools.buffer. Import it
    lazily, on first buffer use, so Zernio-routed brands (AvI / AIPG / WD)
    can run the loader in a crewai-free environment (e.g. the blog engine's
    image venv). Zernio needs only requests, which is always present."""
    from tools.zernio import list_zernio_posts, publish_to_zernio

    def _buffer():
        from tools.buffer import buffer_create_draft_post, buffer_list_posts
        return buffer_create_draft_post, buffer_list_posts

    return {
        "zernio_list": list_zernio_posts,
        "zernio_publish": lambda **kw: publish_to_zernio(**kw),
        "media_fetch": _default_media_fetch,
        "media_upload": _default_media_upload,
        "buffer_list": lambda channel_id: json.loads(
            _buffer()[1].func(channel_id, "draft", 50) or "[]"),
        "buffer_draft": lambda business_key, text, media_csv:
            _buffer()[0].func(business_key, text, media_csv, ""),
    }


def load_jobs(jobs: List["PostJob"], commit: bool = False, allow_stack: bool = False,
              rails: Optional[Dict[str, Any]] = None,
              cfg: Optional[dict] = None) -> List[dict]:
    """cfg overrides config/social_load.json — tests MUST pass it so they never
    depend on the live WD-rename flag (that dependency broke a test once)."""
    r = rails or _real_rails()
    results: List[dict] = []
    zernio_queue: Optional[List[dict]] = None      # fetched once per call
    for i, job in enumerate(jobs):
        brand = canonical_brand(job.brand)
        try:
            rail = route_for_brand(brand, cfg=cfg)
        except (WdBlockedError, NoRailError) as e:
            results.append({"job": job, "action": "blocked", "detail": str(e)})
            continue

        content = tag_links(job.content, job.platform, brand, job.content_id,
                            job.entry_point, str(i))
        tlen = tweet_length(content)
        if job.platform == "twitter" and tlen > 280:
            results.append({"job": job, "action": "too_long",
                            "detail": f"twitter post is {tlen} chars (>280) after UTM tagging"})
            continue
        day = job.scheduled_for.split("T")[0]

        if rail == "zernio":
            if zernio_queue is None:
                zernio_queue = r["zernio_list"]()
            hits = find_conflicts(zernio_queue, job.platform, day, job.account_id)
            if hits and not allow_stack:
                results.append({"job": job, "action": "conflict",
                                "detail": [h.get("_id") for h in hits]})
                continue
            if not commit:
                results.append({"job": job, "action": "dry-run", "detail": f"-> {job.scheduled_for}"})
                continue
            # Re-host external media onto Zernio's CDN NOW (fail loud if a source
            # URL is dead) so the persistent url is baked into the scheduled post
            # — Zernio otherwise fetches the original only at publish time.
            media_urls = rehost_media_urls(
                job.media_urls or [], content_id=job.content_id, brand=brand,
                fetch=r.get("media_fetch") or _default_media_fetch,
                upload=r.get("media_upload") or _default_media_upload)
            res = r["zernio_publish"](content=content, platforms=[job.platform],
                                      account_ids=[job.account_id] if job.account_id else None,
                                      scheduled_for=job.scheduled_for,
                                      media_urls=media_urls or None, timezone=job.tz)
            append_registry({"brand": brand, "rail": "zernio", "platform": job.platform,
                             "account_id": job.account_id, "post_id": res.get("_id"),
                             "scheduled_for": job.scheduled_for, "tz": job.tz,
                             "content_id": job.content_id,
                             "utm_campaign": f"{brand}_{job.content_id}",
                             "entry_point": job.entry_point,
                             "media_url": (media_urls or [None])[0]})
            results.append({"job": job, "action": "scheduled", "detail": res})
        else:  # buffer drafts (P&P and future clients); the draft IS the client gate
            if not commit:
                results.append({"job": job, "action": "dry-run", "detail": "buffer draft"})
                continue
            raw = r["buffer_draft"](job.business_key or brand, content,
                                    ",".join(job.media_urls))
            try:
                out = json.loads(raw)
            except ValueError:
                results.append({"job": job, "action": "error", "detail": raw[:300]})
                continue
            errs = [o for o in out if o.get("error")]
            ok = [o for o in out if o.get("post")]
            for o in ok:
                append_registry({"brand": brand, "rail": "buffer", "platform": job.platform,
                                 "account_id": o.get("channel_id"),
                                 "post_id": (o.get("post") or {}).get("id"),
                                 "scheduled_for": job.scheduled_for, "tz": job.tz,
                                 "content_id": job.content_id,
                                 "utm_campaign": f"{brand}_{job.content_id}",
                                 "entry_point": job.entry_point,
                                 "media_url": (job.media_urls or [None])[0]})
            results.append({"job": job, "action": "drafted" if ok and not errs else "error",
                            "detail": out})
    return results


# ---------------------------------------------------------------- CLI
def _fake_rails_for_cli() -> Dict[str, Any]:
    """SOCIAL_LOAD_FAKE_RAILS=1: offline rails so dry-runs need no keys."""
    return {"zernio_list": lambda: [],
            "zernio_publish": lambda **kw: {"_id": "fake", "status": "scheduled"},
            "media_fetch": lambda url: (b"", "image/png"),
            "media_upload": lambda file_bytes, filename, mime_type:
                f"https://{_ZERNIO_CDN_HOST}/{filename}",
            "buffer_list": lambda cid: [],
            "buffer_draft": lambda *a: json.dumps([])}


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="file-121 Pipe: the one social loader")
    ap.add_argument("jobs", help="JSON file: list of PostJob dicts")
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--allow-stack", action="store_true")
    args = ap.parse_args()
    raw = json.load(open(args.jobs, encoding="utf-8"))
    jobs = [PostJob(**{k: v for k, v in j.items() if k in PostJob.__dataclass_fields__})
            for j in raw]
    rails = _fake_rails_for_cli() if os.getenv("SOCIAL_LOAD_FAKE_RAILS") == "1" else None
    results = load_jobs(jobs, commit=args.commit, allow_stack=args.allow_stack, rails=rails)
    bad = 0
    for res in results:
        j = res["job"]
        print(f"{j.brand:16} {j.platform:10} {res['action']:9} {res.get('detail')}")
        if res["action"] in ("error", "conflict", "too_long"):
            bad += 1
    if not args.commit:
        print("DRY-RUN complete. Re-run with --commit to schedule.")
    return 4 if bad else 0


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    raise SystemExit(main())
