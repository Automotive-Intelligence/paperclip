"""services/bookd_mcp.py -- MCP front door for the Book'd partner-agent port.

A minimal, STATELESS Model Context Protocol server over Streamable HTTP: one POST
route (wired in app.py at /bookd/mcp) speaking JSON-RPC 2.0 and answering with plain
JSON (the spec's stateless mode -- no SSE stream, no session ids). Hand-rolled on
purpose: it is ~100 lines, adds zero dependencies, and avoids mounting a second ASGI
app + lifespan into FastAPI. Any MCP-capable harness (Ryan's Hermes included) mounts it
with a URL + bearer key; anything else uses the plain REST endpoints, which share the
exact same core (services/bookd_agent + services/bookd_handoff).

Methods: initialize, ping, tools/list, tools/call; notifications/* are acknowledged
with no body. Auth happens in app.py (store-backed partner keys, never master API_KEYS);
the resolved grant decides which tools exist and which are callable.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_PROTOCOL_DEFAULT = "2025-06-18"

TOOLS = [
    {
        "name": "bookd_message",
        "description": (
            "Send a message to AVO's Book'd ops agent and get the answer. Book'd-scoped "
            "and answer-only: anything needing an action or Michael's decision is "
            "flagged to Michael automatically. Pass conversation_id from a previous "
            "result to continue that conversation."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "Your message."},
                "conversation_id": {"type": "string",
                                    "description": "Optional: continue a conversation."},
            },
            "required": ["message"],
        },
    },
    {
        "name": "bookd_status",
        "description": "Current Book'd workstream status (live state file, read-only).",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "bookd_handoff_secret",
        "description": (
            "Hand off a credential (API key, webhook secret) securely. The value is "
            "encrypted at rest immediately, never emailed or logged, and Michael "
            "approves the install. Use this instead of pasting secrets into chat."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "key_name": {"type": "string",
                             "description": "Env-var style name, e.g. STRIPE_WEBHOOK_SECRET."},
                "value": {"type": "string", "description": "The secret value."},
            },
            "required": ["key_name", "value"],
        },
    },
]


# Tools an 'avo'-scope key gets ON TOP of the three above. The corpus is far too large
# to inject, so reading the operation means searching and reading it on demand.
AVO_TOOLS = [
    {
        "name": "avo_search",
        "description": (
            "Search AVO's operating state across every brand and seat. Returns matching "
            "lines with their file and line number. Start here when you do not know "
            "which file holds the answer."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Text or regex to find."},
            },
            "required": ["query"],
        },
    },
    {
        "name": "avo_read",
        "description": (
            "Read one AVO state file, or one section of it by heading. Large files are "
            "returned in capped chunks; pass offset to continue."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File name, e.g. revenue_state.md."},
                "section": {"type": "string", "description": "Optional heading to extract."},
                "offset": {"type": "integer", "description": "Character offset for paging."},
            },
            "required": ["path"],
        },
    },
    {
        "name": "request_action",
        "description": (
            "Request that AVO take a real action. Anything with no external effect is "
            "recorded and proceeds. Anything with blast radius (spend, sends, deploys, "
            "secrets, client surfaces, deletions) is staged for Michael's approval and "
            "he is paged. Use check_action to poll the verdict."),
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "What you want done, in one clear sentence."},
                "params": {"type": "object", "description": "Any structured detail."},
            },
            "required": ["action"],
        },
    },
    {
        "name": "check_action",
        "description": "Check the status and verdict of an action request you submitted.",
        "inputSchema": {
            "type": "object",
            "properties": {"id": {"type": "integer", "description": "The request id."}},
            "required": ["id"],
        },
    },
]


def tools_for(scope: str) -> list:
    """The tool list a key's scope unlocks. Scope comes from the key, never the request."""
    return TOOLS + AVO_TOOLS if scope == "avo" else TOOLS


def _result(rpc_id: Any, result: Dict[str, Any]) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": rpc_id, "result": result}


def _error(rpc_id: Any, code: int, message: str) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": code, "message": message}}


def _tool_text(payload: Dict[str, Any], *, is_error: bool = False) -> Dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(payload, default=str)}],
            "isError": is_error}


def _call_tool(name: str, args: Dict[str, Any], grant: Dict[str, Any]) -> Dict[str, Any]:
    from services.bookd_agent import handle_message, status
    from services.bookd_handoff import stage
    scope = grant.get("scope", "bookd")
    key_id = grant.get("id")
    label = grant.get("label") or "partner"

    if name == "bookd_message":
        out = handle_message(args.get("conversation_id"), str(args.get("message") or ""),
                             source="hermes-mcp", scope=scope, key_id=key_id)
        return _tool_text(out, is_error=out.get("disposition") in ("error", "invalid"))
    if name == "bookd_status":
        return _tool_text(status(scope))
    if name == "bookd_handoff_secret":
        out = stage(str(args.get("key_name") or ""), str(args.get("value") or ""),
                    submitted_by=f"{label}/mcp")
        return _tool_text(out, is_error=not out.get("ok"))

    # Scope gate: an 'avo' tool is invisible AND unusable to a 'bookd' key, even if the
    # caller names it directly (a tool list is a hint, never the enforcement point).
    if name in {t["name"] for t in AVO_TOOLS}:
        if scope != "avo":
            return _tool_text({"error": f"{name} requires 'avo' scope; this key is "
                                        f"scoped to {scope!r}"}, is_error=True)
        if name == "avo_search":
            from services.avo_state import search
            return _tool_text(search(str(args.get("query") or ""), scope))
        if name == "avo_read":
            from services.avo_state import read
            return _tool_text(read(str(args.get("path") or ""), scope,
                                   section=(args.get("section") or None),
                                   offset=int(args.get("offset") or 0)))
        if name == "request_action":
            if not grant.get("can_act"):
                return _tool_text({"error": "this key's action channel is turned off; "
                                            "ask Michael to enable it"}, is_error=True)
            from services.partner_actions import request_action
            return _tool_text(request_action(
                str(args.get("action") or ""), args.get("params") or {},
                key_id=key_id, requested_by=label))
        if name == "check_action":
            from services.partner_actions import status_for
            return _tool_text(status_for(int(args.get("id") or 0)))
    return _tool_text({"error": f"unknown tool {name!r}"}, is_error=True)


def handle_rpc(payload: Dict[str, Any],
               grant: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """One JSON-RPC message -> response dict, or None for notifications (HTTP 202).
    `grant` is the resolved key (scope, can_act) from app.py; it decides which tools
    exist and which are callable."""
    grant = grant or {"scope": "bookd", "can_act": False}
    if not isinstance(payload, dict) or payload.get("jsonrpc") != "2.0":
        return _error(None, -32600, "invalid JSON-RPC 2.0 request")
    method = str(payload.get("method") or "")
    rpc_id = payload.get("id")

    if method.startswith("notifications/"):
        return None
    if method == "initialize":
        proto = ((payload.get("params") or {}).get("protocolVersion")) or _PROTOCOL_DEFAULT
        return _result(rpc_id, {
            "protocolVersion": proto,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "avo-bookd-port", "version": "1.0.0"},
            "instructions": (
                "Book'd-scoped port into AVO. Tools: bookd_message (converse), "
                "bookd_status (read state), bookd_handoff_secret (secure credential "
                "handoff). Answer-only; actions and decisions are flagged to Michael."),
        })
    if method == "ping":
        return _result(rpc_id, {})
    if method == "tools/list":
        return _result(rpc_id, {"tools": tools_for(grant.get("scope", "bookd"))})
    if method == "tools/call":
        params = payload.get("params") or {}
        name = str(params.get("name") or "")
        args = params.get("arguments") or {}
        try:
            return _result(rpc_id, _call_tool(name, args if isinstance(args, dict) else {},
                                              grant))
        except Exception:
            logger.exception("[bookd-mcp] tool %s crashed", name)
            return _result(rpc_id, _tool_text({"error": "internal error"}, is_error=True))
    return _error(rpc_id, -32601, f"method not found: {method}")
