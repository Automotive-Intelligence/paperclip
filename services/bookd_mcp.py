"""services/bookd_mcp.py -- MCP front door for the Book'd partner-agent port.

A minimal, STATELESS Model Context Protocol server over Streamable HTTP: one POST
route (wired in app.py at /bookd/mcp) speaking JSON-RPC 2.0 and answering with plain
JSON (the spec's stateless mode -- no SSE stream, no session ids). Hand-rolled on
purpose: it is ~100 lines, adds zero dependencies, and avoids mounting a second ASGI
app + lifespan into FastAPI. Any MCP-capable harness (Ryan's Hermes included) mounts it
with a URL + bearer key; anything else uses the plain REST endpoints, which share the
exact same core (services/bookd_agent + services/bookd_handoff).

Methods: initialize, ping, tools/list, tools/call; notifications/* are acknowledged
with no body. Auth happens in app.py (scoped BOOKD_AGENT_KEYS, never master API_KEYS).
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


def _result(rpc_id: Any, result: Dict[str, Any]) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": rpc_id, "result": result}


def _error(rpc_id: Any, code: int, message: str) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": code, "message": message}}


def _tool_text(payload: Dict[str, Any], *, is_error: bool = False) -> Dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(payload, default=str)}],
            "isError": is_error}


def _call_tool(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    from services.bookd_agent import handle_message, status
    from services.bookd_handoff import stage
    if name == "bookd_message":
        out = handle_message(args.get("conversation_id"),
                             str(args.get("message") or ""), source="hermes-mcp")
        return _tool_text(out, is_error=out.get("disposition") in ("error", "invalid"))
    if name == "bookd_status":
        return _tool_text(status())
    if name == "bookd_handoff_secret":
        out = stage(str(args.get("key_name") or ""), str(args.get("value") or ""),
                    submitted_by="hermes-mcp")
        return _tool_text(out, is_error=not out.get("ok"))
    return _tool_text({"error": f"unknown tool {name!r}"}, is_error=True)


def handle_rpc(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """One JSON-RPC message -> response dict, or None for notifications (HTTP 202)."""
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
        return _result(rpc_id, {"tools": TOOLS})
    if method == "tools/call":
        params = payload.get("params") or {}
        name = str(params.get("name") or "")
        args = params.get("arguments") or {}
        try:
            return _result(rpc_id, _call_tool(name, args if isinstance(args, dict) else {}))
        except Exception:
            logger.exception("[bookd-mcp] tool %s crashed", name)
            return _result(rpc_id, _tool_text({"error": "internal error"}, is_error=True))
    return _error(rpc_id, -32601, f"method not found: {method}")
