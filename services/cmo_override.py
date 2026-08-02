"""services/cmo_override.py -- turn a reply to the CMO Daily into a CMO override.

The CMO Daily email tells Michael he is inspecting the CMO, not approving assets,
and that a reply overrides the gate. But the email had no Reply-To and no inbound
handler, so any reply black-holed at the Resend sending subdomain. This wires the
missing leg.

How it works
------------
The brief is sent with Reply-To = the CMO override inbox (default the connected
`avi` Gmail, michael@automotiveintelligence.io). This poller sweeps that inbox for
replies to a "CMO Daily" thread, and for each NEW inbound reply from Michael it
files a `\U0001F3C1 FLAG FOR: CMO` override block into avo-telemetry/cmo_state.md --
the CMO's own owned file, which the CMO reads at the start of every session (the
live owned-file markdown protocol; Slack routing was retired 2026-07-20). The
flag_router webhook also parses it on push.

Mirrors services/wd_crm_push_responder (same inbox tooling, same de-quote logic,
same graceful degradation) so it fits the codebase's existing reply-trigger shape.

Idempotency (belt + suspenders):
  * a hidden `<!-- cmo-override:msgid=<id> -->` marker in cmo_state.md -- the
    authoritative dedupe (update_state re-reads latest each pass and skips if the
    marker is already present).
  * a Gmail thread label `CMO/override-filed`, applied best-effort after filing.

Fail CLOSED: if the inbox is unreachable (every Postal Gmail token was revoked /
needs_reauth as of 2026-07-01), the poller logs, returns status
"inbox_unavailable", and writes NOTHING. It self-activates the moment the inbox is
re-authed -- it never invents an override.

Run from the scheduler (_run_cmo_override_poll in app.py) or by hand:
    python -m services.cmo_override            # live
    python -m services.cmo_override --dry-run  # detect only, no flag, no label
"""

from __future__ import annotations

import datetime as _dt
import logging
import os
import re
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

CMO_STATE_PATH = "cmo_state.md"
FLAGS_SECTION = "## Flags for other chats"
FILED_LABEL = "CMO/override-filed"

# The CMO Daily is sent from the Resend sending subdomain. Anything from this
# marker is OUR own send in the thread, never Michael's reply.
_DEFAULT_SENDER_MARKER = "mail.automotiveintelligence.io"


def _override_inbox() -> str:
    """Postal account label whose Gmail receives the Reply-To. Default 'avi'
    (michael@automotiveintelligence.io) -- the same brand family as the sender."""
    return (os.environ.get("CMO_OVERRIDE_INBOX") or "avi").strip()


def _sender_markers() -> List[str]:
    """Substrings that identify OUR outbound copy (to skip). Derived from the
    configured From plus the known Resend subdomain."""
    markers = {_DEFAULT_SENDER_MARKER}
    frm = (os.environ.get("CMO_DAILY_FROM") or "").lower()
    m = re.search(r"[\w.+-]+@([\w.-]+)", frm)
    if m:
        markers.add(m.group(1))
    return [x for x in markers if x]


def _search_query(lookback_days: int) -> str:
    return f'subject:"CMO Daily" newer_than:{lookback_days}d in:inbox'


# ---------- parsing (mirrors wd_crm_push_responder / ape_reply_parser) ----------

def _email_addr(from_header: str) -> str:
    if not from_header:
        return ""
    m = re.search(r"<([^>]+)>", from_header)
    addr = (m.group(1) if m else from_header).strip().lower()
    m2 = re.search(r"[\w.+-]+@[\w.-]+", addr)
    return m2.group(0) if m2 else addr


def _strip_quoted(body: str) -> str:
    """Drop the quoted prior message + signature so we only read what Michael
    actually typed."""
    if not body:
        return ""
    cleaned = re.split(r"\n\s*On\s+.+wrote:", body, maxsplit=1, flags=re.IGNORECASE)[0]
    cleaned = re.split(r"\n-{2,}\s*\n", cleaned, maxsplit=1)[0]
    cleaned = re.split(r"\n_{5,}", cleaned, maxsplit=1)[0]  # Outlook divider
    cleaned = "\n".join(
        line for line in cleaned.splitlines() if not line.lstrip().startswith(">")
    )
    return cleaned.strip()


def _is_inbound(from_header: str) -> bool:
    low = (from_header or "").lower()
    return not any(marker in low for marker in _sender_markers())


def _latest_inbound(messages: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Most-recent inbound message with non-empty de-quoted body (Michael's
    reply). Never matches our own sent brief."""
    for msg in reversed(messages or []):  # newest last in a Gmail thread
        if not _is_inbound(msg.get("from", "")):
            continue
        typed = _strip_quoted(msg.get("body", ""))
        if typed:
            return {**msg, "typed": typed}
    return None


# ---------- flag construction ----------

def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def _msg_marker(msg_id: str) -> str:
    return f"<!-- cmo-override:msgid={msg_id} -->"


def build_override_block(msg: Dict[str, Any], now_iso: Optional[str] = None) -> str:
    """Render a CMO OVERRIDE as a `\U0001F3C1 FLAG FOR: CMO` block. Michael's verbatim
    reply is blockquoted; a hidden msgid marker makes the write idempotent."""
    now_iso = now_iso or _now_iso()
    sender = _email_addr(msg.get("from", "")) or "michael"
    subject = (msg.get("subject") or "").strip()
    date = (msg.get("date") or "").strip()
    verbatim = (msg.get("typed") or "").strip()
    quoted = "\n".join(f"> {ln}" if ln else ">" for ln in verbatim.splitlines()) or "> (empty)"
    why = f'Reply to "{subject}"' + (f" ({date})" if date else "") + f"; from {sender}."
    return (
        "\U0001F3C1 FLAG FOR: CMO\n"
        "**What:** CMO OVERRIDE from Michael's email reply to the CMO Daily -- verbatim below. "
        "Apply it (hold/pull/change the named asset) and close this flag.\n"
        f"**Why now:** {why}\n"
        "**By when:** Act this session.\n"
        "**Posted by:** CMO Daily reply handler\n"
        f"**Posted:** {now_iso}\n\n"
        f"{quoted}\n\n"
        f"{_msg_marker(msg.get('id', ''))}\n"
    )


def _make_transform(block: str, msg_id: str) -> Callable[[str], Optional[str]]:
    """Return a transform(content)->new|None for avo_state_commit.update_state.
    None = already filed (idempotent skip). Inserts the block at the top of the
    live flags section (creating it if missing)."""
    marker = _msg_marker(msg_id)

    def _transform(content: str) -> Optional[str]:
        if marker and marker in (content or ""):
            return None  # already filed -> idempotent skip
        text = content or ""
        idx = text.find(FLAGS_SECTION)
        if idx == -1:
            # No flags section yet -- append one at the end.
            sep = "" if text.endswith("\n") else "\n"
            return f"{text}{sep}\n{FLAGS_SECTION}\n\n{block}"
        # Insert right after the section header line so newest override is on top.
        line_end = text.find("\n", idx)
        if line_end == -1:
            return f"{text}\n\n{block}"
        head, tail = text[: line_end + 1], text[line_end + 1 :]
        return f"{head}\n{block}\n{tail.lstrip(chr(10))}"

    return _transform


# ---------- main pass ----------

def run(dry_run: bool = False, account: Optional[str] = None,
        lookback_days: int = 3, limit: int = 25) -> Dict[str, Any]:
    """One sweep of the CMO override inbox. Returns a summary dict. Fail-closed:
    an unreachable inbox yields status 'inbox_unavailable' and writes nothing."""
    account = account or _override_inbox()
    summary: Dict[str, Any] = {
        "status": "ok", "inbox": account, "scanned": 0, "matched": 0,
        "filed": 0, "already_filed": 0, "dry_run": dry_run, "details": [],
    }

    from services import postal_inbox

    try:
        threads = postal_inbox.search(account, _search_query(lookback_days), limit=limit)
    except Exception as e:  # noqa: BLE001 - inbox down / needs_reauth -> fail closed
        logger.warning("[cmo-override] inbox '%s' unavailable: %s", account, e)
        summary["status"] = "inbox_unavailable"
        summary["error"] = str(e)[:200]
        return summary

    summary["scanned"] = len(threads)
    if not threads:
        return summary

    token = (os.environ.get("GITHUB_TOKEN_TELEMETRY") or "").strip()
    if not token and not dry_run:
        summary["status"] = "no_telemetry_token"
        summary["error"] = "GITHUB_TOKEN_TELEMETRY not set -- cannot write cmo_state.md"
        return summary

    # Best-effort idempotency label (needs Gmail modify scope; skipped if it fails).
    filed_label_id: Optional[str] = None
    if not dry_run:
        try:
            from tools import gmail_multi
            filed_label_id = gmail_multi.ensure_label(account, FILED_LABEL)
        except Exception as e:  # noqa: BLE001 - label is optional; marker is authoritative
            logger.info("[cmo-override] label unavailable (%s); relying on file marker", e)

    from services.avo_state_commit import update_state

    for t in threads:
        thread_id = t.get("id")
        if not thread_id:
            continue
        try:
            thread = postal_inbox.read_thread(account, thread_id)
        except Exception as e:  # noqa: BLE001
            logger.warning("[cmo-override] read_thread %s failed: %s", thread_id, e)
            continue
        messages = thread.get("messages") or []

        if filed_label_id and messages and filed_label_id in (messages[0].get("label_ids") or []):
            summary["already_filed"] += 1
            continue

        hit = _latest_inbound(messages)
        if not hit:
            continue
        msg_id = hit.get("id") or ""
        summary["matched"] += 1
        detail = {"thread_id": thread_id, "from": _email_addr(hit.get("from", "")),
                  "subject": hit.get("subject", ""), "msg_id": msg_id}

        if dry_run:
            detail["mode"] = "dry_run"
            detail["preview"] = (hit.get("typed") or "")[:160]
            summary["details"].append(detail)
            continue

        block = build_override_block(hit)
        try:
            result = update_state(
                CMO_STATE_PATH,
                _make_transform(block, msg_id),
                message=f"cmo-override: file Michael's reply as CMO OVERRIDE ({msg_id[:10]})",
                token=token,
            )
        except Exception as e:  # noqa: BLE001 - one bad write must not kill the sweep
            logger.error("[cmo-override] flag write failed for %s: %s", thread_id, e)
            detail["mode"] = "error"
            detail["error"] = str(e)[:160]
            summary["details"].append(detail)
            continue

        if result.get("committed"):
            summary["filed"] += 1
            detail["mode"] = "filed"
            logger.info("[cmo-override] filed CMO OVERRIDE from %s (thread %s)",
                        detail["from"], thread_id)
            if filed_label_id:
                try:
                    from tools import gmail_multi
                    gmail_multi.add_label(account, thread_id, filed_label_id)
                except Exception as e:  # noqa: BLE001
                    logger.info("[cmo-override] label apply failed (non-fatal): %s", e)
        else:
            summary["already_filed"] += 1
            detail["mode"] = "already_filed"
        summary["details"].append(detail)

    return summary


if __name__ == "__main__":
    import json
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    print(json.dumps(run(dry_run="--dry-run" in sys.argv), indent=2))
