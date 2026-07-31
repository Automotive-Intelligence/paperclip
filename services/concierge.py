"""The Concierge — AI-native DM/chat responder (deliverable 152).

Chatwoot Agent Bot webhook -> this brain -> reply via Chatwoot API.
SHADOW MODE by default: logs + drafts, sends nothing, until CONCIERGE_LIVE=1.
Brain rules: per-brand voice, wired-CTA registry ONLY, no pricing, no em-dashes,
hot-thread handoff (conversation opened to human inbox) on qualification signal.
"""
import os, json, re, logging
import requests

CHATWOOT_URL = os.getenv("CHATWOOT_URL", "https://portal.worshipdigital.co").rstrip("/")
BOT_TOKEN = os.getenv("CHATWOOT_BOT_TOKEN", "")
LIVE = os.getenv("CONCIERGE_LIVE", "0") == "1"
# inbox->brand map: {"1": "*"} = test inbox, all brands; {"5": "avi"} = AvI-only.
# An inbox NOT in this map NEVER auto-replies (human handoff only) — safe default.
INBOX_BRANDS = json.loads(os.getenv("CONCIERGE_INBOX_BRANDS") or '{"1": "*"}')
ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY", "")
MODEL = os.getenv("CONCIERGE_MODEL", "claude-sonnet-5")

# keyword registry: keyword -> (brand, fulfillment line). ONLY wired paths. (152 registry)
REGISTRY = {
    "EQUITY":     ("avi",  "Here is the short version of how mined equity works, and the free 30-minute diagnostic where we pull it from your own data: automotiveintelligence.io/diagnostic-call?utm_source=concierge&utm_medium=dm&utm_campaign=equity"),
    "DIAGNOSTIC": ("avi",  "Book the free 30-minute diagnostic here: automotiveintelligence.io/diagnostic-call?utm_source=concierge&utm_medium=dm&utm_campaign=diagnostic"),
    "PLAYBOOK":   ("aipg", "Here is the free Missed Call Playbook: theaiphoneguy.com/discovery?utm_source=concierge&utm_medium=dm&utm_campaign=playbook"),
    "DEMO":       ("aipg", "Grab a 15-minute demo slot here: theaiphoneguy.com (booking link in the top nav)."),
    "SAMPLE":     ("wd",   "We will build you a free sample first, no invoice attached. Start here: worshipdigital.co?utm_source=concierge&utm_medium=dm&utm_campaign=sample"),
    "BUILD":      ("bae",  "The community is on Skool: skool.com/agent-empire-4291"),
    "DEMO2":      ("bookd", "See it working at bookd.cx (demo link in the top nav)."),
}
VOICE = {
    "avi":  "Plain-spoken dealership operator. I sell cars for a living and build AI for stores like mine. Never discuss pricing; offer the free diagnostic.",
    "aipg": "The AI Phone Guy. DFW trades. Never say chatbot; we catch calls your team can not get to. Never discuss pricing.",
    "wd":   "Worship Digital, founder-run full-service agency. Plain English, no jargon. Lead with the free sample.",
    "bae":  "Agent Empire. Anti-guru, operator-to-operator. No income claims ever.",
    "bookd": "book'd, the compliance-first CRM for life insurance agents. Ryan-default voice: plain, direct, agent-to-agent. NEVER discuss pricing, NEVER income/earnings claims (insurance compliance). Point to the demo at bookd.cx.",
}
# Brands held in shadow even when CONCIERGE_LIVE=1 (e.g. partner brands pending standing sign-off)
BRAND_HOLD = set(json.loads(os.getenv("CONCIERGE_BRAND_HOLD") or '["bookd"]'))
# Any HOT match forces a human handoff AND suppresses the auto-reply. Two groups:
#   pricing / qualification signals (existing), and
#   legal / medical / financial RISK terms (word-boundaried on the left so common
#   inflections still match, e.g. refund/refunded, invest/investment, sue/sued).
# Erring toward extra handoffs is the safe direction; a false handoff is fine.
HOT = re.compile(
    r"price|cost|how much|call me|talk|meeting|ready|sign"
    r"|\b(?:legal|lawyer|attorney|lawsuit|sue|liable|liability"
    r"|medical|doctor|health|diagnosis|refund|chargeback|dispute"
    r"|financial|invest|guarantee)",
    re.I,
)
# Post-generation scrub: never emit a $ amount, a percentage, or a numeric
# "<n> percent/dollars" claim, even if the LLM prompt is bypassed. Benign counts
# like "30-minute" are intentionally NOT matched. When a reply trips this, we
# suppress it and hand off to a human (a wrong number to a customer is never ok).
NUMERIC = re.compile(r"\$\s?\d|\d+\s?%|\b\d+\s?(?:percent|dollars)\b", re.I)

def _claude(system, user):
    r = requests.post("https://api.anthropic.com/v1/messages", timeout=30, headers={
        "x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
        json={"model": MODEL, "max_tokens": 1024, "system": system,
              "messages": [{"role": "user", "content": user}]})
    r.raise_for_status()
    blocks = r.json().get("content") or []
    texts = [b.get("text", "") for b in blocks if b.get("type") == "text"]
    if not texts:
        raise ValueError(f"no text block in response (types: {[b.get('type') for b in blocks]})")
    return "\n".join(texts).strip()

def _send(account_id, conversation_id, text):
    r = requests.post(f"{CHATWOOT_URL}/api/v1/accounts/{account_id}/conversations/{conversation_id}/messages",
        timeout=15, headers={"api_access_token": BOT_TOKEN}, json={"content": text})
    if r.status_code >= 300:
        logging.error("[concierge] chatwoot send FAILED %s: %s", r.status_code, r.text[:200])
    return r.status_code

def _handoff(account_id, conversation_id):
    requests.post(f"{CHATWOOT_URL}/api/v1/accounts/{account_id}/conversations/{conversation_id}/toggle_status",
        timeout=15, headers={"api_access_token": BOT_TOKEN}, json={"status": "open"})

def handle_webhook(payload: dict) -> dict:
    """Chatwoot agent_bot webhook. Returns a receipt dict (also the shadow log line)."""
    if payload.get("message_type") != "incoming" or payload.get("event") != "message_created":
        return {"skip": "not an incoming message"}
    text = (payload.get("content") or "").strip()
    conv = payload.get("conversation") or {}
    account_id = (payload.get("account") or {}).get("id") or conv.get("account_id")
    conv_id = conv.get("display_id") or conv.get("id")
    inbox = (conv.get("meta") or {}).get("channel") or ""

    inbox_id = str(conv.get("inbox_id") or payload.get("inbox", {}).get("id") or "")
    inbox_brand = INBOX_BRANDS.get(inbox_id)
    kw = next((k for k in REGISTRY if re.search(rf"\b{k}\b", text, re.I)
               and (inbox_brand == "*" or REGISTRY[k][0] == inbox_brand)), None)
    brand, fulfill = REGISTRY.get(kw, (None, None)) if kw else (None, None)
    hot = bool(HOT.search(text))

    if brand:
        system = (f"You are the DM concierge. Voice: {VOICE[brand]} Reply in under 60 words, "
                  f"warm and human, NO em-dashes, no pricing, no promises beyond: {fulfill} "
                  f"End with exactly that link/line. One question max to qualify them.")
        try:
            reply = _claude(system, f'They wrote: "{text}"')
        except Exception:
            logging.exception("[concierge] claude call failed; using fulfillment fallback")
            reply, hot = fulfill, True   # fulfillment never fails; flag human
        if "—" in reply or "–" in reply: reply = reply.replace("—", ",").replace("–", ",")
        if reply and NUMERIC.search(reply):
            hot = True                   # generated a $/%/number -> suppress + human
    else:
        reply, hot = None, True          # unknown intent -> human

    suppressed = bool(hot and reply)     # drafted but withheld (logged, not sent)
    receipt = {"mode": "LIVE" if LIVE else "SHADOW", "conv": conv_id, "acct": account_id,
               "inbox": inbox_id or inbox, "inbox_brand": inbox_brand, "kw": kw, "brand": brand,
               "hot": hot, "suppressed": suppressed,
               "in": text[:120], "reply": (reply or "")[:200]}
    logging.info("[concierge] %s", json.dumps(receipt))
    if LIVE and account_id and conv_id and (brand not in BRAND_HOLD):
        if reply and not hot: _send(account_id, conv_id, reply)   # HOT -> suppress auto-reply
        if hot: _handoff(account_id, conv_id)
    return receipt


# ---------------- Zernio inbox transport (deliverable 152, transport seam) ----------------
# Zernio account_id -> brand. Unmapped account NEVER auto-replies (same rule as Chatwoot map).
ZERNIO_ACCOUNT_BRANDS = json.loads(os.getenv("CONCIERGE_ZERNIO_ACCOUNT_BRANDS") or json.dumps({
    "69c8aef66cb7b8cf4cabaf67": "avi",   # AvI facebook
    "69c8af386cb7b8cf4cabb001": "avi",   # AvI instagram
    "6a43fd0a9d9472faae32a6e6": "aipg",  # AIPG facebook
    "6a43fca79d9472faae32a2a0": "aipg",  # AIPG instagram
    "69c8ac0a6cb7b8cf4caba586": "wd",    # WD (Calling Digital) facebook
    "69c8ac356cb7b8cf4caba743": "wd",    # WD (Calling Digital) instagram
}))

def _zernio_send(conversation_id: str, text: str, account_id: str) -> int:
    from tools.zernio import _zernio_request
    try:
        r = _zernio_request("POST", f"/inbox/conversations/{conversation_id}/messages",
                            {"message": text, "accountId": account_id})
        logging.info("[concierge] zernio send OK conv=%s", conversation_id)
        return 200 if r else 500
    except Exception:
        logging.exception("[concierge] ZERNIO SEND FAILED conv=%s acct=%s", conversation_id, account_id)
        return 500

def handle_zernio(payload: dict) -> dict:
    if payload.get("event") != "message.received":
        return {"skip": "not message.received"}
    msg = payload.get("message") or {}
    if msg.get("direction") != "incoming":
        return {"skip": "not incoming"}
    text = (msg.get("text") or "").strip()
    conv_id = msg.get("conversationId")
    acct = str(msg.get("accountId") or (payload.get("account") or {}).get("id")
               or msg.get("account_id") or "")
    brand = ZERNIO_ACCOUNT_BRANDS.get(acct)
    hot = bool(HOT.search(text))
    kw = next((k for k in REGISTRY if re.search(rf"\b{k}\b", text, re.I)
               and brand and REGISTRY[k][0] == brand), None)
    reply = None
    if kw:
        _, fulfill = REGISTRY[kw]
        system = (f"You are the DM concierge. Voice: {VOICE[brand]} Reply in under 60 words, "
                  f"warm and human, NO em-dashes, no pricing, no promises beyond: {fulfill} "
                  f"End with exactly that link/line. One question max to qualify them.")
        try:
            reply = _claude(system, f'They wrote: "{text}"')
            if "—" in reply or "–" in reply:
                reply = reply.replace("—", ",").replace("–", ",")
            if reply and NUMERIC.search(reply):
                hot = True  # generated a $/%/number -> suppress (human handles it)
        except Exception:
            logging.exception("[concierge-zernio] claude failed; fulfillment fallback")
            reply, hot = fulfill, True
    else:
        hot = True  # unmapped account, no keyword, or off-brand keyword -> human only
    suppressed = bool(hot and reply)  # drafted but withheld (Zernio handoff = no auto-reply)
    receipt = {"transport": "zernio", "mode": "LIVE" if LIVE else "SHADOW",
               "conv": conv_id, "acct": acct, "brand": brand,
               "platform": msg.get("platform"), "kw": kw, "hot": hot, "suppressed": suppressed,
               "in": text[:120], "reply": (reply or "")[:200]}
    logging.info("[concierge] %s", json.dumps(receipt))
    if LIVE and reply and not hot and conv_id and (brand not in BRAND_HOLD):
        _zernio_send(conv_id, reply, acct)
    return receipt
