"""
Paper & Purpose blog -> Klaviyo email rail.

Why this exists (2026-09-22): Miriam publishes a blog post on Shopify (Tuesdays,
9:00 AM CT) and expects the matching email to her opted-in list the same
morning. Until now that required Michael to relay her text into a chat and a
human to build the send, which is how a 9:30 email went out at 12:16.

What it does, every weekday at :20 past the hour from 9 AM to 3 PM CT:
  1. Pull articles from the P&P Shopify blog published TODAY (America/Chicago).
  2. Skip any article that already carries the `pp_email.campaign_id` metafield
     (durable dedupe that lives on the article, not on ephemeral disk).
  3. Build a branded email from the article (masthead, her opener, hero image,
     Read button, reserve P.S., footer with {% unsubscribe %}). No LLM, no
     em-dashes, nothing invented: the copy is the article's own first paragraphs.
  4. Create the Klaviyo campaign to the opted-in list and schedule it for
     max(publish + 30 min, now + 10 min). Campaigns are never sent to buyers-only
     lists here; that path stays manual and Miriam-gated.
  5. Stamp the article metafield with the campaign id, post the preview link to
     Slack (#client-marketing-garage) so anyone can cancel in Klaviyo before it
     fires, and record a watchdog heartbeat.

Kill switch: PP_BLOG_EMAIL_RAIL_ENABLED=0.
Config (env, all optional): PP_BLOG_EMAIL_LIST_ID (default USLsqg),
PP_BLOG_ID (default 93783949400), PP_BLOG_EMAIL_DISPATCH_CHANNEL
(default client-marketing-garage).
"""
from __future__ import annotations

import datetime as _dt
import html as _html
import logging
import os
import re
from typing import Any, Dict, List, Optional

import pytz
import requests

logger = logging.getLogger(__name__)

CST = pytz.timezone("America/Chicago")
BUSINESS = "paperandpurpose"
SHOP_HOST_DEFAULT = "nsapaq-qu.myshopify.com"
API_VERSION = "2025-07"
KLAVIYO_BASE = "https://a.klaviyo.com/api"
KLAVIYO_REVISION = "2025-07-15"
BLOG_ID_DEFAULT = "93783949400"
LIST_ID_DEFAULT = "USLsqg"
METAFIELD_NS = "pp_email"
METAFIELD_KEY = "campaign_id"
SITE = "https://paperandpurpose.co"
PRODUCT_PATH = "/products/be-transformed-guided-mind-renewal-journal"
FROM_EMAIL = "hello@paperandpurpose.co"
FROM_LABEL = "Miriam at Paper & Purpose"
DISPATCH_URL_DEFAULT = "https://avo-production-e7f2.up.railway.app/pit-wall/dispatch"
LOGO_HEADER = "https://cdn.shopify.com/s/files/1/0712/6168/3800/files/pp_email_logo1_header.png?v=1782180091"
LOGO_FOOTER = "https://cdn.shopify.com/s/files/1/0712/6168/3800/files/pp_email_logo3_footer.png?v=1782180094"
TIMEOUT = 20


# ── config ────────────────────────────────────────────────────────────────

def enabled() -> bool:
    return (os.getenv("PP_BLOG_EMAIL_RAIL_ENABLED") or "1").strip() not in ("0", "false", "no", "off")


def _list_id() -> str:
    return (os.getenv("PP_BLOG_EMAIL_LIST_ID") or LIST_ID_DEFAULT).strip()


def _blog_id() -> str:
    return (os.getenv("PP_BLOG_ID") or BLOG_ID_DEFAULT).strip()


def _shop_host() -> str:
    raw = (os.getenv("SHOPIFY_SHOP_PAPERANDPURPOSE") or "").strip()
    if not raw:
        return SHOP_HOST_DEFAULT
    # The env value is the friendly handle; the API and token belong to the
    # original nsapaq-qu handle. Both are the same store; prefer the API one.
    if raw.startswith("paper-purpose"):
        return SHOP_HOST_DEFAULT
    return raw if raw.endswith(".myshopify.com") else f"{raw}.myshopify.com"


# ── shopify ───────────────────────────────────────────────────────────────

def shopify_token() -> Optional[str]:
    """Mint a 24h Admin token from the client-credentials app; fall back to a
    static admin token if one is set. The static one in env is known-stale."""
    cid = (os.getenv("SHOPIFY_CLIENT_ID_PAPERANDPURPOSE") or "").strip()
    sec = (os.getenv("SHOPIFY_CLIENT_SECRET_PAPERANDPURPOSE") or "").strip()
    if cid and sec:
        try:
            r = requests.post(
                f"https://{_shop_host()}/admin/oauth/access_token",
                json={"client_id": cid, "client_secret": sec, "grant_type": "client_credentials"},
                timeout=TIMEOUT,
            )
            if r.ok and r.json().get("access_token"):
                return r.json()["access_token"]
            logger.warning("[pp-blog-email] token mint failed HTTP %s: %s", r.status_code, r.text[:200])
        except requests.RequestException as e:
            logger.warning("[pp-blog-email] token mint error: %s", e)
    return (os.getenv("SHOPIFY_ADMIN_TOKEN_PAPERANDPURPOSE") or "").strip() or None


def _sh(method: str, token: str, path: str, **kw) -> requests.Response:
    return requests.request(
        method,
        f"https://{_shop_host()}/admin/api/{API_VERSION}/{path.lstrip('/')}",
        headers={"X-Shopify-Access-Token": token, "Content-Type": "application/json"},
        timeout=TIMEOUT,
        **kw,
    )


def fetch_articles(token: str) -> List[Dict[str, Any]]:
    r = _sh("GET", token, f"blogs/{_blog_id()}/articles.json",
            params={"limit": 50, "published_status": "published",
                    "fields": "id,title,handle,author,published_at,body_html,summary_html,image,tags"})
    r.raise_for_status()
    return r.json().get("articles", [])


def article_campaign_id(token: str, article_id: int) -> Optional[str]:
    r = _sh("GET", token, f"articles/{article_id}/metafields.json",
            params={"namespace": METAFIELD_NS, "key": METAFIELD_KEY})
    if not r.ok:
        return None
    for m in r.json().get("metafields", []):
        if m.get("namespace") == METAFIELD_NS and m.get("key") == METAFIELD_KEY and m.get("value"):
            return str(m["value"])
    return None


def stamp_article(token: str, article_id: int, campaign_id: str) -> bool:
    r = _sh("POST", token, f"articles/{article_id}/metafields.json",
            json={"metafield": {"namespace": METAFIELD_NS, "key": METAFIELD_KEY,
                                "type": "single_line_text_field", "value": campaign_id}})
    if not r.ok:
        logger.warning("[pp-blog-email] metafield stamp failed HTTP %s: %s", r.status_code, r.text[:200])
    return r.ok


# ── pure helpers (unit-tested) ────────────────────────────────────────────

def published_today(articles: List[Dict[str, Any]], now: Optional[_dt.datetime] = None) -> List[Dict[str, Any]]:
    """Articles whose published_at falls on today's date in America/Chicago and
    is not in the future (scheduled-but-not-yet-live posts wait for the next run)."""
    now = now or _dt.datetime.now(CST)
    today = now.astimezone(CST).date()
    out = []
    for a in articles:
        raw = a.get("published_at")
        if not raw:
            continue
        try:
            when = _dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            continue
        when_cst = when.astimezone(CST)
        if when_cst.date() == today and when_cst <= now.astimezone(CST):
            out.append(a)
    return out


def send_time(published_at: str, now: Optional[_dt.datetime] = None) -> _dt.datetime:
    """UTC-aware send time: publish + 30 min, but never sooner than now + 10 min."""
    now = (now or _dt.datetime.now(_dt.timezone.utc)).astimezone(_dt.timezone.utc)
    pub = _dt.datetime.fromisoformat(published_at.replace("Z", "+00:00")).astimezone(_dt.timezone.utc)
    return max(pub + _dt.timedelta(minutes=30), now + _dt.timedelta(minutes=10))


_TAG = re.compile(r"<[^>]+>")


def clean_text(s: str) -> str:
    """Strip tags, unescape entities, kill em/en dashes (house rule), squash space."""
    s = _html.unescape(_TAG.sub(" ", s or ""))
    s = s.replace("—", ", ").replace("–", ", ").replace(" ,", ",").replace(",,", ",")
    s = re.sub(r"\s*,\s*,", ",", s)
    s = re.sub(r"[ \t\r\n]+", " ", s).strip()
    s = re.sub(r"\s+([.,;:!?])", r"\1", s)
    return s


def opening_paragraphs(body_html: str, title: str, max_paras: int = 3, max_chars: int = 700) -> List[str]:
    """First few real paragraphs of the post: skip the duplicated title, headings,
    empty lines, and scripture block quotes; stop at max_chars."""
    paras = re.findall(r"<p\b[^>]*>(.*?)</p>", body_html or "", flags=re.S | re.I)
    out: List[str] = []
    total = 0
    t_norm = clean_text(title).lower()
    for raw in paras:
        txt = clean_text(raw)
        if not txt or txt.lower() == t_norm or txt.startswith(("“", '"')):
            continue
        if len(txt) < 20:
            continue
        out.append(txt)
        total += len(txt)
        if len(out) >= max_paras or total >= max_chars:
            break
    return out


def build_email_html(article: Dict[str, Any], utm_campaign: str) -> str:
    title = clean_text(article.get("title", ""))
    url = f"{SITE}/blogs/news/{article['handle']}?utm_source=klaviyo&amp;utm_medium=email&amp;utm_campaign={utm_campaign}"
    prod = f"{SITE}{PRODUCT_PATH}?utm_source=klaviyo&amp;utm_medium=email&amp;utm_campaign={utm_campaign}"
    hero = (article.get("image") or {}).get("src") or ""
    paras = opening_paragraphs(article.get("body_html", ""), title)
    para_html = "".join(f'<p style="margin:0 0 20px;">{_html.escape(p, quote=False)}</p>' for p in paras)
    hero_html = (
        f'<tr><td align="center" style="padding:0 46px 6px;"><a href="{url}" target="_blank">'
        f'<img alt="{_html.escape(title)}" src="{_html.escape(hero)}" style="display:block;width:100%;max-width:508px;height:auto;border:0;border-radius:8px;" width="508"/></a></td></tr>'
        if hero else ""
    )
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"/><meta content="width=device-width, initial-scale=1.0" name="viewport"/><title>Paper &amp; Purpose</title></head>
<body style="margin:0;padding:0;background-color:#E8E0D3;">
<table cellpadding="0" cellspacing="0" role="presentation" style="background-color:#E8E0D3;padding:32px 12px;" width="100%"><tr><td align="center">
<table cellpadding="0" cellspacing="0" role="presentation" style="max-width:600px;width:100%;background-color:#FCFAF6;border-radius:10px;overflow:hidden;border:1px solid #E2D8C7;" width="600">
<tr><td align="center" style="padding:36px 40px 6px;"><img alt="Paper and Purpose" src="{LOGO_HEADER}" style="display:block;width:240px;max-width:66%;height:auto;margin:0 auto;border:0;" width="240"/></td></tr>
<tr><td align="center" style="padding:0 40px 22px;"><p style="margin:0;font-family:Georgia,'Times New Roman',serif;font-size:12px;letter-spacing:3px;text-transform:uppercase;color:#B89968;">From the journal</p><div style="width:44px;height:1px;background-color:#D8CBB4;margin:14px auto 0;"></div></td></tr>
<tr><td style="padding:12px 46px 8px;font-family:Georgia,'Times New Roman',serif;color:#4A5340;font-size:17px;line-height:1.75;">
<p style="margin:0 0 20px;">Hi friends,</p>
{para_html}
<p style="margin:0 0 8px;">I wrote the whole thing out in this week's post. You can read it here:</p>
</td></tr>
<tr><td align="center" style="padding:14px 46px 26px;"><table cellpadding="0" cellspacing="0" role="presentation" style="margin:0 auto;"><tr><td align="center" style="background-color:#B89968;border-radius:6px;"><a href="{url}" style="display:inline-block;padding:13px 34px;font-family:Georgia,'Times New Roman',serif;font-size:15px;letter-spacing:1px;color:#FCFAF6;text-decoration:none;" target="_blank">Read the full post</a></td></tr></table></td></tr>
{hero_html}
<tr><td style="padding:22px 46px 8px;font-family:Georgia,'Times New Roman',serif;color:#4A5340;font-size:17px;line-height:1.75;"><p style="margin:0;">Be blessed,<br/><span style="font-style:italic;">Miriam</span></p></td></tr>
<tr><td align="center" style="padding:8px 46px 34px;"><div style="width:44px;height:1px;background-color:#D8CBB4;margin:0 auto 22px;"></div><p style="margin:0 0 18px;font-family:Georgia,'Times New Roman',serif;font-size:15px;line-height:1.6;color:#6B7358;">The Be Transformed journal is in stock and shipping now. If you do not have yours yet, you can get it here.</p><table cellpadding="0" cellspacing="0" role="presentation" style="margin:0 auto;"><tr><td align="center" style="border:1px solid #B89968;border-radius:6px;"><a href="{prod}" style="display:inline-block;padding:12px 30px;font-family:Georgia,'Times New Roman',serif;font-size:14px;letter-spacing:1px;color:#4A5340;text-decoration:none;" target="_blank">Get the journal</a></td></tr></table></td></tr>
<tr><td align="center" style="padding:30px 40px 30px;background-color:#F2EDE4;border-top:1px solid #E2D8C7;font-family:Georgia,'Times New Roman',serif;font-size:12px;line-height:1.6;color:#9CA88E;"><img alt="Paper and Purpose" src="{LOGO_FOOTER}" style="display:block;width:92px;height:auto;margin:0 auto 16px;border:0;" width="92"/>Paper &amp; Purpose · 9901 Brodie Ln #160, Austin, TX 78748<br/>{{% unsubscribe %}}</td></tr>
</table></td></tr></table></body></html>"""


def subject_for(title: str) -> str:
    t = clean_text(title)
    return t if len(t) <= 78 else t[:75].rsplit(" ", 1)[0] + "..."


def preview_for(article: Dict[str, Any]) -> str:
    paras = opening_paragraphs(article.get("body_html", ""), article.get("title", ""), max_paras=1, max_chars=200)
    txt = paras[0] if paras else clean_text(article.get("title", ""))
    first = re.split(r"(?<=[.!?])\s", txt, 1)[0]
    return first[:140]


def utm_for(article: Dict[str, Any]) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", (article.get("handle") or "post").lower()).strip("_")[:40]
    return f"pp_blog_{slug}"


# ── klaviyo ───────────────────────────────────────────────────────────────

def _kl(method: str, path: str, body: Optional[Dict[str, Any]] = None) -> requests.Response:
    key = (os.getenv("KLAVIYO_API_KEY_PAPERANDPURPOSE") or "").strip()
    return requests.request(
        method, f"{KLAVIYO_BASE}/{path.lstrip('/')}",
        headers={"Authorization": f"Klaviyo-API-Key {key}", "revision": KLAVIYO_REVISION,
                 "Content-Type": "application/json", "accept": "application/json"},
        json=body, timeout=TIMEOUT,
    )


def create_and_schedule(article: Dict[str, Any], when_utc: _dt.datetime) -> Dict[str, Any]:
    """Template -> campaign -> assign -> send job. Returns ids and the preview URL.
    Raises RuntimeError with the Klaviyo body on any failure so nothing half-built
    is silently stamped as done."""
    utm = utm_for(article)
    html = build_email_html(article, utm)
    title = clean_text(article["title"])
    r = _kl("POST", "templates", {"data": {"type": "template", "attributes": {
        "name": f"PP Blog rail [art:{article['id']}] {title[:60]}", "editor_type": "CODE", "html": html}}})
    if not r.ok:
        raise RuntimeError(f"template: {r.status_code} {r.text[:300]}")
    tid = r.json()["data"]["id"]
    r = _kl("POST", "campaigns", {"data": {"type": "campaign", "attributes": {
        "name": f"PP Blog rail [art:{article['id']}] {title[:60]}",
        "audiences": {"included": [_list_id()], "excluded": []},
        "send_strategy": {"method": "static", "datetime": when_utc.strftime("%Y-%m-%dT%H:%M:%S+00:00"), "options": {"is_local": False}},
        "tracking_options": {"add_tracking_params": True, "custom_tracking_params": [
            {"type": "static", "value": "klaviyo", "name": "utm_source"},
            {"type": "static", "value": "email", "name": "utm_medium"},
            {"type": "static", "value": utm, "name": "utm_campaign"}],
            "is_tracking_clicks": True, "is_tracking_opens": True},
        "send_options": {"use_smart_sending": True},
        "campaign-messages": {"data": [{"type": "campaign-message", "attributes": {"definition": {
            "channel": "email", "label": "Blog email", "content": {
                "subject": subject_for(title), "preview_text": preview_for(article),
                "from_email": FROM_EMAIL, "from_label": FROM_LABEL, "reply_to_email": FROM_EMAIL}}}}]}}}})
    if not r.ok:
        raise RuntimeError(f"campaign: {r.status_code} {r.text[:300]}")
    cj = r.json()["data"]
    cid = cj["id"]
    mid = cj["relationships"]["campaign-messages"]["data"][0]["id"]
    r = _kl("POST", "campaign-message-assign-template", {"data": {"type": "campaign-message", "id": mid,
            "relationships": {"template": {"data": {"type": "template", "id": tid}}}}})
    if not r.ok:
        raise RuntimeError(f"assign: {r.status_code} {r.text[:300]}")
    r = _kl("POST", "campaign-send-jobs", {"data": {"type": "campaign-send-job", "id": cid}})
    if not r.ok:
        raise RuntimeError(f"send-job: {r.status_code} {r.text[:300]}")
    return {"campaign_id": cid, "template_id": tid, "message_id": mid,
            "preview_url": f"https://www.klaviyo.com/campaign/{cid}/content",
            "send_at_utc": when_utc.isoformat()}


# ── slack ─────────────────────────────────────────────────────────────────

def dispatch(message: str) -> Dict[str, str]:
    url = (os.getenv("PP_BLOG_EMAIL_DISPATCH_URL") or DISPATCH_URL_DEFAULT).strip()
    secret = (os.getenv("PIT_WALL_DISPATCH_SECRET") or "").strip()
    channel = (os.getenv("PP_BLOG_EMAIL_DISPATCH_CHANNEL") or "client-marketing-garage").strip()
    if not secret:
        return {"status": "skipped", "reason": "PIT_WALL_DISPATCH_SECRET missing"}
    try:
        r = requests.post(url, headers={"Authorization": f"Bearer {secret}", "Content-Type": "application/json"},
                          json={"channel": channel, "message": message, "posted_by": "P&P blog email rail"}, timeout=10)
        return {"status": "posted" if r.ok else "failed", "http": str(r.status_code)}
    except Exception as e:  # noqa: BLE001
        return {"status": "failed", "error": f"{type(e).__name__}: {e}"}


# ── entry point ───────────────────────────────────────────────────────────

def run_hourly(now: Optional[_dt.datetime] = None) -> Dict[str, Any]:
    """APScheduler entry. Always returns a summary; never raises."""
    summary: Dict[str, Any] = {"enabled": enabled(), "checked": 0, "scheduled": [], "skipped": [], "errors": []}
    if not enabled():
        return summary
    try:
        token = shopify_token()
        if not token:
            summary["errors"].append("no shopify token")
            return summary
        todays = published_today(fetch_articles(token), now)
        summary["checked"] = len(todays)
        for a in todays:
            existing = article_campaign_id(token, a["id"])
            if existing:
                summary["skipped"].append({"article": a["id"], "campaign": existing})
                continue
            when = send_time(a["published_at"], now)
            try:
                res = create_and_schedule(a, when)
            except RuntimeError as e:
                summary["errors"].append({"article": a["id"], "error": str(e)})
                dispatch(f":x: P&P blog email rail could not build the email for *{clean_text(a['title'])}*: {e}")
                continue
            stamped = stamp_article(token, a["id"], res["campaign_id"])
            res["stamped"] = stamped
            summary["scheduled"].append({"article": a["id"], **res})
            local = when.astimezone(CST).strftime("%-I:%M %p CT")
            dispatch(f":email: P&P blog email scheduled for *{local}* today: *{clean_text(a['title'])}*\n"
                     f"Audience: opted-in list ({_list_id()}). Preview / cancel: {res['preview_url']}\n"
                     f"Post: {SITE}/blogs/news/{a['handle']}")
    except Exception as e:  # noqa: BLE001
        logger.exception("[pp-blog-email] run failed")
        summary["errors"].append(f"{type(e).__name__}: {e}")
    try:
        from services.watchdog import record_heartbeat
        record_heartbeat("pp_blog_email_rail")
    except Exception:  # noqa: BLE001
        pass
    logger.info("[pp-blog-email] %s", summary)
    return summary
