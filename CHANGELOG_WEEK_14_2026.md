# CHANGELOG — Week 14, 2026
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
- Commits this week: 33
- Bugs fixed: 23
- Features added: 12

### Bugs Fixed
- 19626f4 fix: restore dashboard company→agent→history navigation with work history panel
- 614028b fix: remove corrupted voice transcription text from app.py line 2669
- 51211f0 fix: unpin fastapi/uvicorn/requests versions to resolve crewai+chromadb conflicts
- 1aff3a3 fix: railway.toml startCommand must use uvicorn app:app, not python main.py
- 79ccab2 fix: mount Paperclip rivers into existing app.py instead of replacing it
- 745d4dc fix: enforce business hours on all outbound messages
- ec4d3e6 fix: loosen python-dotenv version to resolve crewai dependency conflict
- 320cb1b fix: correct AI Phone Guy team roles — Alex is CEO, Zoe is marketing, Jennifer is client success
- 00b1ef1 fix: GHL media as plain URL strings, add payload logging for debugging
- 3d264f0 fix: GHL post only to target platform, not all accounts; try mime-type media
- c938210 fix: GHL media as objects with url key, fallback sends empty array
- 9c7586c fix: try GHL media as simple URL string array
- 76cb4e7 fix: always include media array in GHL payload, add caption field
- 90f9b16 fix: GHL social posts set to published, not draft
- 42fc84f fix: GHL payload — use accountIds array, remove locationId from body, add userId
- 0de979c fix: surface GHL 422 error response body for debugging
- 26dfa24 fix: correct GHL Social Planner API path and payload format
- 437cec6 fix: update GHL social API path and add version param to requests
- 4e0a663 feat: add /admin/test-zernio-post diagnostic for single platform debugging
- d40ed5d fix: FLUX generates text-free backgrounds, PIL overlays clean typography
- 5971787 feat: add /admin/test-replicate diagnostic endpoint for FLUX debugging
- 29f2366 fix: Zernio profile matching for Automotive Intelligence
- 992267b fix: overlay business logos on AI-generated images and carousel slides

### Features Added
- 620da72 feat: Project Paperclip — full empire build, all 5 rivers, all agents
- cb5cfe0 Add devcontainer configuration for Python project
- 2b7f03a feat: GHL media format probe — test 5 formats to find what GHL accepts
- 00b1ef1 fix: GHL media as plain URL strings, add payload logging for debugging
- 76cb4e7 fix: always include media array in GHL payload, add caption field
- 42fc84f fix: GHL payload — use accountIds array, remove locationId from body, add userId
- 437cec6 fix: update GHL social API path and add version param to requests
- 1748ef8 feat: add /admin/test-ghl diagnostic for AI Phone Guy social publishing
- 4e0a663 feat: add /admin/test-zernio-post diagnostic for single platform debugging
- 5971787 feat: add /admin/test-replicate diagnostic endpoint for FLUX debugging
- bf0f00d feat: add /admin/zernio-profiles endpoint to list all profiles and accounts
- f11128b feat: add AI creative pipeline — text-to-image, text-to-video, carousel generation

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
