# CHANGELOG — Week 16, 2026
## AVO — Weekly Build Report
Generated: 2026-04-18 21:25 CST

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
- Commits this week: 39
- Bugs fixed: 23
- Features added: 18

### Bugs Fixed
- 262cfe6 fix(ghl): force $482 MRR on new opps, clean name format
- bdca320 fix: correct Gemini model ID — google/gemini-2.5-flash (1M context)
- 0eaf3f6 fix: scope Bloomberry tech enrichment to Ryan Data only
- fb67eb7 cleanup: remove /tech-debug endpoint from production
- bd0e9d9 fix: tighten AI/CRM/chat detection — eliminate false positives
- d562571 debug: add /tech-debug endpoint to verify Bloomberry key loading on Railway
- f5c7018 fix: parse real Bloomberry response format (vendor_name, vendor_category)
- 868ef64 fix: pass Bloomberry API key as query param instead of Bearer header
- 5c05cae fix: GHL custom field format — verified_fact/trigger_event were silently dropped
- 7f7db6f fix: align CRITICAL RULE with relaxed STEP 5 for all 3 sales agents
- 855a52f fix: unblock sales agents — cap search context + relax prospect criteria
- 43d8e9a fix: HubSpot token fallback + contact name mapping for Instantly pipeline
- 0557cea fix: strip [source: ...] annotations from cd_prospect_notes
- 317e984 fix: migrate instantly integration to v2 API
- b413906 fix: bump web_search to advanced depth, 10 results
- a0669ac fix: apply anti-fabrication guardrails to Marcus prompt
- 727f74a fix: recovery hardening after Marcus merge tag incident
- ad8ebd2 fix: accept real prospects with phone-only contact (email OR phone required)
- 0066b0c docs: add critical warning about Attio merge tag copy-paste bug
- 3c639ce fix: force Tyler + Ryan Data to use web_search tool, forbid fabrication
- 21bf456 fix: critical CRM routing bug + LLM assumed-email hallucinations
- 440970b fix: harden ICP filter against LLM hallucinations
- 281ca85 docs: Tyler Instantly campaign doc + fix Ryan Data merge tags

### Features Added
- 262cfe6 fix(ghl): force $482 MRR on new opps, clean name format
- a69d09c docs: Tyler GHL SMS workflow build guide — 3 workflows, click-by-click
- e023f9d feat: Ryan Data strategy — find websites, Bloomberry qualifies
- 5040bfb feat: add daily usage cap for Bloomberry (default 6/day, env configurable)
- 3f7e642 feat: switch Marcus + Ryan Data to Gemini Flash for better research
- a683bb4 feat: Joshua (Pit Wall) + shared F1 race analytics for all 3 RevOps agents
- 8b8e38d feat: add rooftop-level prospecting intelligence to Ryan Data agent
- d562571 debug: add /tech-debug endpoint to verify Bloomberry key loading on Railway
- de731de feat: Bloomberry tech stack enrichment with dealership AI-readiness scoring
- 7b24c9a feat: regional rotation for Marcus + Ryan, force web search, relax ICP
- 49d9db0 feat: overnight prospecting for all 3 sales agents + Marcus to full schedule
- 8ca7188 feat: agent-aware Instantly API keys for multi-workspace routing
- b645910 feat: marcus_vertical override on /admin/run-now
- 0066b0c docs: add critical warning about Attio merge tag copy-paste bug
- 1f6a18d feat: route Tyler email through Instantly (GHL reputation damaged)
- 199d381 feat: Tyler writes enrichment to GHL custom fields for sequence merge tags
- a46e0e9 feat: Ryan Data goes nationwide + CRM cleanup scripts + sequence docs
- c941547 feat: rebuild Tyler + Ryan Data as trigger-event SDRs, add Instantly integration

---

## AVO Cost Report
- Cost tracking not yet active

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
