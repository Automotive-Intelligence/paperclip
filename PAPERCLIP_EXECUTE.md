# PAPERCLIP_EXECUTE.md
# Project Paperclip — Full Empire Build
# Drop this into Claude Code. Build everything. Deploy to Railway.
# No placeholders. No TODOs. No questions. Build it.
# April 4, 2026

---

## MISSION
Build and deploy the complete automation stack for all five Project Paperclip rivers.
Every agent is named. Every workflow is defined. Every CRM is specified.
Prospects are being found but ZERO are enrolled in any sequence. That ends today.

---

## IRON RULES — APPLY TO ALL RIVERS
1. NEVER mention pricing in any outbound message. Ever. Pricing only comes from Michael on the call.
2. Every message is written to the OWNER — a real human being, not the business.
3. Enrollment fires the moment a contact enters the CRM. Not when they reply. IMMEDIATELY.
4. All copy is ICP-specific. A plumber gets plumber language. A lawyer gets lawyer language.
5. Hot lead alerts fire to Michael's phone within 5 minutes of trigger via Twilio.
6. All secrets via environment variables. Never hardcoded.
7. Every river logs to logs/[river]_enrollments.log

---

## AGENT ROSTER — FULL EMPIRE
### Texas names, 1979-1985. Every agent is real to their river.

### AI PHONE GUY
- Tyler — Head of Sales (Prospecting)
- Zoe — Head of Marketing (Content)
- Jennifer — Head of Client Success (Retention)
- Alex — Marketing Ops (Daily briefing)
- Sophie — AI Receptionist (Inbound voice/email/chat/DM)
- Randy — RevOps Agent (GHL Workflow Architect) NEW

### CALLING DIGITAL
- Dek — CEO Agent (Strategy)
- Marcus — Head of Sales + Biz Dev (Outreach)
- Carlos — Head of Content + Creative (AEO blog)
- Sofia — Head of Client Success (Retention)
- Nova — Implementation Director (Delivery)
- Brenda — RevOps Agent (Attio Workflow Architect) NEW

### AUTOMOTIVE INTELLIGENCE
- Michael (Meta) — CEO
- Chase — CRO (Revenue + Outreach)
- Atlas — Head of Marketing (LinkedIn + podcast)
- Ryan — Research Analyst (Dealership intelligence)
- Phoenix — Implementation Lead (Delivery)
- Darrell — RevOps Agent (HubSpot Workflow Architect) NEW

### AGENT EMPIRE
- Michael — The Host (YouTube + Skool)
- Debra — Producer Agent (Content + show notes)
- Wade — Biz Dev Agent (Sponsor outreach via Gmail)
- Tammy — Community Agent (Skool engagement)

### CUSTOMERADVOCATE
- Michael — Vision (Strategic direction)
- Clint — Technical Builder (VERA + AATA + The Exchange)
- Sherry — Web Design Agent (Consumer UI via Claude Code + Stitch 2.0)

---

## PROJECT STRUCTURE

```
paperclip/
├── main.py
├── requirements.txt
├── railway.toml
├── .env.example
├── .gitignore
├── README.md
├── SETUP.md
├── core/
│   ├── __init__.py
│   ├── notifier.py
│   ├── logger.py
│   └── scheduler.py
├── rivers/
│   ├── ai_phone_guy/
│   │   ├── workflow.py
│   │   ├── sequences.py
│   │   └── hot_leads.py
│   ├── calling_digital/
│   │   ├── workflow.py
│   │   ├── scoring.py
│   │   └── sequences.py
│   ├── automotive_intelligence/
│   │   ├── cleanup.py
│   │   ├── workflow.py
│   │   ├── deals.py
│   │   └── sequences.py
│   ├── agent_empire/
│   │   ├── workflow.py
│   │   ├── sponsor_scan.py
│   │   └── sequences.py
│   └── customer_advocate/
│       ├── vera.py
│       ├── aata.py
│       └── exchange.py
└── logs/
    └── .gitkeep
```

---

## ENVIRONMENT VARIABLES

```
GHL_API_KEY=
GHL_LOCATION_ID=
ATTIO_API_KEY=
HUBSPOT_API_KEY=
SKOOL_EMAIL=
SKOOL_PASSWORD=
TWILIO_ACCOUNT_SID=
TWILIO_AUTH_TOKEN=
TWILIO_FROM=
MICHAEL_PHONE=
BOOKING_LINK_APG=
BOOKING_LINK_CD=
BOOKING_LINK_AI=
GMAIL_MCP_URL=https://gmail.mcp.claude.com/mcp
SPONSOR_EMAIL_ALIAS=sponsors@buildagentempire.com
ANTHROPIC_API_KEY=
```

---

## RIVER 1: AI PHONE GUY
CRM: GoHighLevel
RevOps Agent: Randy
Product: Sophie AI Receptionist at $382/mo — NEVER in outreach
Demo: 15-min Zoom with Michael
Geography: DFW 380 Corridor — Prosper, Celina, Aubrey, McKinney, Frisco, Little Elm

### ICP + SEND SCHEDULE
| Tag | Vertical | Owner | Send Day | Time CST | First Channel |
|-----|----------|-------|----------|----------|---------------|
| tyler-prospect-plumber | Plumber | Mike, 47, F-150 | Tuesday | 6:00 PM | SMS |
| tyler-prospect-hvac | HVAC | Rick, 45, on rooftops | Thursday | 6:00 PM | SMS |
| tyler-prospect-roofer | Roofer | Tony, 42, done by 4pm | Wednesday | 4:30 PM | SMS |
| tyler-prospect-dental | Dental | Dr. Kim, 47 | Tuesday | 11:00 AM | SMS |
| tyler-prospect-lawyer | PI Law | James, 52, depositions | Thursday | 8:00 PM | Email |

### TRIGGER
Tyler tags contact: tyler-prospect-[vertical]
Randy monitors → auto-enrolls immediately → adds tag sequence-active

### SEQUENCE — 12 DAYS, NO PRICING, ICP-SPECIFIC COPY
```
DAY 0  — SMS  — Pain hook for their vertical
DAY 2  — EMAIL — Outcome story from similar business
DAY 5  — SMS  — "Want to hear Sophie on a real call?" + demo link
DAY 8  — EMAIL — ROI framing without numbers + booking link
DAY 12 — SMS  — Low pressure final touch
```

### PLUMBER COPY
DAY 0 SMS: "Hey [firstName] — quick question. How many calls does [businessName] miss while you're under a sink? Most plumbers don't know the number. Sophie does. [BOOKING_LINK_APG]"
DAY 2 EMAIL Subject: "The call you missed yesterday" — plumber in Celina losing 2-3 jobs/week to voicemail. Now every call answered. [BOOKING_LINK_APG]
DAY 5 SMS: "[firstName] — want to hear Sophie answer a call the way your customers would? Takes 2 minutes. [BOOKING_LINK_APG]"
DAY 8 EMAIL Subject: "One missed call a day" — do the math on what a job is worth. We solve that. [BOOKING_LINK_APG]
DAY 12 SMS: "Last one from me [firstName]. Sophie's ready when you are. [BOOKING_LINK_APG]"

### HVAC COPY
DAY 0 SMS: "Hey [firstName] — when you're up on a rooftop and a new customer calls, where does that call go? [BOOKING_LINK_APG]"
DAY 2 EMAIL Subject: "The HVAC owner who stopped losing calls on the roof" — McKinney HVAC guy. Sophie handles every call. He just shows up. [BOOKING_LINK_APG]
DAY 5 SMS: "[firstName] — 2-minute live demo of Sophie on a real HVAC call. [BOOKING_LINK_APG]"
DAY 8 EMAIL Subject: "Summer's coming. Every call counts." — DFW HVAC season is your most valuable 90 days. [BOOKING_LINK_APG]
DAY 12 SMS: "Last message [firstName]. Whenever the timing's right. [BOOKING_LINK_APG]"

### ROOFER COPY
DAY 0 SMS: "Hey [firstName] — when you're on a roof and your phone rings, what happens to that call? [BOOKING_LINK_APG]"
DAY 2 EMAIL Subject: "The roofer who stopped climbing down for calls" — Prosper roofer. Now Sophie handles it. He stays on the roof. [BOOKING_LINK_APG]
DAY 5 SMS: "[firstName] — 2-minute demo. Hear Sophie on a real roofing call. [BOOKING_LINK_APG]"
DAY 8 EMAIL Subject: "Storm season is coming" — your phone won't stop. Neither does Sophie. [BOOKING_LINK_APG]
DAY 12 SMS: "Last one [firstName]. Sophie's ready when you are. [BOOKING_LINK_APG]"

### DENTAL COPY
DAY 0 SMS: "Hey [firstName] — when you're with a patient and a new patient calls, what happens? [BOOKING_LINK_APG]"
DAY 2 EMAIL Subject: "The dental practice that stopped losing new patients to voicemail" — Frisco dentist. Sophie books appointments. [BOOKING_LINK_APG]
DAY 5 SMS: "[firstName] — hear how Sophie sounds to a new patient calling your practice? [BOOKING_LINK_APG]"
DAY 8 EMAIL Subject: "Every missed call is a missed new patient" — they don't call back. They book somewhere else. [BOOKING_LINK_APG]
DAY 12 SMS: "Last message [firstName]. [BOOKING_LINK_APG]"

### PI LAWYER COPY
DAY 0 EMAIL Subject: "The call that became a $40K case" — Allen PI attorney. Missed call in deposition. Caller signed elsewhere. Sophie prevents that. [BOOKING_LINK_APG]
DAY 2 SMS: "[firstName] — Sophie qualifies every inbound call so you only talk to real cases. [BOOKING_LINK_APG]"
DAY 5 EMAIL Subject: "What Sophie tells a caller at 9pm" — accidents don't happen at 9am. Sophie handles 24/7. [BOOKING_LINK_APG]
DAY 8 SMS: "[firstName] — 15 minutes to see it live. [BOOKING_LINK_APG]"
DAY 12 EMAIL Subject: "Signing off — for now" — last message. Offer stands. [BOOKING_LINK_APG]

### HOT LEAD ESCALATION
SMS reply OR 3+ email opens → tag hot-lead → Randy sends Twilio SMS to Michael:
"HOT LEAD: [firstName] [lastName] at [business] just [action]. Call them now. [phone]"
Pause sequence immediately.

### RANDY'S SCHEDULE: Every 4 hours

---

## RIVER 2: CALLING DIGITAL
CRM: Attio
RevOps Agent: Brenda
Data: OwnerPhones.com — 200 contacts arriving within 24hrs at michael@calling.digital
4 CSVs: Med Spa 50, PI Attorney 50, Real Estate Team 50, Custom Home Builder 50
Pricing: $2,500/mo Growth Retainer · $5K-$8K/mo Full Stack Partner — NEVER in outreach

### ICP + SEND SCHEDULE
| Vertical | Owner | Send Day | Time CST |
|----------|-------|----------|----------|
| Med Spa | Sarah, 38, NP-turned-entrepreneur | Wednesday | 7:00 PM |
| PI Law Firm | James, 52, solo firm | Wednesday | 7:00 PM |
| Real Estate Team | Derek, 44, team leader | Tuesday | 8:00 AM |
| Custom Home Builder | Tony, 50, referral-dependent | Monday | 6:30 AM |

### OWNERPHONES CSV IMPORT ON ARRIVAL
1. Download all four CSVs from michael@calling.digital
2. Import each into Attio with vertical tag (med-spa, pi-law, real-estate, home-builder)
3. Brenda scores on entry
4. Enroll Track A or Track B

### ICP SCORING (Brenda)
+3 North Texas/DFW business
+3 Target vertical match
+2 Revenue over $1M
+2 Expressed AI interest
+2 Referred by client
+1 Engaged with content
-2 Outside Texas, no referral
Score 7+ = Track B. Under 7 = Track A.

### TRACK A — COLD (score under 7)
DAY 1  EMAIL: "What AI actually does for a [industry] business in DFW" — education only, no pitch
DAY 4  EMAIL: "Real numbers from a North Texas client" — case study, no pitch
DAY 8  EMAIL: "The real cost of waiting on AI" — soft urgency
DAY 14 EMAIL: "One last thing before I go" — free 30-min AI audit offer [BOOKING_LINK_CD]

### TRACK B — WARM (score 7+)
DAY 1  EMAIL: "Here's exactly what we'd build for [businessName]" — specific, personalized, 3 workflows
DAY 3  EMAIL: "A case study from your industry" — specific numbers
DAY 6  EMAIL: "Ready to show you the full picture?" — proposal preview + [BOOKING_LINK_CD]
DAY 10 EMAIL: Personal follow-up from Marcus. Direct ask. Last touch.

### HOT LEAD: Track B Day 6 open + click → flag Marcus within 2 hours
### BRENDA'S SCHEDULE: Every 2 hours

---

## RIVER 3: AUTOMOTIVE INTELLIGENCE
CRM: HubSpot
RevOps Agent: Darrell
Current contacts: 690 (mixed — dealers, podcast guests, vendors — MUST CLEAN FIRST)
Pricing: Free Mini Audit → $997-$2,500 Full Audit → $5K-$8K/mo Retainer

### STEP 1: RUN CLEANUP SCRIPT FIRST (cleanup.py)
Export all 690 HubSpot contacts via API.
Add custom property "Contact Type" with values:
  - Dealership Decision Maker
  - Podcast Guest
  - Vendor Partner
  - Unclassified

Auto-classify logic:
  - Email domain contains dealership brand (toyota, ford, honda, etc.) → Dealership Decision Maker
  - Company name contains (Auto/Motors/Toyota/Ford/Chevy/Honda/Nissan/BMW/Mercedes/Volvo/Mazda/Hyundai/Kia/Dodge/Jeep/Dealer/Group) → Dealership Decision Maker
  - All others → Unclassified

Create saved HubSpot view: "Chase — Verified Dealers"
Log all classifications to logs/ai_cleanup.log

NOTE: Job Title field is dirty — many contacts have car brand as job title (Volvo, Nissan, etc.)
DO NOT use Job Title for filtering. Use email domain and company name only.

### STEP 2: DEAL CREATION + SEQUENCE
On classification as Dealership Decision Maker:
Darrell creates HubSpot deal at Stage 1 "Qualified Lead"
Fires 5-email sequence within 1 hour

### EMAIL SEQUENCE — INSIDER TONE, NO VENDOR PITCH
EMAIL 1 Day 0 Subject: "20 years on your side of the desk"
Michael's story. Sold 500+ cars. Desk manager experience.
"I know what your BDC is actually doing at 6pm on a Friday.
I'm not here to sell you software — I'm here to show you what's possible
when the tools actually understand how a dealership works."

EMAIL 2 Day 3 Subject: "Where does your dealership actually stand on AI?"
Free AI Readiness Mini Audit. 30 minutes. Score across 5 pillars.
"I'll tell you exactly where the gap is and what it's costing you per month."
[BOOKING_LINK_AI]

EMAIL 3 Day 7 Subject: "The dealer two towns over just deployed this"
Competitive pressure. AI adoption stats in automotive.
"The dealers who move first own the zip code."

EMAIL 4 Day 10 Subject: "The 5 pillars we look at in every dealership audit"
Educational. Lead Intelligence, Personalization, Sales Automation,
Revenue Optimization, Customer Lifecycle. Positions Michael as expert.

EMAIL 5 Day 14 Subject: "Signing off — for now"
Genuine breakup email. Offer stands. No pressure.
If 3+ opens → tag hot-lead → alert Michael immediately via Twilio.

### DARRELL'S SCHEDULE: Every 1 hour

---

## RIVER 4: AGENT EMPIRE
Platform: Skool (LIVE) + YouTube (starts tomorrow) + Ghost blog
Domain: buildagentempire.com
Sponsor email: sponsors@buildagentempire.com via Gmail MCP
Revenue targets: $97/mo students (51 target) · $5K premium sponsor · $3K mid-tier sponsor
Brand: Build-in-public. Faith-centered. Raw and honest.

### AGENT: DEBRA — Producer
Job: Turns VS Code logs and build sessions into content
Weekly output: 6 video outlines, 6 show notes, 1 Ghost blog post, thumbnail copy
Day 1 action: Read all VS Code chat logs in repo → generate first 30-day content calendar

### AGENT: WADE — Biz Dev (Sponsor Outreach)
Job: Pitches sponsors via sponsors@buildagentempire.com through Gmail MCP
Weekly output: 5 personalized sponsor pitch emails

Step 1 — Scan repo (sponsor_scan.py):
Scan entire paperclip codebase.
Extract every tool, API, library, service used:
  - All imports in .py files
  - All packages in requirements.txt
  - All API keys in .env.example
  - All services mentioned in comments
Build prioritized sponsor target list from actual tools in use.

Pitch template Wade sends:
Subject: "Agent Empire — we build with [TOOL] live on YouTube"
"I run Agent Empire — a build-in-public community documenting building 5 AI businesses.
We use [TOOL] in every build and film it live. Our students are [TOOL]'s exact customer —
builders and founders deploying AI agents for the first time.
I'd love to explore a founding sponsor partnership. 15 minutes this week?"
Michael Rodriguez · buildagentempire.com

Sponsor tiers:
- Premium $5,000/mo: video feature, integration tutorial, Skool placement
- Mid-tier $3,000/mo: video mention, Skool placement

### AGENT: TAMMY — Community
Job: Keeps Skool warm between Michael's live sessions
Weekly output: Welcome DM to every new member within 1 hour, 1 daily post, respond to all questions within 4 hours

Welcome sequence:
IMMEDIATE DM: "Hey [name] — welcome to Agent Empire. Building 5 AI businesses in public
and documenting every win and failure. Start here: [pinned post]. Ask anything."
DAY 3 DM: "Quick check-in — watched the first build video? Here's where most start: [YouTube link]"
DAY 7 DM: "One week in — here's what paid members are working on: [teaser]. Trial is free 7 days: [trial link]"
DAY 6 OF TRIAL: "Trial ends tomorrow. Here's what you'd lose: [list]. Keep going: [upgrade link]"

### TAMMY'S SCHEDULE: Every 6 hours
### WADE'S SCHEDULE: 5 emails per week, Monday 9am

---

## RIVER 5: CUSTOMERADVOCATE
Partners: Michael Rodriguez + Jose Puente (advisor, 12 yrs AutoTrader/Cox)
Go-to-market: B2C first — own the buyer, dealers follow
Architecture: VERA + AATA + The Exchange

### THREE LAYERS
VERA — Consumer Buyer Agent
Collects behavioral signals (not self-reported preferences)
Scores across 6 dimensions
Assigns negotiation profile
Knows buyer's real walk-away threshold before they do

AATA — Negotiation Protocol
Tamper-proof session between buyer agent and dealer agent
SSL for car deals
Neither side can read the other's threshold

The Exchange — Network Infrastructure
Platform between all buyer agents and all dealer agents
Visa between cardholders and merchants — the long game

### AGENT: CLINT — Technical Builder (Claude Code)
Day 1 action:
1. Pull both Jose Puente Fellow AI transcripts from Fellow AI MCP
2. Extract all product decisions from those two sessions
3. Build VERA behavioral scoring engine based on transcript direction
4. Begin AATA protocol architecture

### AGENT: SHERRY — Web Design (Claude Code + Stitch 2.0)
Consumer-facing UI for car buyers
Design: Simple, trustworthy, consumer-grade — built for the buyer not the dealer
Entry: "Let us help you buy your next car"
Flow: Behavioral intake → VERA scoring → negotiation profile assigned

### BUILD ORDER
1. Pull Jose transcripts → extract decisions
2. Clint builds VERA scoring engine
3. Sherry builds consumer intake UI
4. AATA protocol (Phase 2)
5. The Exchange infrastructure (Phase 3)

---

## SHARED INFRASTRUCTURE

### core/notifier.py — Twilio SMS to Michael
notify_hot_lead(river, contact_name, business, phone, action)
notify_error(river, error_message)
notify_daily_summary(stats_dict) — 8am every day

### core/logger.py — Unified logging
log_enrollment(river, contact_id, contact_name, track)
log_sequence_event(river, contact_id, event_type, step)
log_hot_lead(river, contact_id, trigger)

### core/scheduler.py — APScheduler
Randy (GHL): every 4 hours
Brenda (Attio): every 2 hours
Darrell (HubSpot): every 1 hour
Tammy (Skool): every 6 hours
Wade (Gmail): Monday 9am, 5 emails
Daily 8am summary to Michael via Twilio

### main.py
Start all schedulers
Run initial enrollment pass on startup
Health endpoint port 8000 for Railway
Log empire status on startup

---

## REQUIREMENTS
```
requests==2.31.0
python-dotenv==1.0.0
apscheduler==3.10.4
twilio==8.10.0
fastapi==0.109.0
uvicorn==0.27.0
hubspot-api-client==8.2.0
anthropic==0.25.0
crewai==0.28.0
google-auth==2.29.0
google-auth-oauthlib==1.2.0
```

---

## RAILWAY DEPLOYMENT
```toml
[build]
builder = "nixpacks"

[deploy]
startCommand = "python main.py"
restartPolicyType = "on-failure"
restartPolicyMaxRetries = 3
```

---

## CHANGELOG SYSTEM
Every Friday 5pm: python paperclip/changelog_gen.py
Output: CHANGELOG_WEEK_[N]_2026.md
Contents: contacts enrolled, replies, hot leads, revenue impact, bugs fixed, next week
Post to Agent Empire Skool every Saturday. This IS the build-in-public content.

---

## EXECUTION ORDER
1. Create full project structure
2. Run HubSpot cleanup script FIRST — 690 contacts need classification
3. Build core/ infrastructure
4. Build Randy — GHL/AI Phone Guy (43 open opps — highest priority)
5. Build Brenda — Attio/Calling Digital
6. Build Darrell — HubSpot/Automotive Intelligence
7. Build Tammy — Skool/Agent Empire
8. Build Debra — Content producer/Agent Empire
9. Scan repo → build Wade sponsor list → wire Gmail MCP
10. Pull Jose transcripts → build Clint + Sherry/CustomerAdvocate
11. Deploy to Railway
12. Output SETUP.md with exact API key instructions

---

## THE NORTH STAR
$15,000 MRR across all five rivers.
Michael shows up to close. Agents do everything else.
Funded by faith. Built for freedom.
