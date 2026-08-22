# Local Directory Footprint — AIPG + WD (fill-and-go packet)

**Date:** 2026-08-19 · **Feeds:** Local Win spec (`2026-08-18-local-win-aipg-wd.md`) Phase 3 · **Owner of
submission:** CRO / Michael (accounts + claims) · **Everything else staged below.**

Why: the local-flank probe showed AI cites **local directories + review sites** for city-level queries
(Yelp on "roofers McKinney", BBB on "marketing Prosper", Birdeye/reviews on plumber queries). Getting
*listed* on the right ones is the fast, flanking path — cheaper than out-ranking the nationals.

## Canonical NAP — use IDENTICAL text on every listing (this is the whole point)
Inconsistent Name/Address/Phone across directories kills local trust signals. Lock one string per brand:
- **The AI Phone Guy** · Phone: **(817) 670-9689** · Web: **https://theaiphoneguy.com** · Address: `______`
- **Worship Digital** · Phone: **(817) 662-2473** · Web: **https://worshipdigital.co** · Address: `______`

⚠️ **Address dependency (same as GBP):** must be the AVO LLC real commercial/home address, **NOT the
Anytime Mailbox CMRA** — Yelp/BBB/GBP all reject or flag CMRAs. Both brands list as **service-area /
by-appointment** (hide street address, show service cities). Blocked until the AVO LLC address is set.

## Directory hit-list (priority order)

### Both brands — universal, free, high-citation
1. **Google Business Profile** (staged separately — the anchor)
2. **Bing Places** — feeds Copilot; import from GBP once live
3. **Apple Business Connect** — feeds Siri/Apple Maps, free
4. **Facebook Page** (both have legacy pages in the social registry — claim/clean, don't duplicate)
5. **Nextdoor Business** — hyper-local, cited for "near me"; free
6. **BBB** — appeared in the WD Prosper answer; paid accreditation optional, a free listing still helps

### AIPG — home-services surfaces
7. **Yelp for Business** (appeared on roofers-McKinney) — category: *Telephone answering service*
8. **Angi** + **Thumbtack** — home-services intent, where trades and their tools get discovered
9. **Local chamber of commerce** (Prosper / Frisco / McKinney) — real local citation + backlink
10. Category descriptor everywhere: **"answering service for [trade]"**, NOT "AI receptionist" (the probe
    showed "AI receptionist" summons Smith.ai even locally)

### WD — agency directories (these OWN the "agency near me" answers)
7. **Clutch.co** (cited repeatedly) — free profile, drives "best agency" citations
8. **Expertise.com** + **DesignRush** + **UpCity** — agency directories AI pulls from
9. **380guide.com** — the hyper-local 380-corridor directory that appeared for "marketing agency 380 corridor"
10. **Semrush Agency Partners** directory (appeared on "local SEO agency Frisco")

## Profile content
Reuse the descriptions + categories + service-area cities already written in
`2026-08-18-dba-gbp-turnkey-packet.md` (they're directory-agnostic and on-voice, no em-dashes, no
fabricated claims). Per-directory only the field layout changes.

**Hard rule (Zoe lesson):** zero fabricated reviews, ratings, client names, or metrics on any listing.
Pre-revenue means bare profiles at first — that's fine and honest; reviews come after real clients. A
claimed, complete, consistent profile still gets cited; a fake review gets the account banned.

## Sequence
1. Set the AVO LLC address (unblocks NAP) → 2. Claim GBP (anchor) → 3. Push identical NAP to Bing/Apple/
Nextdoor/Yelp/BBB/Facebook → 4. WD: claim Clutch/Expertise/DesignRush/380guide → 5. verify NAP matches
across all (Polaris audits) → 6. re-run `tools/growth/local_citation_probe.py`, watch the open-field city
queries flip toward AIPG/WD.
