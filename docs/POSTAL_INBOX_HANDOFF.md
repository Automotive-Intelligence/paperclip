# Postal Inbox → AVO: Handoff (finish the wiring)

Status as of this handoff:

- **Done (paperclip):** PR #85 merged to `main` (commit `192ffd0`). Added
  `tools/postal_inbox_tools.py` — six AVO tools wrapping the backend's
  `/postal/inbox/*` endpoints (search, thread, labels, apply_label, archive,
  mark_read), plus a `POSTAL_INBOX_TOOLS` manifest
  (`{name, description, input_schema, handler}`), a `POSTAL_INBOX_HANDLERS`
  name→handler map, and a `dispatch(name, **kwargs)` entry point. Spec:
  [`docs/POSTAL_INBOX_API.md`](./POSTAL_INBOX_API.md).
- **Left to do (avo-slack / Paperclip MCP — separate repo + Railway service):**
  register the tools, set env vars, verify, open a PR. See the prompt below.

The tools talk to the Paperclip backend over HTTP and read two env vars:
`PAPERCLIP_BASE_URL` and `PAPERCLIP_API_KEY` (sent as a Bearer token).

## Prompt to paste into Claude Code (in the avo-slack / Paperclip MCP repo)

```
Context: We just merged paperclip PR #85 (commit 192ffd0 on main), which added
tools/postal_inbox_tools.py — six AVO tools wrapping the backend's
/postal/inbox/* endpoints (search, thread, labels, apply_label, archive,
mark_read), plus a `POSTAL_INBOX_TOOLS` manifest
({name, description, input_schema, handler}), a `POSTAL_INBOX_HANDLERS`
name→handler map, and a `dispatch(name, **kwargs)` entry point. Full spec is in
paperclip/docs/POSTAL_INBOX_API.md. The tools talk to the Paperclip backend over
HTTP and read two env vars: PAPERCLIP_BASE_URL and PAPERCLIP_API_KEY (sent as a
Bearer token).

What's left to make this live in avo-slack / the Paperclip MCP — please do all of it:

1. Register the six tools in this service. First figure out how this repo
   currently registers MCP/AVO tools (grep for existing tool registration, the
   MCP server setup, or how other backend endpoints are exposed) and follow that
   exact pattern — one tool per endpoint.
   - Decide and tell me: does this service import `paperclip` as a library
     (so it can `from tools.postal_inbox_tools import POSTAL_INBOX_TOOLS`), or is
     it fully separate? If separate, port the module into this repo (keep it
     framework-agnostic) and register from the local copy. Don't duplicate logic
     if you can import it.
   - The handlers are plain callables taking keyword args matching each tool's
     input_schema and returning the parsed JSON body; they raise
     PostalInboxToolError (carries the upstream HTTP status) on failure. Map that
     to whatever error shape this framework expects.

2. Set the env vars wherever this service runs (Railway, likely):
   - PAPERCLIP_BASE_URL = https://paperclip-production-ba14.up.railway.app
     (confirm this is the prod backend you want)
   - PAPERCLIP_API_KEY = one of the paperclip backend's API_KEYS values
   Don't hardcode the key; set it as a secret/env var.

3. Verify end to end: with the tools registered and env set, do a read-only call
   (e.g. postal_inbox_labels for account "avi", or postal_inbox_search with
   q="is:unread" limit=5) and confirm a real response. Accounts only work if they
   have an active token in postal_tokens — run scripts/postal_audit.py (in
   paperclip) if an account returns 404 "no active token".

4. Open a PR on this repo with the registration changes. Don't merge without my
   say. Tell me which accounts came back live in the verification step.

Available inbox account labels: avi, wd, salesdroid, aipg, agentempire, bookd.
```

## Notes before you run it

- The prompt assumes the session has the avo-slack repo (and ideally paperclip)
  available. If paperclip isn't open, the agent works from the spec and ports
  the module instead of importing it (step 1 covers that).
- Confirm `PAPERCLIP_BASE_URL` before setting it. The value above comes from
  `services/postal_oauth.py` in paperclip — verify it's the environment you want.
