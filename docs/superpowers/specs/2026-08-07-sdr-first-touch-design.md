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

## Out of scope (v1, WD rebuild motion)

Sequences/follow-up touches -- SHIPPED 2026-08-11, see
`sdr_first_touch.py`'s module docstring (up to 3 touches, reply-gated).
Price talk stays a permanent hold (unchanged).

## 2026-08-11 -- SP5 direction: the no-call-close motion, AIPG-first

**Owner decision (Michael, verbatim intent):** processed an external
agency-outreach video ("2-minute audit / no-call close"). Real, reusable
mechanics extracted (guru credibility-signaling packaging discarded):
permission-first ask before pitching, a non-restarting 3-touch follow-up
(shipped, above), the BYAF "you're free to say no" close (already in the
touch-1 opt-out line; extended into follow-ups), the price-tag rule (price
never stated alone, paired with value math the PROSPECT computes -- scoped
to the human close call only, S5 of file 400, NOT autonomous sends), and
the core mechanism itself: a short audit (video, in the source) that states
one real, evidence-backed problem, the fix, the price, and a direct way to
buy -- no call, ever.

**Product-fit reasoning (Michael + Claude, 2026-08-11):** the video's own
example product was a narrow, cheap-to-deliver, flat-monthly automation (a
3-message review-request sequence), NOT a broad engagement. Checked each
AVO brand against "narrow, cheap to deliver, provably automated, no setup
fee, no minimum term":
- WD (rebuild motion, current SP4) -- wrong shape; broad marketing work,
  not a narrow SaaS-style deliverable. This is WHY no existing WD price
  ($1,500/mo Foundations, $399/mo sales-floor, $482/mo verbal ruling) ever
  cleanly fit a no-call price-in-message close -- wrong product, not just
  wrong number.
- AvI ($2,500 diagnostic -> $3,500/mo advisory) -- high-touch consulting,
  wrong shape.
- Book'd -- ALREADY self-serve, ALREADY no-call by design (live Stripe
  checkout, $59/$69/$79 tiers, no setup fee, no minimum term). Michael,
  2026-08-11: "I think booked would be a fit for this as well" -- flagged
  as a strong architectural fit (Ryan's business/product/pricing; AVO
  builds the OUTREACH layer pointing at Book'd's existing checkout, never
  redesigns the product or price without Ryan).
- **AIPG -- the confirmed first build.** Already a narrow, flat-monthly
  SaaS product (Sophie, $299/$499/mo per pricing.yaml, or $482 per
  Michael's 08-02 ruling -- UNSETTLED, same as WD, needs the deferred
  Money Architecture session). The evidence is honestly, publicly provable
  without any live secret-shop call at scale (legal/consent complexity,
  doesn't safely automate): mine the business's OWN Google reviews (Places
  New `places.reviews` field) for missed-call/no-answer language. Same
  principle as the video's review-count-gap -- use evidence the business
  already published, never manufacture new contact.

**Real scope, not a toggle (owning the "you don't update the files"
correction: this is the honest accounting, written down before more
building happens):**
1. AIPG has an active Postal token with `gmail.send` already granted
   (verified live 2026-08-07) but is MISSING from `tools/brand_send.py`
   `BRAND_IDENTITIES` -- a real gap, small fix.
2. AIPG has NO Twenty workspace. SP1-4's whole pipeline (gate, engine,
   first-touch) is built around Twenty as the opportunity store. AIPG's
   real CRM is GHL (`tools/ghl.py` already has a `send_email` conversations
   path). A new AIPG motion needs GHL as its store, not a Twenty shim --
   this is new plumbing, not a flag flip.
3. A new evidence check (missed-call review mining via Places) -- new
   code, needs its own honesty validator (same discipline as
   `DEFECT_PHRASES`: only what a fetched review literally says, never an
   inferred count).
4. A new copy template + Scrutineering pass for this motion (Sophie
   as the fix, not a website rebuild).

**Book'd: gate intentionally NOT flipped yet.** `sdr_first_touch.py`'s
`_BRANDS["bookd"]` motion_ok stays `False` until Book'd has its OWN
template -- flipping it today would route Book'd candidates through the
WD rebuild-defect copy, which is false (Book'd does not sell website
rebuilds). That would be exactly the kind of fabricated-claim mistake this
whole system exists to prevent. Real next step once AIPG ships: a Book'd
motion using the same review-evidence approach, built WITH Ryan's product/
pricing sign-off (it is his business), pointing at Book'd's existing live
checkout -- not a new price invented by AVO.
