# Pit Wall v2 — the operator dashboard

**Status:** PARKED, spec'd and ready to build. Not started.
**Parked:** 2026-08-23 · **Owner:** Build & Tech · **Requested by:** Michael

This is a complete spec, written so it can be picked up cold months from now without
reconstructing the reasoning. If you are reading this and about to redesign the
dashboard, start here rather than from scratch.

---

## Why this exists

On 2026-08-23 we installed the open-source `paperclipai` product (an unrelated project
that shares our repo's name) to see what a packaged version of AVO's thesis looks like.
Their UI is better than Pit Wall in real ways. Michael asked: can we build a better one.

The answer is yes, but **"better than theirs" is the wrong target.** Their dashboard is
a generic agent-ops console built for a stranger's business, and on their own launch
screen it rendered a "Success Rate, last 14 days" chart for a system seven minutes old.
Their hard problem is having anything to show. Ours is choosing what to show. Copying
their shape would inherit their constraint and waste our advantage.

## The thesis

Michael sells cars at Chevrolet full time. AVO runs without him. He checks his phone
between customers. So the dashboard has exactly one job:

> **What needs me, what is the machine doing without me, and did we make money?**

Three questions. Everything else is a drill-down. Phone-first, not desktop-first.

**OPEN QUESTION FOR MICHAEL (ask before building):** when he pulls out his phone at
work and opens this, what is he actually checking? The three bands below are B&T's best
inference from watching him work. His answer overrides them.

## The three bands

### 1. NEEDS YOU
The only section that is not glance-and-forget. Converts his attention into action.

- Pending partner action requests (`partner_action_requests` where status='pending')
- Staged credential handoffs awaiting install (`bookd_handoff_staging`)
- Red canaries (`lead_canary` latest responded=false)
- Watchdog criticals
- **One-tap approve / deny inline.** Today Michael approved a live Stripe key install by
  asking B&T to run curl. That should be a button. This is the single highest-value
  element on the screen.

### 2. MONEY
The question no surface he owns currently answers.

- Spend today and month-to-date, by service (OpenRouter, Anthropic, fal, Meta ads)
- **Against a cap.** 2026-08-23: the OpenRouter key was found UNCAPPED with $139.89
  already spent. Michael is pre-revenue and cost-sensitive; an uncapped credential is
  the same shape of risk as the abandoned OAuth ritual, fine until it isn't.
- Revenue side: "did we make money" is the real question.

### 3. THE MACHINE
- Of the live scheduled jobs, which ran, which are late, which are red.
- What shipped today. Partner port activity (Ryan's agent).
- NOT a 113-row table. A health line with exceptions surfaced, the rest collapsed.

### 4. Reference tab
The Stack Inventory page (shipped 2026-08-23, `/inventory`) becomes the reference layer
beneath the operational one.

## Hard rules

- **No chart without data.** If there is nothing to show it says "no runs yet." Never a
  decorative sparkline. 2026-08-23 cost three cycles to stale and wrong records; this
  dashboard must be the one surface that never lies to him.
- **Phone-first.** Designed for a glance in a parking lot, not a desk.
- **Truthful counts.** Read the live system (running scheduler, live tables), never a
  cached or hand-maintained number. See `services/stack_inventory.py` for the pattern.

## Build notes

- React source: `pitwall-ui/src/` (vite, Tailwind, framer-motion, nivo, react-pro-sidebar).
  Build with `npm install && npm run build` in `pitwall-ui/`; output lands in
  `static/pitwall-react/`. `emptyOutDir: false`, so DELETE stale unreferenced asset
  files after a rebuild or they ship as dead weight.
- New pages need three edits: `src/App.tsx` (route), `src/components/DashboardShell.tsx`
  (nav), `src/lib/api.ts` (type + fetcher), plus a FastAPI route serving `index.html`
  and the path added to `_DASH_AUTH_EXACT` in `app.py`.
- API endpoints under `/api/pitwall/` inherit dashboard Basic Auth automatically.
- Reference implementation: `/api/pitwall/inventory` + `InventoryPage.tsx`.

---

## Steal list from `paperclipai` (evaluated 2026-08-23)

Captured so the evaluation is not repeated. Their product is NOT adoptable (greenfield,
runs agents as local Claude Code CLI invocations on the laptop, no knowledge of our
system) but four ideas are genuinely better than ours:

1. **External-agent invite flow — THE BEST IDEA, worth building on its own.**
   Generate a one-time onboarding prompt -> the agent requests access -> admin approves
   -> **the agent claims its own API key.** The credential never passes through a human.
   Ours does the opposite: B&T mints a key, Michael texts a 48-char bearer token, Ryan
   lost it, B&T rotated it, and the key now exists in a text thread and a chat
   transcript. A one-time claim token is single-use and short-lived, so losing it costs
   almost nothing. **Proposed:** `POST /admin/partner-invite` -> claim token (+ optional
   role/brief message, a nice touch of theirs) -> agent posts to `/bookd/agent/claim` ->
   lands pending, Michael paged -> one-tap approve -> agent receives its scoped key over
   the wire it already trusts. Single-use, expiring. Est. ~2 hours; the key store,
   scopes, revocation, and approval plumbing already exist.
2. **Costs as a first-class page with a budget.** Directly motivated band 2 above.
3. **A "Board" governance entity** above the lead agent, with a visible Pending
   Approvals queue. We have the behavior (things escalate to Michael) but it is
   implicit. They made it an object with a queue.
4. **"Environment lease acquired"** — an isolated environment leased per agent run, with
   a visible lifecycle. Our worktree isolation promoted to a product concept.

**Their tell:** given AVO's real mission verbatim, their agent's self-generated first
task was "Hire your first engineer and create a hiring plan." The mission is stored, not
reasoned from.
