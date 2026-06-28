# Postal Agent — Multi-Inbox API (for AVO)

On-demand access to **any connected inbox**, addressed by `account_label`. This
is what lets AVO "check the avi inbox" / "search wd for X" instead of being
limited to the single account the Gmail MCP is bound to.

Two separate systems, don't confuse them:

| System | What it is | Surface |
|---|---|---|
| **Gmail MCP** (`gmail.mcp.claude.com`) | single-account, bound to salesdroid | legacy |
| **Postal inbox API** (this doc) | all 6 OAuth'd accounts | `/postal/inbox/*` on the Paperclip backend |

Connected accounts: `avi`, `wd`, `salesdroid`, `aipg`, `agentempire`, `bookd`
(whichever have an active row in `postal_tokens` — run `scripts/postal_audit.py`).

## Auth

Every endpoint requires the Paperclip API key:

```
Authorization: Bearer <API_KEY>
```

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET  | `/postal/status` | connected accounts + sync state + `writes_enabled` |
| GET  | `/postal/inbox/search?account=<a>&q=<gmail-query>&limit=25` | search threads |
| GET  | `/postal/inbox/thread?account=<a>&thread_id=<id>` | full thread, simplified |
| GET  | `/postal/inbox/labels?account=<a>` | list labels |
| POST | `/postal/inbox/label` | `{account, thread_id, label}` — ensure + apply |
| POST | `/postal/inbox/archive` | `{account, thread_id}` — remove INBOX |
| POST | `/postal/inbox/mark_read` | `{account, thread_id}` — remove UNREAD |

`q` uses Gmail search syntax, e.g. `from:datamoon is:unread newer_than:7d`.

Errors: `400` unknown account / bad input · `404` no active token for that
account (re-run OAuth) · `502` upstream Gmail error.

### Example

```bash
curl -s "$BASE/postal/inbox/search?account=avi&q=is:unread&limit=10" \
  -H "Authorization: Bearer $API_KEY"

curl -s "$BASE/postal/inbox/thread?account=wd&thread_id=18f..." \
  -H "Authorization: Bearer $API_KEY"
```

## Wiring AVO

These are plain HTTP tools. Surface them through the Paperclip MCP the same way
the other backend endpoints are exposed, one MCP tool per endpoint. Once
registered, AVO can search/read/triage every connected inbox by name.

`tools/postal_inbox_tools.py` is that wiring: an AVO tool per endpoint
(`inbox_search`, `inbox_thread`, `inbox_labels`, `inbox_apply_label`,
`inbox_archive`, `inbox_mark_read`) plus a `POSTAL_INBOX_TOOLS` manifest
(`{name, description, input_schema, handler}`) to register one MCP tool per
endpoint, and a `dispatch(name, **kwargs)` entry point. Each function returns
the parsed JSON body and raises `PostalInboxToolError` (with the upstream
status code) on failure. Configure with `PAPERCLIP_BASE_URL` and
`PAPERCLIP_API_KEY`.

## Note on writes

The modify endpoints (`label`, `archive`, `mark_read`) act immediately — they
are **not** gated by `POSTAL_WRITES_ENABLED`, because they run only on an
explicit human request through AVO. That flag gates only the *autonomous*
Postal sweep (`agents/postal/postal_agent.py`), which classifies and routes new
mail on a schedule.
