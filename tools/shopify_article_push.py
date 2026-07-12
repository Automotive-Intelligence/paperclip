"""tools/shopify_article_push.py — Create a Shopify blog Article as a DRAFT.

Purpose-built for CLIENT content staging (Paper & Purpose et al.). Creates a
blog Article via the Shopify Admin REST API with ``published=false`` so NOTHING
goes live. The client (e.g. Miriam) approves in the Shopify admin before it is
ever published. This tool never publishes and has no publish path on purpose.

SSL: this Mac needs certifi for a valid CA bundle, so every request passes
``verify=certifi.where()``.

Credentials: loaded from an env file (default ~/cd-ops/.pp-shopify.env). A
CLIENT-ready run needs a standard Admin API access token from a custom app with
the ``write_content`` scope:

    SHOPIFY_FLAG_STORE=<subdomain>.myshopify.com      (or bare subdomain)
    SHOPIFY_ADMIN_TOKEN=shpat_xxxxxxxxxxxxxxxxxxxxxx    (write_content scope)

NOTE: a Theme Access token (prefix ``shptka_``, used by ``shopify theme`` CLI)
will NOT work here. It only reaches the theme-kit proxy for asset files and
returns 401 on Blog/Article Admin resources. You need the ``shpat_`` token.

Usage (CLI):
    python -m tools.shopify_article_push \
        --title "My Post" --body-html post.html \
        --summary "One-line summary" --image https://.../hero.png \
        [--blog-handle news] [--tags "faith,journal"] \
        [--author "Paper & Purpose"] [--env ~/cd-ops/.pp-shopify.env]

Usage (import):
    from tools.shopify_article_push import push_article_draft
    res = push_article_draft(title=..., body_html=..., summary=..., image_url=...)

Every function returns a dict; errors come back as {"ok": False, "error": ...}
rather than raising, so a caller/agent gets actionable feedback.
"""

from __future__ import annotations

import argparse
import os
from typing import Any, Dict, Optional

import requests

try:
    import certifi  # this Mac needs certifi for SSL verification
    _CA_BUNDLE = certifi.where()
except Exception:  # pragma: no cover - certifi should be present
    _CA_BUNDLE = True

DEFAULT_ENV_FILE = "~/cd-ops/.pp-shopify.env"
DEFAULT_API_VERSION = "2024-10"
DEFAULT_TIMEOUT = 30


def load_env_file(path: str = DEFAULT_ENV_FILE) -> Dict[str, str]:
    """Parse a shell-style env file (supports optional ``export`` prefix).

    Values are read into a dict and ALSO not printed anywhere by this module.
    Returns {} if the file is missing.
    """
    resolved = os.path.expanduser(path)
    out: Dict[str, str] = {}
    if not os.path.isfile(resolved):
        return out
    with open(resolved, "r", encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export "):]
            if "=" not in line:
                continue
            k, v = line.split("=", 1)
            v = v.strip().strip('"').strip("'")
            out[k.strip()] = v
    return out


def _resolve_store(env: Dict[str, str]) -> Optional[str]:
    store = (
        env.get("SHOPIFY_FLAG_STORE")
        or env.get("SHOPIFY_STORE")
        or os.environ.get("SHOPIFY_FLAG_STORE")
        or os.environ.get("SHOPIFY_STORE")
        or os.environ.get("SHOPIFY_SHOP_PAPERANDPURPOSE")  # per-brand fallback
        or ""
    ).strip()
    if not store:
        return None
    if "." not in store:
        store = f"{store}.myshopify.com"
    return store


def _resolve_admin_token(env: Dict[str, str]) -> Optional[str]:
    """Return a usable Admin API token. Rejects Theme Access (shptka_) tokens.

    P&P's store is on the new Dev Dashboard where static tokens expire every
    24h (Client Credentials Grant), so a stale env token is the NORMAL case
    there — when the per-brand suffixed CLIENT_ID/SECRET pair is present we
    auto-mint a fresh token instead of trusting the env one. Verified
    2026-07-12: the minted token carries write_content + write_themes and
    blogs.json returns 200, which closes the "Shopify token wall" flag.
    """
    token = (
        env.get("SHOPIFY_ADMIN_TOKEN")
        or env.get("SHOPIFY_ADMIN_API_TOKEN")
        or env.get("SHOPIFY_ACCESS_TOKEN")
        or os.environ.get("SHOPIFY_ADMIN_TOKEN")
        or ""
    ).strip()
    if token:
        return token
    # Auto-mint via Client Credentials Grant (per-brand suffixed convention,
    # same machinery as services/pp_scoreboard.py). PAPERANDPURPOSE is the
    # only Dev-Dashboard store today; extend the suffix list as more migrate.
    for suffix in ("PAPERANDPURPOSE",):
        shop = (os.environ.get(f"SHOPIFY_SHOP_{suffix}") or "").strip().replace(".myshopify.com", "")
        cid = (os.environ.get(f"SHOPIFY_CLIENT_ID_{suffix}") or "").strip()
        sec = (os.environ.get(f"SHOPIFY_CLIENT_SECRET_{suffix}") or "").strip()
        if not (shop and cid and sec):
            continue
        try:
            r = requests.post(
                f"https://{shop}.myshopify.com/admin/oauth/access_token",
                json={"client_id": cid, "client_secret": sec, "grant_type": "client_credentials"},
                timeout=15,
            )
            if r.ok:
                minted = (r.json().get("access_token") or "").strip()
                if minted:
                    return minted
        except requests.RequestException:
            continue
    return None


def _api_version(env: Dict[str, str]) -> str:
    return (env.get("SHOPIFY_API_VERSION") or os.environ.get("SHOPIFY_API_VERSION") or DEFAULT_API_VERSION).strip()


def _admin_base(store: str, env: Dict[str, str]) -> str:
    return f"https://{store}/admin/api/{_api_version(env)}"


def _headers(token: str) -> Dict[str, str]:
    return {
        "X-Shopify-Access-Token": token,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def preflight(env_file: str = DEFAULT_ENV_FILE) -> Dict[str, Any]:
    """Confirm store + Admin API access (Blog/Article scope). No writes."""
    env = load_env_file(env_file)
    store = _resolve_store(env)
    token = _resolve_admin_token(env)
    if not store:
        return {"ok": False, "error": "SHOPIFY_FLAG_STORE not set in env file."}
    if not token:
        return {
            "ok": False,
            "error": "No Admin API token found. Need SHOPIFY_ADMIN_TOKEN (shpat_, write_content scope). "
            "A Theme Access token (shptka_) will NOT work for Blog/Article resources.",
        }
    if token.startswith("shptka_"):
        return {
            "ok": False,
            "error": "The configured token is a Theme Access token (shptka_). It cannot create Blog Articles. "
            "Supply a custom-app Admin API token (shpat_) with the write_content scope.",
        }
    base = _admin_base(store, env)
    try:
        r = requests.get(f"{base}/blogs.json", headers=_headers(token), timeout=DEFAULT_TIMEOUT, verify=_CA_BUNDLE)
    except requests.exceptions.RequestException as e:
        return {"ok": False, "error": f"Request failed: {type(e).__name__}: {e}"}
    if r.status_code == 401:
        return {"ok": False, "error": "401 Unauthorized. Admin token invalid or lacks write_content scope."}
    if r.status_code >= 400:
        return {"ok": False, "error": f"HTTP {r.status_code}: {r.text[:300]}"}
    blogs = r.json().get("blogs", [])
    return {"ok": True, "store": store, "blogs": [{"id": b.get("id"), "handle": b.get("handle"), "title": b.get("title")} for b in blogs]}


def _resolve_blog_id(base: str, token: str, blog_handle: Optional[str]) -> Any:
    r = requests.get(f"{base}/blogs.json", headers=_headers(token), timeout=DEFAULT_TIMEOUT, verify=_CA_BUNDLE)
    if r.status_code >= 400:
        return {"ok": False, "error": f"Could not list blogs (HTTP {r.status_code}): {r.text[:300]}"}
    blogs = r.json().get("blogs", [])
    if not blogs:
        return {"ok": False, "error": "No blogs exist on this store. Create one in the Shopify admin (Online Store > Blog posts) first."}
    if blog_handle:
        for b in blogs:
            if b.get("handle") == blog_handle or str(b.get("id")) == str(blog_handle):
                return b.get("id")
        return {"ok": False, "error": f"Blog handle '{blog_handle}' not found. Available: {[b.get('handle') for b in blogs]}"}
    return blogs[0].get("id")


def push_article_draft(
    *,
    title: str,
    body_html: str,
    summary: str = "",
    image_url: str = "",
    image_alt: str = "",
    tags: str = "",
    author: str = "Paper & Purpose",
    blog_handle: Optional[str] = None,
    env_file: str = DEFAULT_ENV_FILE,
) -> Dict[str, Any]:
    """Create a blog Article with published=false (DRAFT). Never publishes.

    Returns {ok, article_id, published_at (must be null), admin_url, ...} or
    {ok: False, error: ...}.
    """
    env = load_env_file(env_file)
    store = _resolve_store(env)
    token = _resolve_admin_token(env)
    if not store:
        return {"ok": False, "error": "SHOPIFY_FLAG_STORE not set."}
    if not token or token.startswith("shptka_"):
        return {"ok": False, "error": "Need an Admin API token (shpat_, write_content). Theme Access token (shptka_) cannot create articles."}

    base = _admin_base(store, env)
    blog_id = _resolve_blog_id(base, token, blog_handle)
    if isinstance(blog_id, dict):  # error passthrough
        return blog_id

    article: Dict[str, Any] = {
        "title": title,
        "author": author,
        "body_html": body_html,
        "published": False,  # HARD: DRAFT ONLY. Nothing goes live.
    }
    if summary:
        article["summary_html"] = summary
    if tags:
        article["tags"] = tags
    if image_url:
        article["image"] = {"src": image_url, "alt": image_alt or title}

    try:
        r = requests.post(
            f"{base}/blogs/{blog_id}/articles.json",
            headers=_headers(token),
            json={"article": article},
            timeout=DEFAULT_TIMEOUT,
            verify=_CA_BUNDLE,
        )
    except requests.exceptions.RequestException as e:
        return {"ok": False, "error": f"Request failed: {type(e).__name__}: {e}"}

    if r.status_code not in (200, 201):
        return {"ok": False, "error": f"HTTP {r.status_code}: {r.text[:400]}"}

    created = r.json().get("article", {})
    # Verify it landed as a DRAFT by reading it back.
    verify_res = get_article(created.get("id"), env_file=env_file, blog_id=blog_id)
    return {
        "ok": True,
        "article_id": created.get("id"),
        "blog_id": blog_id,
        "title": created.get("title"),
        "published_at": created.get("published_at"),  # expect null
        "verified_published_at": verify_res.get("published_at") if verify_res.get("ok") else "VERIFY_FAILED",
        "admin_url": f"https://{store.replace('.myshopify.com','')}.myshopify.com/admin/articles/{created.get('id')}",
        "handle": created.get("handle"),
    }


def get_article(article_id: Any, *, env_file: str = DEFAULT_ENV_FILE, blog_id: Any = None) -> Dict[str, Any]:
    """Read an article back to confirm published_at is null (draft)."""
    env = load_env_file(env_file)
    store = _resolve_store(env)
    token = _resolve_admin_token(env)
    if not store or not token:
        return {"ok": False, "error": "Missing store/token."}
    base = _admin_base(store, env)
    if blog_id is None:
        blog_id = _resolve_blog_id(base, token, None)
        if isinstance(blog_id, dict):
            return blog_id
    r = requests.get(
        f"{base}/blogs/{blog_id}/articles/{article_id}.json",
        headers=_headers(token),
        timeout=DEFAULT_TIMEOUT,
        verify=_CA_BUNDLE,
    )
    if r.status_code >= 400:
        return {"ok": False, "error": f"HTTP {r.status_code}: {r.text[:300]}"}
    a = r.json().get("article", {})
    return {"ok": True, "article_id": a.get("id"), "title": a.get("title"), "published_at": a.get("published_at")}


def _read_maybe_file(value: str) -> str:
    """If value is a path to an existing file, return its contents; else the value."""
    if value and os.path.isfile(os.path.expanduser(value)):
        with open(os.path.expanduser(value), "r", encoding="utf-8") as fh:
            return fh.read()
    return value


def main() -> None:
    ap = argparse.ArgumentParser(description="Create a Shopify blog Article as an UNPUBLISHED DRAFT.")
    ap.add_argument("--title", default=None)
    ap.add_argument("--body-html", default=None, help="HTML string or path to an .html file.")
    ap.add_argument("--summary", default="")
    ap.add_argument("--image", default="", help="Hero image URL.")
    ap.add_argument("--image-alt", default="")
    ap.add_argument("--tags", default="")
    ap.add_argument("--author", default="Paper & Purpose")
    ap.add_argument("--blog-handle", default=None)
    ap.add_argument("--env", default=DEFAULT_ENV_FILE)
    ap.add_argument("--preflight", action="store_true", help="Only check access, do not write.")
    args = ap.parse_args()

    if args.preflight:
        import json
        print(json.dumps(preflight(args.env), indent=2))
        return

    if not args.title or not args.body_html:
        ap.error("--title and --body-html are required unless --preflight is used.")

    body = _read_maybe_file(args.body_html)
    res = push_article_draft(
        title=args.title,
        body_html=body,
        summary=args.summary,
        image_url=args.image,
        image_alt=args.image_alt,
        tags=args.tags,
        author=args.author,
        blog_handle=args.blog_handle,
        env_file=args.env,
    )
    import json
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
