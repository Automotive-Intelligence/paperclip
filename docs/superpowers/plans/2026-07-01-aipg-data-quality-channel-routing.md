# AIPG Prospect Data Quality + Channel Routing Guardrails

Date: 2026-07-01
Branch: `fix/aipg-data-quality-channel-routing`
Owner: B&T (dispatched by TP), Boardroom-identified send blocker.

## Problem (probed live GHL 2026-07-01, loc `ZoxVB4ibMZZ2lZ5QpXep`, read-only)

Randy (`rivers/ai_phone_guy/workflow.py`) enrolls every `tyler-prospect` contact
with a recognized trade into an ICP sequence. Those sequences interleave SMS and
EMAIL steps (`sequences.py`). Randy applies **no data-quality gate**, so bad
records reach live sends. Real numbers on the 187 live `tyler-prospect` contacts:

| defect | count | consequence today |
|---|---|---|
| no email at all | 61 | still get the EMAIL steps of their sequence (silent no-op / bad data) |
| free/personal email (gmail/yahoo/outlook) | 13 | low-trust B2B email, phone-first owners |
| valid business email | 113 | genuinely emailable |
| invalid area code (`875` x5, `995` x1, `120` x1) | 7 | dead SMS/call targets |
| no phone | 7 | can't be routed to the call lane |
| person-name field holds the company name | 70 | `{firstName}` merge renders "Little" / "Celina" etc. |

AIPG **sells an AI phone receptionist**. The core strategic miss: phone-first
local trades (plumbers/HVAC/roofers) with no business email are aimed at EMAIL
instead of the SMS/CALL lane that matches the product.

## Design

Add ONE guardrail module and wire it into the enrollment path so bad records
never reach a sequence. Keep GHL access read-only in this change (routing tag is
recorded on the enrollment record + a `_channel` field on the contact dict that a
future call-lane consumes; we do NOT mutate GHL contacts here).

### New module: `rivers/ai_phone_guy/data_quality.py`
Pure functions, no I/O, fully unit-testable:
- `normalize_email(e)` / `is_valid_business_email(e)` — RFC-ish shape + reject
  free-mail domains (gmail/yahoo/hotmail/aol/outlook/icloud/msn/live/comcast).
- `normalize_phone(p)` -> E.164-ish digits; `phone_area_code(p)`;
  `is_valid_na_phone(p)` — NANP rules: 10 digits (or 11 w/ leading 1), area code
  not starting 0/1, no N11, and reject the confirmed-bogus set `{875, 995, 120}`
  plus toll-free-as-local noise. `is_bad_phone` = inverse.
- `looks_like_company_in_name(contact)` — firstName/lastName equals companyName.
- `clean_name_fields(contact)` — if the name field is the company, blank the
  first name so merge tags don't render a company fragment; keep companyName.
- `dedup_key(contact)` — normalized `companyName|email|phone` for dedup.
- `route_channel(contact)` -> `"email"` | `"sms"`:
    * has valid business email -> `"email"`
    * else (no email / free email / invalid) but has a valid phone -> `"sms"`
    * else -> `"excluded"` (no reachable channel).
- `SMS_LANE_TAG = "aipg-sms-lane"` / `EMAIL_LANE_TAG = "aipg-email-lane"` — the
  routing property the future call-lane consumes.

### Wire into `workflow.py` `_find_new_prospects` (the enroll gate)
After a contact matches the tag + has a vertical, run the guardrails BEFORE it is
returned as enrollable:
1. **Clean name fields** (parse fix).
2. **Dedup** within the run by `dedup_key` (drop later dupes; log).
3. **Validate phone**: flag bad area codes; a contact with neither a valid email
   nor a valid phone is excluded (no reachable channel).
4. **Channel route**: attach `contact["_channel"]`. Email-havers -> email lane;
   phone-first/no-email/free-email -> SMS lane.
5. Accumulate exclusion/reroute **counts with reasons** into the run tally; never
   silently drop.

### Enrollment must respect the channel (the email gate)
`_process_sequences` / `_send_sequence_step`: when a contact's `_channel` is
`"sms"`, **skip the EMAIL steps** of its sequence (only send SMS steps). This is
the enrich-or-exclude-on-email rule at the send boundary — no validated business
email => never sent an email. Record the routing tag on the enrollment record.

### Telemetry
`_format_run_summary` gains an excluded/rerouted block:
`no_email_rerouted_to_sms`, `bad_phone_flagged`, `dupes_dropped`,
`names_cleaned`, `no_reachable_channel_excluded`, plus per-lane enrolled counts.

## Tests (`tests/test_aipg_data_quality.py`, unittest)
- no business email => excluded from EMAIL (routed sms or excluded), never emailed.
- dupes (same normalized company+phone) dropped, first kept.
- bad phones (`875`/`995`/`120`, N11, leading-1 area) flagged invalid; good DFW
  codes (972/469/940/214/817) pass.
- phone-first contact tagged `aipg-sms-lane`; email-haver tagged
  `aipg-email-lane`.
- company-in-name cleaned so `{firstName}` won't render a company fragment.
- integration: `_find_new_prospects` on a mixed batch returns correct lanes +
  tally counts.

Existing `tests/test_aipg_randy_tag_contract.py` must still pass.

## Out of scope / not done
- No GHL contact mutation (read-only). Actual SMS/call sending lane is future work
  that consumes the `aipg-sms-lane` tag; here we only route + tag intent.
- No merge, no deploy.
