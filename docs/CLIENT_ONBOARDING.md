# Client Onboarding

Standard steps to bring a new client onto AVO. This doc is the source of truth
for onboarding; add a checklist copy per client in your tracker.

---

## 1. Anthropic workspace + API key (spend tracking) — DO THIS FIRST

Every client gets **its own Anthropic workspace and API key.** This is how we
attribute AI spend per client — the Anthropic Console breaks usage and cost down
by workspace and by key automatically, so a per-client key means per-client
spend with zero extra code.

**Why per-client (not one shared key):**

- **Spend attribution** — Console → Usage & Cost shows spend per workspace/key.
  This is the "turn on per-key spend" that makes client profitability visible.
- **Blast radius** — a leaked or runaway key affects one client, not all.
- **Hard caps** — set a per-workspace spend limit so one client can't surprise us.
- **Clean offboarding** — disable one key, done.

**Steps (Anthropic Console, console.anthropic.com):**

1. **Create a workspace** named for the client (e.g. `client-acme`).
2. **Create an API key** inside that workspace. Name it `acme-prod`.
3. **Set a monthly spend limit** on the workspace matched to the client's plan
   (start conservative; raise as usage proves out).
4. **Store the key** in Doppler / Railway under a per-client var
   (`ANTHROPIC_API_KEY__ACME`), never in code or this doc.
5. **Tag spend in the ledger too.** When a client-facing agent runs for this
   client, pass `client="acme"` to `services/llm_ledger.record_from_response(...)`
   so the internal ledger + daily spend email slice by client as well as the
   Console. (Internal personas leave `client` null.)

**Result:** client spend is visible two ways — natively in the Anthropic Console
(per workspace/key) and in our own `llm_spend_ledger` / daily spend email (per
`client` tag), which also lets us margin-check spend against the client's revenue.

> The two surfaces are complementary: the Console is the billing source of truth;
> the ledger gives arbitrary slices (per-persona, per-brand, per-client) and feeds
> the daily email. Reconcile ledger totals against the Console monthly.

---

## 2. CRM routing

Confirm which CRM the client maps to and wire it (see README → Multi-CRM Plug
And Play): `BUSINESS_CRM_MAP` / `AGENT_CRM_MAP`, plus provider credentials
(GHL / HubSpot / Attio).

## 3. Brand context + voice

Capture brand kit, voice (per the CMO anti-guru / hero-metrics policy), and any
content freezes. Client-marketing production runs through `#client-marketing-garage`.

## 4. Access + comms

Set up the client's comms channel and any shared drives. Confirm escalation path.

## 5. Verify

- [ ] Anthropic workspace + key created, spend limit set, key in Doppler/Railway
- [ ] CRM mapping live; test contact routes correctly
- [ ] Brand context captured
- [ ] First spend row appears in `llm_spend_ledger` tagged with the client
- [ ] Client shows up in the next daily spend email's "By client" section
