

# ---------------- Zernio inbox transport (deliverable 152, transport seam) ----------------
# Zernio account_id -> brand. Unmapped account NEVER auto-replies (same rule as Chatwoot map).
ZERNIO_ACCOUNT_BRANDS = json.loads(os.getenv("CONCIERGE_ZERNIO_ACCOUNT_BRANDS", json.dumps({
    "69c8aef66cb7b8cf4cabaf67": "avi",   # AvI facebook
    "69c8af386cb7b8cf4cabb001": "avi",   # AvI instagram
    "6a43fd0a9d9472faae32a6e6": "aipg",  # AIPG facebook
    "6a43fca79d9472faae32a2a0": "aipg",  # AIPG instagram
    "69c8ac0a6cb7b8cf4caba586": "wd",    # WD (Calling Digital) facebook
    "69c8ac356cb7b8cf4caba743": "wd",    # WD (Calling Digital) instagram
})))

def _zernio_send(conversation_id: str, text: str) -> int:
    from tools.zernio import _zernio_request
    r = _zernio_request("POST", f"/inbox/conversations/{conversation_id}/messages", {"text": text})
    return 200 if r else 500

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
        except Exception:
            logging.exception("[concierge-zernio] claude failed; fulfillment fallback")
            reply, hot = fulfill, True
    else:
        hot = True  # unmapped account, no keyword, or off-brand keyword -> human only
    receipt = {"transport": "zernio", "mode": "LIVE" if LIVE else "SHADOW",
               "conv": conv_id, "acct": acct, "brand": brand,
               "platform": msg.get("platform"), "kw": kw, "hot": hot,
               "in": text[:120], "reply": (reply or "")[:200]}
    logging.info("[concierge] %s", json.dumps(receipt))
    if LIVE and reply and conv_id:
        _zernio_send(conv_id, reply)
    return receipt
