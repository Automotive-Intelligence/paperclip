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
ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY", "")
MODEL = os.getenv("CONCIERGE_MODEL", "claude-sonnet-5")

# keyword registry: keyword -> (brand, fulfillment line). ONLY wired paths. (152 registry)
REGISTRY = {
    "EQUITY":     ("avi",  "Here is the short version of how mined equity works, and the free 30-minute diagnostic where we pull it from your own data: automotiveintelligence.io/diagnostic-call"),
    "DIAGNOSTIC": ("avi",  "Book the free 30-minute diagnostic here: automotiveintelligence.io/diagnostic-call"),
    "PLAYBOOK":   ("aipg", "Here is the free Missed Call Playbook: theaiphoneguy.com/discovery"),
    "DEMO":       ("aipg", "Grab a 15-minute demo slot here: theaiphoneguy.com (booking link in the top nav)."),
    "SAMPLE":     ("wd",   "We will build you a free sample first, no invoice attached. Start here: worshipdigital.co"),
    "BUILD":      ("bae",  "The community is on Skool: skool.com/agent-empire-4291"),
}
VOICE = {
    "avi":  "Plain-spoken dealership operator. I sell cars for a living and build AI for stores like mine. Never discuss pricing; offer the free diagnostic.",
    "aipg": "The AI Phone Guy. DFW trades. Never say chatbot; we catch calls your team can not get to. Never discuss pricing.",
    "wd":   "Worship Digital, founder-run full-service agency. Plain English, no jargon. Lead with the free sample.",
    "bae":  "Agent Empire. Anti-guru, operator-to-operator. No income claims ever.",
}
HOT = re.compile(r"price|cost|how much|call me|talk|meeting|ready|sign", re.I)

def _claude(system, user):
    r = requests.post("https://api.anthropic.com/v1/messages", timeout=30, headers={
        "x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
        json={"model": MODEL, "max_tokens": 300, "system": system,
              "messages": [{"role": "user", "content": user}]})
    r.raise_for_status()
    return r.json()["content"][0]["text"]

def _send(account_id, conversation_id, text):
    r = requests.post(f"{CHATWOOT_URL}/api/v1/accounts/{account_id}/conversations/{conversation_id}/messages",
        timeout=15, headers={"api_access_token": BOT_TOKEN}, json={"content": text})
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

    kw = next((k for k in REGISTRY if re.search(rf"\b{k}\b", text, re.I)), None)
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
    else:
        reply, hot = None, True          # unknown intent -> human

    receipt = {"mode": "LIVE" if LIVE else "SHADOW", "conv": conv_id, "acct": account_id,
               "inbox": inbox, "kw": kw, "brand": brand, "hot": hot,
               "in": text[:120], "reply": (reply or "")[:200]}
    logging.info("[concierge] %s", json.dumps(receipt))
    if LIVE and account_id and conv_id:
        if reply: _send(account_id, conv_id, reply)
        if hot: _handoff(account_id, conv_id)
    return receipt
