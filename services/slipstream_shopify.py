"""services/slipstream_shopify.py -- the Slipstream publish adapter for brands
that live on a SHOPIFY storefront instead of a git repo.

Every other Slipstream brand is a Next.js repo, so publishing means "open a PR
with files". Paper & Purpose has no repo: it is a Shopify store, which is exactly
why it was never on the engine. This module is the missing leg. It renders the
generated block array into article HTML and creates a Shopify blog Article
through the Admin API.

DRAFT ONLY, ALWAYS. P&P is CLIENT work and is Miriam-gated: every article is
created with ``published=false`` and is read back to prove it landed as a draft.
There is no publish path in this module and there must never be one. If a
read-back ever shows a live article, ``_force_draft`` puts it back and the
receipt says so loudly. Nothing this engine writes is ever publicly visible until
a human sets it Visible in the Shopify admin.

Credentials (Doppler project ``paperclip``, config ``prd``), per-brand suffixed:
    SHOPIFY_SHOP_<SUFFIX>            e.g. nsapaq-qu.myshopify.com
    SHOPIFY_ADMIN_TOKEN_<SUFFIX>     a static Admin token, IF the store issues one
    SHOPIFY_CLIENT_ID_<SUFFIX>       custom-app client id  (Client Credentials)
    SHOPIFY_CLIENT_SECRET_<SUFFIX>   custom-app client secret

P&P's store is on the Shopify Dev Dashboard, where static Admin tokens expire
every 24h. A STALE ``SHOPIFY_ADMIN_TOKEN_*`` is therefore the normal case, not an
outage: the env token is probed first and, on a 401, a fresh token is minted via
the Client Credentials Grant (the same machinery tools/shopify_article_push.py
and services/pp_scoreboard.py already use).
"""
from __future__ import annotations

import base64
import html
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

import requests

from services.slipstream_validate import validate_blocks

logger = logging.getLogger(__name__)

try:  # this Mac needs certifi for a valid CA bundle; Railway is fine either way
    import certifi

    _CA_BUNDLE: Any = certifi.where()
except Exception:  # pragma: no cover - certifi is a hard dep of requests' extras
    _CA_BUNDLE = True

# Verified against nsapaq-qu.myshopify.com on 2026-08-03: 2024-10 answers
# blogs.json with 200. Same version tools/shopify_article_push.py has used since
# 2026-07. Override with SHOPIFY_API_VERSION if the store is ever moved forward.
DEFAULT_API_VERSION = "2024-10"
DEFAULT_TIMEOUT = 30

# The whole point of this module. Referenced (not inlined) at the one call site
# that builds the payload so the intent is greppable and a "just flip it" edit
# has to walk past this comment.
NEVER_PUBLISH = False


class ShopifyPublishError(Exception):
    """A Shopify write/read failed in a way the engine should surface as a HOLD."""


# ---------------------------------------------------------------------------
# credentials
# ---------------------------------------------------------------------------


def _suffix(cfg: Dict[str, Any]) -> str:
    """Per-brand Doppler secret suffix, e.g. PAPERANDPURPOSE."""
    return (cfg.get("shopify_secret_suffix") or "").strip().upper()


def _shop_host(cfg: Dict[str, Any]) -> str:
    """Resolve the store host from env. NEVER hardcoded: the value comes from
    SHOPIFY_SHOP_<SUFFIX> in Doppler so a store move is a secret edit."""
    raw = (os.getenv(f"SHOPIFY_SHOP_{_suffix(cfg)}") or "").strip()
    if not raw:
        raise ShopifyPublishError(
            f"SHOPIFY_SHOP_{_suffix(cfg)} is not set (cannot resolve the store)")
    return raw if "." in raw else f"{raw}.myshopify.com"


def _api_version(cfg: Dict[str, Any]) -> str:
    return (cfg.get("shopify_api_version")
            or os.getenv("SHOPIFY_API_VERSION")
            or DEFAULT_API_VERSION).strip()


def _admin_base(cfg: Dict[str, Any]) -> str:
    return f"https://{_shop_host(cfg)}/admin/api/{_api_version(cfg)}"


def _headers(token: str) -> Dict[str, str]:
    return {"X-Shopify-Access-Token": token,
            "Accept": "application/json",
            "Content-Type": "application/json"}


def _mint_token(cfg: Dict[str, Any]) -> str:
    """Client Credentials Grant against the store's custom app."""
    sfx = _suffix(cfg)
    cid = (os.getenv(f"SHOPIFY_CLIENT_ID_{sfx}") or "").strip()
    sec = (os.getenv(f"SHOPIFY_CLIENT_SECRET_{sfx}") or "").strip()
    if not (cid and sec):
        raise ShopifyPublishError(
            f"no usable Admin token: SHOPIFY_ADMIN_TOKEN_{sfx} is stale/absent and "
            f"SHOPIFY_CLIENT_ID_{sfx}/SHOPIFY_CLIENT_SECRET_{sfx} are not both set")
    r = requests.post(f"https://{_shop_host(cfg)}/admin/oauth/access_token",
                      json={"client_id": cid, "client_secret": sec,
                            "grant_type": "client_credentials"},
                      timeout=DEFAULT_TIMEOUT, verify=_CA_BUNDLE)
    if not r.ok:
        raise ShopifyPublishError(f"token mint failed (HTTP {r.status_code}): {r.text[:200]}")
    token = (r.json().get("access_token") or "").strip()
    if not token:
        raise ShopifyPublishError("token mint returned no access_token")
    logger.info("[slipstream/shopify] minted a fresh Admin token via client credentials")
    return token


def resolve_token(cfg: Dict[str, Any]) -> str:
    """Return a WORKING Admin token, proving it with a read before handing it back.

    The env token is probed rather than trusted: on the Dev Dashboard it expires
    every 24h, so a 401 there is routine and must silently fall through to a mint
    instead of failing the run. A Theme Access token (shptka_) is rejected outright
    because it 401s on every Blog/Article resource.
    """
    sfx = _suffix(cfg)
    env_token = (os.getenv(f"SHOPIFY_ADMIN_TOKEN_{sfx}") or "").strip()
    if env_token and not env_token.startswith("shptka_"):
        try:
            r = requests.get(f"{_admin_base(cfg)}/blogs.json", headers=_headers(env_token),
                             timeout=DEFAULT_TIMEOUT, verify=_CA_BUNDLE)
            if r.ok:
                return env_token
            logger.info("[slipstream/shopify] SHOPIFY_ADMIN_TOKEN_%s did not authenticate "
                        "(HTTP %s); minting a fresh one", sfx, r.status_code)
        except requests.RequestException as e:
            logger.info("[slipstream/shopify] env-token probe failed (%s); minting", e)
    return _mint_token(cfg)


# ---------------------------------------------------------------------------
# read-only lookups
# ---------------------------------------------------------------------------


def list_blogs(cfg: Dict[str, Any], token: Optional[str] = None) -> List[Dict[str, Any]]:
    """Read-only: every blog on the store, as [{id, handle, title}]."""
    token = token or resolve_token(cfg)
    r = requests.get(f"{_admin_base(cfg)}/blogs.json", headers=_headers(token),
                     timeout=DEFAULT_TIMEOUT, verify=_CA_BUNDLE)
    if not r.ok:
        raise ShopifyPublishError(f"cannot list blogs (HTTP {r.status_code}): {r.text[:200]}")
    return [{"id": b.get("id"), "handle": b.get("handle"), "title": b.get("title")}
            for b in (r.json().get("blogs") or [])]


def resolve_blog_id(cfg: Dict[str, Any], token: str) -> Any:
    """The blog to write into: cfg['blog_handle'] if set, else the store's first."""
    blogs = list_blogs(cfg, token)
    if not blogs:
        raise ShopifyPublishError(
            "the store has no blog. Create one in Shopify admin (Online Store > Blog posts) first")
    wanted = (cfg.get("blog_handle") or "").strip()
    if not wanted:
        return blogs[0]["id"]
    for b in blogs:
        if b["handle"] == wanted or str(b["id"]) == wanted:
            return b["id"]
    raise ShopifyPublishError(
        f"blog handle '{wanted}' not found on the store. Available: {[b['handle'] for b in blogs]}")


def find_article_by_handle(cfg: Dict[str, Any], token: str, blog_id: Any,
                           handle: str) -> Optional[Dict[str, Any]]:
    """Read-only idempotency probe: an existing article with this handle, or None.

    Mirrors the duplicate-slug guard the ts_posts_array path has. Without it a
    re-run drops a second near-identical draft into a CLIENT's store.
    """
    r = requests.get(f"{_admin_base(cfg)}/blogs/{blog_id}/articles.json",
                     headers=_headers(token), params={"handle": handle, "limit": 250},
                     timeout=DEFAULT_TIMEOUT, verify=_CA_BUNDLE)
    if not r.ok:
        logger.warning("[slipstream/shopify] handle probe failed (HTTP %s); continuing",
                       r.status_code)
        return None
    for a in (r.json().get("articles") or []):
        if a.get("handle") == handle:
            return a
    return None


# ---------------------------------------------------------------------------
# block array -> article HTML
# ---------------------------------------------------------------------------


def _esc(value: Any) -> str:
    return html.escape(str(value or ""), quote=True)


def _image_name(src: str, slug: str) -> str:
    """Recover the image_prompts name from a repo-style src (/blog/{slug}-{name}.png).

    Strips the known '/blog/{slug}-' prefix rather than splitting on the last
    hyphen, so a multi-word prompt name ('in-body-1') resolves correctly.
    """
    stem = src.rsplit("/", 1)[-1].removesuffix(".png")
    prefix = f"{slug}-"
    return stem[len(prefix):] if stem.startswith(prefix) else stem.rsplit("-", 1)[-1]


def _render_image(block: Dict[str, Any], slug: str, image_urls: Dict[str, str],
                  omitted: List[str]) -> str:
    """In-body image. The generator emits a repo-style src (/blog/{slug}-{name}.png)
    which does not exist on a Shopify store, so the name is mapped to a real hosted
    URL. With no mapping the figure is DROPPED and recorded in `omitted` so the
    receipt reports it -- a broken image in a client's store is worse than none, and
    a silent drop is worse than both.
    """
    src = str(block.get("src") or "")
    name = _image_name(src, slug) if src else ""
    url = image_urls.get(name) or image_urls.get(src)
    if not url:
        omitted.append(name or src)
        return ""
    cap = block.get("caption")
    figcap = f'<figcaption>{_esc(cap)}</figcaption>' if cap else ""
    return (f'<figure><img src="{_esc(url)}" alt="{_esc(block.get("alt"))}" '
            f'loading="lazy" />{figcap}</figure>')


def _render_block(block: Dict[str, Any], slug: str, image_urls: Dict[str, str],
                  omitted: List[str]) -> str:
    t = (block or {}).get("type")
    if t == "answer":
        # Answer-first, lifted verbatim by answer engines. Plain paragraph on
        # purpose: no wrapper markup for a scraper to trip over.
        return f'<p class="slipstream-answer">{_esc(block.get("text"))}</p>'
    if t == "p":
        return f"<p>{_esc(block.get('text'))}</p>"
    if t == "h2":
        return f"<h2>{_esc(block.get('text'))}</h2>"
    if t == "h3":
        return f"<h3>{_esc(block.get('text'))}</h3>"
    if t == "ul":
        items = "".join(f"<li>{_esc(i)}</li>" for i in (block.get("items") or []))
        return f"<ul>{items}</ul>" if items else ""
    if t == "definition":
        return (f'<p class="slipstream-definition"><strong>{_esc(block.get("term"))}:</strong> '
                f'{_esc(block.get("text"))}</p>')
    if t == "callout":
        title = block.get("title")
        head = f"<p><strong>{_esc(title)}</strong></p>" if title else ""
        return f'<div class="slipstream-callout">{head}<p>{_esc(block.get("text"))}</p></div>'
    if t == "quote":
        return f"<blockquote><p>{_esc(block.get('text'))}</p></blockquote>"
    if t == "image":
        return _render_image(block, slug, image_urls, omitted)
    if t in ("links", "sources"):
        items = "".join(
            f'<li><a href="{_esc(i.get("href"))}">{_esc(i.get("label"))}</a></li>'
            for i in (block.get("items") or []) if (i or {}).get("href"))
        if not items:
            return ""
        title = block.get("title") or ("Sources" if t == "sources" else "Keep reading")
        return f'<div class="slipstream-{t}"><h3>{_esc(title)}</h3><ul>{items}</ul></div>'
    if t == "table":
        heads = "".join(f"<th>{_esc(h)}</th>" for h in (block.get("headers") or []))
        rows = "".join("<tr>" + "".join(f"<td>{_esc(c)}</td>" for c in (row or [])) + "</tr>"
                       for row in (block.get("rows") or []))
        if not rows:
            return ""
        return f"<table><thead><tr>{heads}</tr></thead><tbody>{rows}</tbody></table>"
    if t == "stat":
        href = block.get("href")
        cite = (f' (<a href="{_esc(href)}" rel="nofollow noopener">{_esc(block.get("source"))}</a>)'
                if href else (f' ({_esc(block.get("source"))})' if block.get("source") else ""))
        return (f'<p class="slipstream-stat"><strong>{_esc(block.get("value"))}</strong> '
                f'{_esc(block.get("label"))}{cite}</p>')
    logger.warning("[slipstream/shopify] unknown block type %r skipped", t)
    return ""


def render_article_html(post_obj: Dict[str, Any],
                        image_urls: Optional[Dict[str, str]] = None) -> Tuple[str, List[str]]:
    """Render the block array into Shopify article HTML.

    Returns (html, omitted_image_names). `image_urls` maps an image_prompts name
    ("diagram") to a real hosted URL. The hero is NOT rendered here: it rides on
    the article's own featured-image field.
    """
    image_urls = image_urls or {}
    omitted: List[str] = []
    slug = post_obj.get("slug", "")
    parts = [_render_block(b, slug, image_urls, omitted) for b in (post_obj.get("body") or [])]

    faq = post_obj.get("faq") or []
    if faq:
        # Visible FAQ markup (no injected JSON-LD script: article JSON-LD belongs in
        # the theme's article template, per P&P's niche-authority map).
        qa = "".join(f"<h3>{_esc(f.get('q'))}</h3><p>{_esc(f.get('a'))}</p>" for f in faq)
        parts.append(f'<div class="slipstream-faq"><h2>Frequently asked questions</h2>{qa}</div>')

    return "\n".join(p for p in parts if p), omitted


# ---------------------------------------------------------------------------
# assemble + gate
# ---------------------------------------------------------------------------


def _build_post_object(post: Dict[str, Any], date_str: str) -> Dict[str, Any]:
    """Map the generated post onto the gate's Post shape.

    heroImage is a repo-style path purely so validate_blocks' zero-image HOLD can
    fire the same way it does for every other brand. On Shopify the real hero is
    the article's featured image (uploaded as a base64 attachment), not a path.
    """
    slug = post["slug"]
    obj: Dict[str, Any] = {
        "slug": slug,
        "title": post["title"],
        "description": post["description"],
        "date": date_str,
        "category": post.get("category") or "Journal",
        "heroImage": f"/blog/{slug}-hero.png",
        "heroAlt": post.get("heroAlt") or post["title"],
        "ogTitle": post.get("ogTitle") or post["title"],
    }
    if post.get("faq"):
        obj["faq"] = post["faq"]
    obj["body"] = post.get("body")
    return obj


def assemble_shopify_article(
    post: Dict[str, Any],
    date_str: str,
    cfg: Dict[str, Any],
    images: Optional[Dict[str, bytes]] = None,
) -> Tuple[Dict[str, Any], List[str]]:
    """Return (article_draft, violations) for the shopify_article format.

    Runs the SAME block gate the ts_posts_array format uses (validate_blocks), so
    a P&P post is held on exactly the bars every other brand is held on: leading
    answer block, definition, quote, callout, hero, >=2 images, no em-dash. On any
    violation returns ({}, violations) and the engine HOLDS before any Shopify call.
    """
    post_obj = _build_post_object(post, date_str)
    violations = validate_blocks(post_obj)
    if violations:
        return {}, violations

    images = images or {}
    # In-body images need a public host. write_files is NOT on this app's scopes
    # (verified 2026-08-03: read/write_content + read/write_themes + products only),
    # so there is no Shopify Files upload path today. `image_host_base` is the seam:
    # set it once P&P's images have a home and in-body figures render immediately.
    base = (cfg.get("image_host_base") or "").rstrip("/")
    slug = post_obj["slug"]
    image_urls = ({name: f"{base}/{slug}-{name}.png" for name in images if name != "hero"}
                  if base else {})

    body_html, omitted = render_article_html(post_obj, image_urls)
    if omitted:
        logger.warning("[slipstream/shopify] %d in-body image(s) omitted (no image_host_base "
                       "configured): %s", len(omitted), omitted)

    draft: Dict[str, Any] = {
        "title": post_obj["title"],
        "author": cfg.get("author") or "",
        "body_html": body_html,
        "summary_html": f"<p>{_esc(post_obj['description'])}</p>",
        "tags": ", ".join(post.get("tags") or []),
        "slug": slug,
        "hero_alt": post_obj["heroAlt"],
        "hero_png": images.get("hero"),
        "omitted_images": omitted,
    }
    return draft, []


# ---------------------------------------------------------------------------
# write (draft only)
# ---------------------------------------------------------------------------


def _article_payload(draft: Dict[str, Any]) -> Dict[str, Any]:
    """Build the REST article body. `published` is pinned to the module-level
    NEVER_PUBLISH constant, never a caller-supplied value: Shopify DEFAULTS an
    article to published=true, so omitting this field would publish client work.
    """
    article: Dict[str, Any] = {
        "title": draft["title"],
        "body_html": draft["body_html"],
        "published": NEVER_PUBLISH,  # DRAFT ONLY. Miriam-gated. Never flip this.
    }
    if draft.get("author"):
        article["author"] = draft["author"]
    if draft.get("summary_html"):
        article["summary_html"] = draft["summary_html"]
    if draft.get("tags"):
        article["tags"] = draft["tags"]
    if draft.get("handle"):
        article["handle"] = draft["handle"]
    if draft.get("hero_png"):
        # write_content covers the article's own featured image, so the hero rides
        # in as a base64 attachment and Shopify hosts it. No write_files needed.
        article["image"] = {
            "attachment": base64.b64encode(draft["hero_png"]).decode("ascii"),
            "alt": draft.get("hero_alt") or draft["title"],
        }
    assert article["published"] is False, "refusing to create a published article"
    return {"article": article}


def _redact(payload: Dict[str, Any]) -> Dict[str, Any]:
    """A log/receipt-safe copy: the base64 hero is replaced by its size."""
    art = dict(payload.get("article") or {})
    img = art.get("image")
    if isinstance(img, dict) and img.get("attachment"):
        art["image"] = {"attachment": f"<{len(img['attachment'])} base64 chars>",
                        "alt": img.get("alt")}
    return {"article": art}


def _admin_url(cfg: Dict[str, Any], blog_id: Any, article_id: Any) -> str:
    return f"https://admin.shopify.com/store/{_shop_host(cfg).split('.')[0]}/articles/{article_id}"


def _force_draft(cfg: Dict[str, Any], token: str, blog_id: Any, article_id: Any) -> bool:
    """Fail-closed: if a read-back ever shows the article live, put it back to draft."""
    r = requests.put(f"{_admin_base(cfg)}/blogs/{blog_id}/articles/{article_id}.json",
                     headers=_headers(token),
                     json={"article": {"id": article_id, "published": False}},
                     timeout=DEFAULT_TIMEOUT, verify=_CA_BUNDLE)
    return r.ok


def create_article_draft(
    cfg: Dict[str, Any],
    draft: Dict[str, Any],
    *,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Create the article as an UNPUBLISHED DRAFT. Never publishes.

    dry_run=True resolves the store, the token and the blog id, builds the exact
    payload, runs the duplicate-handle probe, and returns all of it WITHOUT calling
    the write API. Everything except the POST is exercised, so a dry run proves the
    credential path rather than just echoing the inputs.

    SLIPSTREAM_SHOPIFY_DRY_RUN=1 forces dry-run globally, so a live engine can be
    put into look-but-do-not-touch mode without a deploy.
    """
    if os.getenv("SLIPSTREAM_SHOPIFY_DRY_RUN", "").strip().lower() in ("1", "true", "yes", "on"):
        dry_run = True

    token = resolve_token(cfg)
    blog_id = resolve_blog_id(cfg, token)
    payload = _article_payload(draft)
    store = _shop_host(cfg)
    endpoint = f"{_admin_base(cfg)}/blogs/{blog_id}/articles.json"

    existing = find_article_by_handle(cfg, token, blog_id, draft.get("slug", ""))
    if existing:
        return {"ok": False, "duplicate": True, "store": store, "blog_id": blog_id,
                "article_id": existing.get("id"),
                "admin_url": _admin_url(cfg, blog_id, existing.get("id")),
                "error": f"an article with handle '{draft.get('slug')}' already exists "
                         f"(idempotent guard, refusing duplicate)"}

    if dry_run:
        logger.info("[slipstream/shopify] DRY RUN: would POST %s (published=false)", endpoint)
        return {"ok": True, "dry_run": True, "store": store, "blog_id": blog_id,
                "api_version": _api_version(cfg), "endpoint": endpoint,
                "would_create": _redact(payload),
                "body_html_chars": len(payload["article"]["body_html"]),
                "hero_bytes": len(draft.get("hero_png") or b""),
                "omitted_images": draft.get("omitted_images") or [],
                "note": "no write API was called"}

    r = requests.post(endpoint, headers=_headers(token), json=payload,
                      timeout=DEFAULT_TIMEOUT, verify=_CA_BUNDLE)
    if r.status_code not in (200, 201):
        raise ShopifyPublishError(f"article create failed (HTTP {r.status_code}): {r.text[:300]}")

    created = (r.json().get("article") or {})
    article_id = created.get("id")

    # Prove it landed as a draft rather than trusting the request we sent.
    published_at = created.get("published_at")
    forced = False
    if published_at:
        logger.error("[slipstream/shopify] article %s came back PUBLISHED (%s); forcing back "
                     "to draft", article_id, published_at)
        forced = _force_draft(cfg, token, blog_id, article_id)

    return {"ok": True, "dry_run": False, "store": store, "blog_id": blog_id,
            "api_version": _api_version(cfg), "article_id": article_id,
            "handle": created.get("handle"), "title": created.get("title"),
            "published_at": published_at, "draft_verified": not published_at,
            "forced_back_to_draft": forced,
            "admin_url": _admin_url(cfg, blog_id, article_id),
            "omitted_images": draft.get("omitted_images") or []}


def preflight(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Read-only: prove the store, credential and blog all resolve. No writes."""
    try:
        token = resolve_token(cfg)
        blogs = list_blogs(cfg, token)
        return {"ok": True, "store": _shop_host(cfg), "api_version": _api_version(cfg),
                "blogs": blogs, "blog_id": resolve_blog_id(cfg, token)}
    except (ShopifyPublishError, requests.RequestException) as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
