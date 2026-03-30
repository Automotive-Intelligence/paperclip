# PHASE_ROADMAP.md

## Current State — Phase 1

### Email Outreach Decision — Phase 1

We are NOT using Resend for outbound prospecting or outreach email at this time.

All outreach email fires through the connected CRM per business:

- The AI Phone Guy → GoHighLevel
  Outreach sequences built and managed in GHL
  Workflows trigger automatically when prospect is added with correct tag

- Calling Digital → Attio CRM
  Outreach managed through Attio sequences
  Prospects pushed to Attio by Marcus agent

- Automotive Intelligence → HubSpot
  Outreach sequences built in HubSpot
  Workflows trigger when Ryan Data pushes a new prospect contact

Resend is verified for theaiphoneguy.ai and callingdigital.com domains but is NOT active in the codebase at this time.

Resend for automotiveintelligence.io had DNS conflicts with Cloudflare — conflicts deleted March 2026 — pending re-verification when needed.

## Future Need — Phase 2/3

Resend will be activated when AIBOS requires agents to send email programmatically outside of CRM workflows. At that point:

- RESEND_API_KEY added to Railway variables
- tools/email.py created with send_email() function
- All three domains fully verified in Resend
- Agent email tools wired per business

Do not build Resend integration until Phase 2 is confirmed ready to begin.
