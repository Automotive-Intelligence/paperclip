# Design Spec: SP4 -- Autonomous First Touch (no approval queue)

**Date:** 2026-08-07
**Owner decision:** Michael, 2026-08-07: "I do SP4, but no approval queue. Install
guardrails in context and be intelligent enough that we don't need approval
queues. We're a top executive solution." Recorded as the OWNER AMENDMENT to
principle 17 in `sdr-desk-principles.md` (BINDING; read it first).
**Consumes:** SP1 gate (verified opportunities), the send-as-brand rail
(authorized 2026-08-07: wd/avi/bookd identities), reply-sync (the return path).

## What it does

`services/sdr_first_touch.py :: run_first_touch(commit=False)` -- daily engine.
For each Twenty brand (wd/avi/bookd): read SDR-verified opportunities that have
never been first-touched, and for each one EITHER send one template-built,
evidence-validated, adversary-checked 1:1 email AS the brand identity, OR record
exactly why it did not (an exception line in the digest). Nothing ever waits for
approval; failures die visibly.

## The guardrail stack (all fail-closed, all structural)

1. **Source lock:** only opportunities whose name carries the `SDR-verified`
   marker (created by the gate-fed engine) are readable. Junk lists cannot
   reach this path by construction.
2. **Verified recipient only:** the contact email must be published on the
   company's OWN site (homepage or /contact, mailto or plain-text), fetched at
   draft time. Domain of the email must match the company's real primary site
   (subdomain-tolerant). No site-published email -> exception `no_verified_email`
   (enrichment is the twin build). Never a broker address.
3. **Template + verified-slot copy:** the email is a fixed per-motion template.
   The only variable content: company name, real primary site, and a
   defect phrase drawn from a FIXED mapping keyed by the gate's verified defect
   kind. A deterministic validator proves: every slot value appears in the
   opportunity/gate evidence; no digits-bearing claim outside the template
   constants; no pricing vocabulary (price/quote/$/cost/%); no em-dash.
   Validator failure -> exception, never a rewrite loop.
4. **The Scrutineering Gate, in-line (Michael's mid-build directive):** every
   draft passes the standing AVO maker-checker contract before it can leave --
   Tier-0 kill-switches (fabrication, pricing content, manipulation/false
   urgency, generic audience, guru voice) then 0-5 scoring on truth/
   specificity/voice/respect/clarity; PASS requires every dim >= 4 AND
   avg >= 4.5; scorer down -> BLOCK (fail-safe mirrors
   avo-telemetry/scripts/scrutineering_gate.py, never fail-open). Blocked
   drafts die as digest exceptions -> `scrutineering_block`. Follow-up: unify
   verdict logging with scrutineering_log.jsonl at next consolidation.
5. **Hard pre-send gates:** suppression/DNC union check (`services/suppression`)
   -> `suppressed`; first-touch dedup = a `FIRST TOUCH` note already linked to
   the opportunity -> skip; **cap: 5 sends/day/brand** (counted from
   `brand_send_audit`, seat `sdr_first_touch`) -> `cap_reached`; send window
   Mon-Fri 08:00-17:30 CT -> `outside_window`; kill switch
   `SDR_FIRST_TOUCH_ENABLED=1` required for any live send.
6. **Send + receipts:** `send_as_brand` (audited, authorized identities only,
   still gated by SEND_AUTHORIZED_MAILBOXES). On success: `FIRST TOUCH sent
   <date>` note linked to the opportunity (the durable dedup marker) + digest
   line. The reply path is already live (hourly reply-sync -> Twenty + SMS on
   positive; angry/negative replies never answered by machine, per the two
   permanent holds).
7. **Scope exclusions (permanent, not approvals):** no pricing content ever;
   angry replies route to Michael; spend untouched.

## Copy contract (rebuild motion, v1)

Subject: `about {domain}`
Body: plain, Michael-voice, no em-dashes: owns why we looked at the site, states
the ONE verified defect in plain words, offers a free specific look, one-line
opt-out ("If you'd rather not hear from me, reply no thanks and that is the
end of it."), signed by the brand identity's human name + brand. Defect-kind
phrase map is a code constant reviewed here:
- `pinch_zoom_blocked` -> "your site blocks pinch-to-zoom on phones, which makes
  it hard for mobile visitors to read"
- `site_down` -> "your site at {domain} is not loading right now"
- `cert_warning` -> "your site is showing a security-certificate warning"
- `no_contact_path` -> "there is no phone number or contact form on your homepage"
- `slow_load` -> "your homepage takes noticeably long to start loading"

## Follow-ups required before volume scales (flagged, honest)

- Wire NEGATIVE replies ("no thanks") from reply-sync into the suppression rail
  automatically (today: tiny volume + daily digest watch; opt-out promise must
  become mechanical before the cap ever rises).
- Contact enrichment (GBP/Yelp) to convert `no_verified_email` exceptions.
- The 5/day/brand cap is a code constant on purpose. Raising it is an owner
  conversation about cold-domain infra (primary-mailbox doctrine).

## Out of scope

Sequences/follow-up touches (reply-sync + humans own the thread after touch 1);
non-Twenty brands (AIPG/GHL later); price talk (permanent hold).
