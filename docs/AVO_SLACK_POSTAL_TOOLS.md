# AVO ⇄ Postal Inbox — tool wiring spec (avo-slack)

**Purpose:** register 6 tools in `salesdroid/avo-slack` so AVO can search / read /
triage **any** connected inbox (`avi, wd, salesdroid, aipg, agentempire, bookd`),
not just the single account the Gmail MCP is bound to.

These tools are thin HTTP calls to the paperclip backend endpoints shipped in
`automotive-intelligence/paperclip` (PR #81, merged). Backend reference:
`docs/POSTAL_INBOX_API.md`.

> Handoff note: this file is the source of truth for a session that has access
> to `salesdroid/avo-slack`. Map that repo's existing tool-registration pattern
> first (it routes persona LLM calls via OpenRouter, so there is already a
> tool/function-schema list); then add the six tools below following that exact
> pattern. Do **not** invent a new framework — mirror how the current tools are
> declared and dispatched.

---

## Config the tools need

| Value | Where | Notes |
|---|---|---|
| `PAPERCLIP_BASE_URL` | env | deployed paperclip URL, e.g. `https://paperclip-production-ba14.up.railway.app` |
| `PAPERCLIP_API_KEY`  | env | one of the keys in paperclip's `API_KEYS`; sent as `Authorization: Bearer <key>` |

Every call sets header `Authorization: Bearer ${PAPERCLIP_API_KEY}`.

Valid `account` values: `avi`, `wd`, `salesdroid`, `aipg`, `agentempire`, `bookd`.

HTTP error contract (surface the body text to the model so it can self-correct):
`400` unknown account / bad input · `404` no active token for that account
(needs re-OAuth) · `502` upstream Gmail error.

---

## The 6 tools

### 1. `postal_inbox_search`
Search a connected inbox using Gmail query syntax.
- HTTP: `GET ${BASE}/postal/inbox/search?account={account}&q={q}&limit={limit}`
- Returns: `{account, query, threads:[{id, snippet, historyId}]}`

```json
{
  "name": "postal_inbox_search",
  "description": "Search a connected Gmail inbox by account label using Gmail query syntax (e.g. 'from:datamoon is:unread newer_than:7d'). Returns matching thread ids + snippets. Use postal_inbox_thread to read a result.",
  "parameters": {
    "type": "object",
    "properties": {
      "account": {"type": "string", "enum": ["avi","wd","salesdroid","aipg","agentempire","bookd"], "description": "Which inbox to search"},
      "q": {"type": "string", "description": "Gmail search query"},
      "limit": {"type": "integer", "default": 25, "description": "Max threads to return"}
    },
    "required": ["account", "q"]
  }
}
```

### 2. `postal_inbox_thread`
Read a full thread, simplified to per-message dicts with extracted body text.
- HTTP: `GET ${BASE}/postal/inbox/thread?account={account}&thread_id={thread_id}`
- Returns: `{account, thread_id, message_count, messages:[{id, from, to, cc, subject, date, snippet, body, label_ids}]}`

```json
{
  "name": "postal_inbox_thread",
  "description": "Read a full email thread from a connected inbox. Returns each message with from/to/subject/date and the extracted plain-text body. Get the thread_id from postal_inbox_search.",
  "parameters": {
    "type": "object",
    "properties": {
      "account": {"type": "string", "enum": ["avi","wd","salesdroid","aipg","agentempire","bookd"]},
      "thread_id": {"type": "string", "description": "Gmail thread id"}
    },
    "required": ["account", "thread_id"]
  }
}
```

### 3. `postal_inbox_labels`
List the labels in an inbox (to find a label id / confirm a name).
- HTTP: `GET ${BASE}/postal/inbox/labels?account={account}`
- Returns: `{account, labels:[{id, name, type}]}`

```json
{
  "name": "postal_inbox_labels",
  "description": "List Gmail labels for a connected inbox (system + user labels).",
  "parameters": {
    "type": "object",
    "properties": {
      "account": {"type": "string", "enum": ["avi","wd","salesdroid","aipg","agentempire","bookd"]}
    },
    "required": ["account"]
  }
}
```

### 4. `postal_inbox_label`
Ensure a label exists and apply it to a thread.
- HTTP: `POST ${BASE}/postal/inbox/label`  body `{account, thread_id, label}`
- Returns: `{ok, account, thread_id, label, label_id}`

```json
{
  "name": "postal_inbox_label",
  "description": "Apply a label to a thread in a connected inbox. Creates the label if it doesn't exist. Use a nested name like 'Postal/lead_response' for hierarchy.",
  "parameters": {
    "type": "object",
    "properties": {
      "account": {"type": "string", "enum": ["avi","wd","salesdroid","aipg","agentempire","bookd"]},
      "thread_id": {"type": "string"},
      "label": {"type": "string", "description": "Label name to ensure + apply"}
    },
    "required": ["account", "thread_id", "label"]
  }
}
```

### 5. `postal_inbox_archive`
Archive a thread (remove the INBOX label).
- HTTP: `POST ${BASE}/postal/inbox/archive`  body `{account, thread_id}`
- Returns: `{ok, account, thread_id, action:"archived"}`

```json
{
  "name": "postal_inbox_archive",
  "description": "Archive a thread in a connected inbox (removes it from the inbox view; does not delete).",
  "parameters": {
    "type": "object",
    "properties": {
      "account": {"type": "string", "enum": ["avi","wd","salesdroid","aipg","agentempire","bookd"]},
      "thread_id": {"type": "string"}
    },
    "required": ["account", "thread_id"]
  }
}
```

### 6. `postal_inbox_mark_read`
Mark a thread as read (remove the UNREAD label).
- HTTP: `POST ${BASE}/postal/inbox/mark_read`  body `{account, thread_id}`
- Returns: `{ok, account, thread_id, action:"marked_read"}`

```json
{
  "name": "postal_inbox_mark_read",
  "description": "Mark a thread as read in a connected inbox.",
  "parameters": {
    "type": "object",
    "properties": {
      "account": {"type": "string", "enum": ["avi","wd","salesdroid","aipg","agentempire","bookd"]},
      "thread_id": {"type": "string"}
    },
    "required": ["account", "thread_id"]
  }
}
```

---

## Reference dispatch (adapt to avo-slack's actual tool layer)

Pseudocode — match it to however avo-slack currently calls tools (requests
client, error handling, auth injection):

```python
import os, requests

BASE = os.environ["PAPERCLIP_BASE_URL"].rstrip("/")
HEADERS = {"Authorization": f"Bearer {os.environ['PAPERCLIP_API_KEY']}"}
TIMEOUT = 20

def postal_inbox_search(account, q, limit=25):
    r = requests.get(f"{BASE}/postal/inbox/search",
                     params={"account": account, "q": q, "limit": limit},
                     headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()

def postal_inbox_thread(account, thread_id):
    r = requests.get(f"{BASE}/postal/inbox/thread",
                     params={"account": account, "thread_id": thread_id},
                     headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()

def postal_inbox_labels(account):
    r = requests.get(f"{BASE}/postal/inbox/labels",
                     params={"account": account}, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()

def postal_inbox_label(account, thread_id, label):
    r = requests.post(f"{BASE}/postal/inbox/label",
                      json={"account": account, "thread_id": thread_id, "label": label},
                      headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()

def postal_inbox_archive(account, thread_id):
    r = requests.post(f"{BASE}/postal/inbox/archive",
                      json={"account": account, "thread_id": thread_id},
                      headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()

def postal_inbox_mark_read(account, thread_id):
    r = requests.post(f"{BASE}/postal/inbox/mark_read",
                      json={"account": account, "thread_id": thread_id},
                      headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()
```

On non-2xx, return the response body text to the model (don't swallow it) —
a `404` means "that inbox isn't connected, run OAuth" and a `400` means
"bad account label", both of which AVO can act on or relay.

---

## Acceptance check (after wiring)

1. `PAPERCLIP_BASE_URL` + `PAPERCLIP_API_KEY` set in avo-slack's env.
2. In Slack: ask AVO "search the avi inbox for unread from datamoon" → it calls
   `postal_inbox_search(account="avi", ...)` and returns threads.
3. "read that thread" → `postal_inbox_thread` returns the body.
4. Confirm AVO can target each of the 6 accounts by name (no longer salesdroid-only).

## Prereqs (paperclip side — already done / your steps)

- Endpoints live in paperclip `app.py` (`/postal/inbox/*`), merged in PR #81.
- Deploy paperclip `main` so the endpoints are reachable at `PAPERCLIP_BASE_URL`.
- Run `scripts/postal_audit.py --check-live` to confirm which of the 6 inboxes
  actually have live tokens; re-OAuth any that fail before relying on them.
