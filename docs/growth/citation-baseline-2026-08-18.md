# AEO Citation Baseline — 2026-08-18 (Phase-0 anchor)

First real measurement of AI-answer citation across the portfolio. This is the **month-over-month
starting line** for the authority program. Re-run monthly and diff.

**How to re-run:** `cd ~/paperclip && doppler run -- python tools/growth/citation_baseline.py`
(Perplexity Sonar via OpenRouter; key = `OPENROUTER_API_KEY` from Doppler. Local-flank variant:
`tools/growth/local_citation_probe.py`.) Total spend both runs: **$0.15** (exact, from OpenRouter usage.cost).

## Head-term baseline — cited in 0 / 18 queries
Every brand, zero citations. Who owns the answers instead (the displacement targets):

| Brand | AI cites instead |
|---|---|
| Worship Digital | BrightLocal, Wikipedia, Semrush-agencies, Clutch, HigherVisibility, Mailchimp |
| The AI Phone Guy | Dialzara, Smith.ai, AnswerForce, AnswerNet, Podium, Reddit |
| Automotive Intelligence | Fullpath (3×), Impel, Matador, DealerAI, Cox Auto, BCG |
| Agent Empire | HuggingFace, IBM, Coursera, Forbes, Medium/LinkedIn, aibuilderclub |
| Bookd | AgencyBloc (2×), Creatio (2×), Decerto, agent-crm, HubSpot, GetApp |

Patterns: directories dominate (Clutch, GetApp, Semrush-agency lists); Reddit/forums appear; specific
competitors own verticals (Fullpath = dealership-AI, AgencyBloc/Creatio = insurance-CRM).

## Local-flank probe (AIPG + WD) — the whitespace is real
Half the city-level queries are OPEN FIELD; the "contested" ones are lost to **directories**, not competitors.

**AIPG** — local answers cite *local service companies + review sites*, not the SaaS giants:
- 🟢 OPEN: "answering service for plumbers in Frisco", "24/7 call answering for electricians in Plano"
- 🟡 soft: "HVAC answering service Prosper" (local HVAC cos + AnswerPro), "roofers McKinney" (+ Yelp)
- 🔴 the "**AI receptionist**" framing pulls Smith.ai in even locally ("AI receptionist HVAC Denton")
- **Lesson:** frame as "answering service for [trade] in [city]", NOT "AI receptionist". Answers cite
  local entities + Yelp/Birdeye → AIPG must read as a credible LOCAL entity (GBP + reviews).

**WD** — the wall is directories, not agencies:
- 🟢 OPEN: "digital marketing agency Prosper", "web design small business Celina"
- 🟡 local directory appears: "marketing agency 380 corridor" → 380guide.com + Clutch
- 🔴 directories own it: "local SEO agency Frisco", "SEO company McKinney" → Clutch, Expertise, DesignRush, Semrush-agencies
- **Lesson:** smaller city = more open. Contested queries are won by getting LISTED on the cited
  directories, not out-ranking them.

**Verdict:** the flank is winnable, and it does NOT require microsites — the whitespace is on the brand
domain + third-party directories. See `2026-08-18-local-win-aipg-wd.md`.
