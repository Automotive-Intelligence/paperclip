# Project Paperclip — Setup Guide

## Quick Start

1. Clone the repo
2. Copy `.env.example` to `.env`
3. Fill in your API keys (see below)
4. Deploy to Railway or run locally with `python main.py`

## API Keys — Where to Get Each One

### GoHighLevel (AI Phone Guy — Randy)
```
GHL_API_KEY=         → Settings > Business Profile > API Key (or Marketplace app if using OAuth)
GHL_LOCATION_ID=     → Settings > Business Profile > Location ID (starts with "loc_")
```
**Where:** https://app.gohighlevel.com → Settings > Business Profile

### Attio (Calling Digital — Brenda)
```
ATTIO_API_KEY=       → Settings > Developers > API Keys > Create new key
```
**Where:** https://app.attio.com → Settings > Developers

### HubSpot (Automotive Intelligence — Darrell)
```
HUBSPOT_API_KEY=     → Settings > Integrations > Private Apps > Create private app
```
**Where:** https://app.hubspot.com → Settings > Integrations > Private Apps
**Scopes needed:** crm.objects.contacts.read, crm.objects.contacts.write, crm.objects.deals.read, crm.objects.deals.write, crm.schemas.contacts.write

### Twilio (Hot Lead Alerts to Michael)
```
TWILIO_ACCOUNT_SID=  → Dashboard > Account SID
TWILIO_AUTH_TOKEN=    → Dashboard > Auth Token
TWILIO_FROM=         → Phone Numbers > your Twilio number (format: +1XXXXXXXXXX)
MICHAEL_PHONE=       → Michael's cell (format: +1XXXXXXXXXX)
```
**Where:** https://console.twilio.com

### Booking Links
```
BOOKING_LINK_APG=    → Your Calendly/Cal.com link for AI Phone Guy demos
BOOKING_LINK_CD=     → Your Calendly/Cal.com link for Calling Digital audits
BOOKING_LINK_AI=     → Your Calendly/Cal.com link for Automotive Intelligence audits
```

### Skool (Agent Empire — Tammy)
```
SKOOL_EMAIL=         → Your Skool login email
SKOOL_PASSWORD=      → Your Skool password
```

### Gmail MCP (Agent Empire — Wade)
```
GMAIL_MCP_URL=https://gmail.mcp.claude.com/mcp
SPONSOR_EMAIL_ALIAS=sponsors@buildagentempire.com
```

### Claude / Anthropic
```
ANTHROPIC_API_KEY=   → https://console.anthropic.com → API Keys
```

## Railway Deployment

1. Push to GitHub
2. Connect repo in Railway dashboard
3. Add all environment variables above in Railway > Variables
4. Railway auto-detects `railway.toml` and deploys
5. Health check at `/health` confirms all rivers are active

## Manual Agent Triggers (API)

```bash
# Trigger any agent manually
curl -X POST https://your-app.railway.app/run/randy
curl -X POST https://your-app.railway.app/run/brenda
curl -X POST https://your-app.railway.app/run/darrell
curl -X POST https://your-app.railway.app/run/tammy
curl -X POST https://your-app.railway.app/run/wade

# Re-run HubSpot cleanup
curl -X POST https://your-app.railway.app/cleanup/hubspot

# Check health
curl https://your-app.railway.app/health
```

## Execution Order (What Happens on Deploy)

1. HubSpot cleanup runs first — classifies all 690 contacts
2. Initial enrollment pass — Randy, Brenda, Darrell, Tammy all do first run
3. Scheduler starts — all agents on their cadence
4. Health endpoint live on port 8000

## Agent Schedule

| Agent | River | CRM | Frequency |
|-------|-------|-----|-----------|
| Randy | AI Phone Guy | GoHighLevel | Every 4 hours |
| Brenda | Calling Digital | Attio | Every 2 hours |
| Darrell | Automotive Intelligence | HubSpot | Every 1 hour |
| Tammy | Agent Empire | Skool | Every 6 hours |
| Wade | Agent Empire | Gmail MCP | Monday 9am CST |
| Daily Summary | All | Twilio | 8am CST daily |
