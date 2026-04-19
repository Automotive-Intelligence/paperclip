# CHANGELOG — Week 12, 2026
## AVO — Weekly Build Report
Generated: 2026-04-18 22:34 CST

---

## Pipeline Activity (Last 7 Days)

### AI Phone Guy (GoHighLevel)
- Contacts enrolled: 0
- Hot leads triggered: 0
- RevOps agent: Randy (every 4h)

### Calling Digital (Attio)
- Contacts enrolled: 0
- Hot leads triggered: 0
- RevOps agent: Brenda (every 2h)

### Automotive Intelligence (HubSpot)
- Contacts enrolled: 0
- Hot leads triggered: 0
- RevOps agent: Darrell (every 1h)

### Agent Empire (Skool)
- Community agent: Tammy (every 6h)
- Content producer: Debra (Mon 6am)
- Sponsor outreach: Wade (Mon 9am)

### CustomerAdvocate (Internal)
- Technical builder: Clint (daily 10am)
- Web design: Sherry (daily 11am)

---

## Revenue Impact
- Total contacts enrolled: 0
- Total hot leads: 0
- Active rivers: 5
- Active agents: 22

---

## Development Activity
- Commits this week: 53
- Bugs fixed: 27
- Features added: 26

### Bugs Fixed
- cb96127 fix: add email-level dedup guard to prevent Tyler double-sends
- 68e79bb fix: correct Attio email duplicate lookup and handle uniqueness conflicts as duplicate skips
- 50b60c8 fix: normalize Attio person names from noisy contact fields with safe fallbacks
- 9a26552 fix: GHL search uses 'query' param (fixes 422); 400 on create treated as duplicate-skip; enricher name validation requires Title Case + no junk words
- eab1def fix: Attio person name/email payload format (first_name/last_name/email_address); bump parser to 8k input/4k output
- e6b6236 fix: remove locationId from note payload (GHL 422) and guard note call as non-fatal
- 35c77dd fix: harden GHL contact payload to avoid 400 validation failures
- fbada3d fix: remove duplicate note key in _default_channels (was silently falling through to crm)
- 1041920 fix: activation layer dispatch hardening
- 17ef8b6 fix: batch - dashboard live metrics, prospect_parser LiteLLM, api/metrics cleanup
- 25966f1 fix: replace undefined AGENT_TYPES with LOG_TYPES in /api/agent-logs endpoint
- d29858e fix: dashboard API_BASE uses window.location.origin instead of hardcoded localhost
- fb79de2 fix: parser uses LiteLLM/DeepSeek instead of Anthropic; fix Tyler inline output
- f716513 fix: increase max_tokens 1200→4000 so agents can write full prospect reports
- ae67649 fix: run run-now jobs in thread pool to prevent event loop blocking
- ba9c409 feat: add run-now debug telemetry for sales/content pipelines
- 2c22b2d fix: parse inline prospect format and fallback on empty parser results
- 384fb37 fix: fallback to heuristic parsing when Anthropic parse calls fail
- 368fc2f fix: add no-Anthropic parser fallbacks for sales/content pipelines
- 2f71f3c fix: disable CrewAI memory tools for Groq compatibility
- c479be1 fix: restore agent execution and durable history logging
- b364b5a fix: improve agent log persistence for Railway deployment
- 66d02e7 fix: update dashboard API_BASE to use relative URLs for Railway deployment
- 031aff6 fix: add litellm to support Groq provider for crewai
- 6947b08 fix: use absolute path for dashboard route
- 17b242a fix: allow app to start even if GROQ_API_KEY missing - will show error on dashboard
- ddc66b5 fix: hardcode Groq config to prevent crewai fallback to unsupported providers

### Features Added
- 932ee7f feat: allow per-business SMTP credentials in unified mode
- 18ae9ce feat: support per-business sender identities in unified SMTP
- cb96127 fix: add email-level dedup guard to prevent Tyler double-sends
- 1bf6bc7 feat: replace webhook publish with direct GHL Blog + Social Planner API (no premium trigger)
- 497d7d5 feat: GHL site and social publishing lane (10 tests passing)
- 69766c7 feat: add templated email composer, validation gates, and daily template quality report endpoint
- 9a26552 fix: GHL search uses 'query' param (fixes 422); 400 on create treated as duplicate-skip; enricher name validation requires Title Case + no junk words
- eab1def fix: Attio person name/email payload format (first_name/last_name/email_address); bump parser to 8k input/4k output
- e127b30 feat: real contact prospecting — search for name/email/phone, enrich before CRM push, cadence notes, pipeline status endpoint
- af3767e feat: add sales preflight and unified outbound email mode
- 08332b4 feat: phase 4 complete - Activation Layer MVP (30 tests passing)
- 9b68160 feat: phase 3 complete — DB service layer, Attio dedup, HubSpot deals, onboarding write API
- dc2d15d feat: multi-CRM routing by business with plug-and-play connectors
- 3ca3f8c feat: phase 2 reliability layer (service envelope, retries, structured logs)
- b89c09a feat: add LLM_MODEL/LLM_API_KEY env vars for OpenRouter/Chinese model support
- ba9c409 feat: add run-now debug telemetry for sales/content pipelines
- 368fc2f fix: add no-Anthropic parser fallbacks for sales/content pipelines
- a890220 feat: add authenticated run-now endpoint for immediate agent execution
- 8eb6ad9 feat: make agent output history durable and queryable
- 3dd05ee feat: add founder communication and physical-world CEO actions to briefs
- f408467 feat: add anti-hallucination guardrails for CEO briefings
- 031aff6 fix: add litellm to support Groq provider for crewai
- 1a3d33a feat: switch from Anthropic Claude to Groq API (free, 500x faster) - /bin/zsh/month operation
- 71c6b65 feat: add enhanced dashboard with agent log viewer, switch to Anthropic Claude LLM
- 1e854bc Build AI-native revenue engine: agents now prospect, email, and track pipeline autonomously
- 6a822e0 Add SalesGPT conversation engine skill

---

## AVO Cost Report
- Not captured (historical backfill)

---

## Next Week Priorities
- Monitor enrollment rates across all 5 rivers
- Review hot lead conversion rates
- Optimize sequence copy based on open/reply data
- Continue VERA behavioral scoring engine build
- Expand Agent Empire Skool community

---

*AVO — AI Business Operating System. $15,000 MRR across 5 rivers.*
*Michael shows up to close. Agents do everything else.*
*Built live for Agent Empire Skool community.*
*Named from Avoda — work is worship.*
