# Digital Presence Audit — Sutherlin Kia Huntington Beach

**Prepared by:** Automotive Intelligence
**Property audited:** https://www.sutherlinkia.com/
**Dealership:** Sutherlin Kia Huntington Beach — 18835 Beach Blvd, Huntington Beach, CA 92648
**Sales:** (714) 274-4632  •  **Service:** (714) 274-1000
**Audit date:** August 8, 2026
**Auditor framework:** AI house SEO/CRO/reputation audit standard (`.agents/skills/seo-audit`, `.agents/MARKETING-AUDIT.md`)

---

## 0. How to read this report

This is a "top-agency" teardown: every finding follows **Issue → Evidence → Impact → Fix → Priority (P0–P3)**. It is written to be handed directly to the dealer principal or GM.

**Methodology & scope note (important, and to our credit):** The client's live origin (`sutherlinkia.com` and the legacy `orangecountykia.com`) sits behind an egress policy that blocked our automated crawler during this pass, so the technical-render checks (PageSpeed/Core Web Vitals, JS-injected schema, full tag-manager inventory, form-handler inspection) are marked **[VERIFY ON-SITE]** and should be confirmed with a rendered crawl (Screaming Frog + PageSpeed Insights + Rich Results Test). Everything else here is built from live SERP data, the major review/citation platforms, the observable domain and URL architecture, industry M&A reporting, and the platform's public URL fingerprints. The headline findings do **not** depend on the blocked crawl — they are visible from outside the firewall, which is exactly how Google and your shoppers see you.

---

## 1. Executive summary

Sutherlin Kia Huntington Beach is a **brand-new storefront sitting on top of a decade-old dealership** — and right now the digital footprint reflects the seams of that transition, not the strength of the store underneath it.

Here is the situation in one paragraph: Sutherlin Automotive Group (family-owned, CEO Brett Sutherlin, ~50 years in the business) entered California in September 2025 by acquiring McKenna Subaru in Huntington Beach, then picked up the Kia store next door — **the former Car Pros Kia Huntington Beach at 18835 Beach Blvd** — and rebranded it "Sutherlin Kia." The physical store changed hands cleanly. **The digital identity did not migrate with it.** The result is a classic post-acquisition digital-debt situation, and it is costing the store money every day it stands.

### The one finding that matters most

> **A decade of reputation, ranking authority, and review equity is stranded under the old "Car Pros Kia" brand — while the new "Sutherlin Kia" brand and its new domain start from near-zero. Two live domains for one store are splitting whatever authority remains.** Fix the migration and you inherit ten years of trust for free. Leave it and you rebuild from scratch while competitors keep theirs.

### Digital Health Scorecard

| Pillar | Grade | One-line reason |
|---|:---:|---|
| **Brand & Domain Migration** | 🔴 **F** | Two live domains, split authority, no visible consolidation strategy |
| **Local SEO & Google Business Profile** | 🔴 **D–** | Review/citation equity trapped under the retired "Car Pros Kia" name |
| **Reputation / Reviews** | 🟠 **C** | Strong legacy volume (1,700+ Yelp) — but under the wrong brand, not carried forward |
| **Technical SEO** | 🟠 **C–** | Legacy Cox/Dealer.com-style build; duplicate-domain & canonical risk [VERIFY ON-SITE] |
| **Website UX & Conversion (CRO)** | 🟡 **C+** | Standard OEM-compliant template; conversion depends on lead-path hygiene [VERIFY ON-SITE] |
| **Paid Media & Analytics** | ⚪ **Inconclusive** | Attribution almost certainly fragmented across two domains and a rebrand |
| **AI / Answer-Engine Readiness (AEO)** | 🔴 **D** | Inconsistent NAP + brand ambiguity make the store hard for ChatGPT/AI Overviews to cite correctly |

**Overall: 🔴 High-risk transition — high upside if addressed in the next 60 days.**

### Top 5 priorities (the whole report in five lines)

1. **P0 — Consolidate to one domain.** Decide the canonical home (`sutherlinkia.com`), then 301-redirect every URL on `orangecountykia.com` to its match. Stop splitting authority. *(§3)*
2. **P0 — Migrate the Google Business Profile in place, don't recreate it.** Rename the *existing* Car Pros Kia GBP → "Sutherlin Kia Huntington Beach" so all ~decade of reviews and ranking history transfer. Never create a new listing. *(§4)*
3. **P0 — Fix NAP consistency across the web.** The name is "Car Pros Kia" on Yelp/Cars.com/CARFAX/KBB and "Sutherlin Kia" on the site. Reconcile the top 20 citations. *(§4)*
4. **P1 — Publish a reputation bridge.** Explicitly connect the two brands ("Car Pros Kia is now Sutherlin Kia") on-site and on the GBP so shoppers and Google understand it's the same trusted store. *(§5)*
5. **P1 — Instrument the funnel & make it AI-citable.** Rebuild analytics/attribution on the surviving domain and add LocalBusiness/AutoDealer schema so AI answer engines cite you correctly. *(§6, §8)*

---

## 2. Business & competitive context

**Who you are now.** Sutherlin Automotive Group's westward expansion is real and well-capitalized — the McKenna Subaru deal was reported as one of the largest single-point Subaru transactions to date, with stated plans to build one of the largest Subaru operations in the U.S. You are not a struggling store; you are a strong operator wearing a new jersey the digital world hasn't caught up to.

**The block you're on (Beach Blvd "Mile of Cars"):**

| Store | Location | Brand posture | Review footprint (public) |
|---|---|---|---|
| **Sutherlin Kia HB** (you) | 18835 Beach Blvd | New brand, ex–Car Pros Kia | Equity stranded under old name |
| **Sutherlin Subaru HB** (sister store) | 18801 Beach Blvd | Ex–McKenna Subaru | ~4.7–4.8★, 100–700+ reviews across platforms |
| **Garden Grove Kia** | 13731 Harbor Blvd | Independent competitor | Weaker footprint (~3.7★ on one source) |
| **Kia of Cerritos / Kia of Irvine** | Cerritos / Irvine | Regional Kia competitors | Established, actively marketed |

**Strategic read:** Your nearest same-brand competitors are *not* dominant online. The store you rebranded (Car Pros Kia) built a genuinely large review base — **1,700+ Yelp reviews and 460+ on Cars.com**, KBB dealer ratings in the 4.3–4.7 range. That is a moat. Today that moat is protecting a brand name you no longer use. The entire game is to *inherit* it, not abandon it.

---

## 3. Brand & Domain Migration — 🔴 the critical failure

### 3.1 Two live domains for one store

- **Issue:** Both `sutherlinkia.com` (new) and `orangecountykia.com` (legacy Car Pros domain) resolve as live dealership sites. The legacy domain even renders the *new* title tag ("...Sutherlin Kia Huntington Beach") while its body content and service pages still say "Car Pros Kia." One store, two front doors.
- **Evidence:** Google indexes both hosts for the same store; `orangecountykia.com/` shows the Sutherlin title, and its interior pages (`/promotions/index.htm`, `/service/center.htm`, `/used-inventory/index.htm`) still carry Car Pros branding and the same 18835 Beach Blvd address / (714) 274-4632 phone.
- **Impact:** **High.** Google splits link authority, crawl budget, and ranking signals across two domains covering identical inventory → neither ranks as well as one consolidated property would. Shoppers who land on the legacy site see a defunct brand and mixed signals. PPC/retargeting pixels and analytics are almost certainly fragmented across both.
- **Fix:**
  1. Choose the **single canonical domain** — recommend `sutherlinkia.com` (matches the go-forward brand).
  2. **301-redirect every `orangecountykia.com` URL** to its exact equivalent on `sutherlinkia.com` (page-to-page, not a blanket redirect to home — page-to-home mapping loses the deep-link equity that matters most).
  3. Keep the legacy domain registered and pointed via redirect for 12+ months so external backlinks keep flowing value.
  4. Verify both domains in Google Search Console and use **Change of Address** where applicable.
- **Priority:** **P0.**

### 3.2 No visible "we've rebranded" bridge

- **Issue:** Nothing publicly connects "Car Pros Kia" ↔ "Sutherlin Kia." A returning customer Googling "Car Pros Kia Huntington Beach" needs to instantly understand it's the same store, same building, same service department.
- **Impact:** **High** on returning-customer retention and service-drive traffic — the most profitable traffic a dealer has.
- **Fix:** Add a persistent "Car Pros Kia is now Sutherlin Kia — same team, same location, new name" banner/notice on-site, in the GBP description, and in a short press/FAQ page. (Also protects branded search: people will search the old name for years.)
- **Priority:** **P1.**

---

## 4. Local SEO, Google Business Profile & Citations — 🔴 D–

For a single-rooftop dealer, **the Google Business Profile is the single highest-ROI digital asset** — it drives the map pack, "Kia dealer near me," and directions/calls. This is where the migration damage is most expensive.

### 4.1 Do NOT recreate the GBP — rename the existing one

- **Issue:** The risk in every dealership rebrand is that someone creates a *brand-new* Google Business Profile for "Sutherlin Kia" and orphans the decade-old Car Pros Kia listing with all its reviews and local ranking history.
- **Impact:** **Severe.** A new listing starts at zero reviews and zero local authority; Google may also flag a duplicate at the same address and suppress both.
- **Fix:** In the **existing** Car Pros Kia GBP, edit the business *name* to "Sutherlin Kia Huntington Beach," update the website URL to the canonical domain, refresh photos/hours/description. Google preserves reviews and ranking history through a name change on the same profile. If a duplicate "Sutherlin Kia" listing already exists, merge/remove it — do not run both.
- **Priority:** **P0.** *(Verify current GBP state first.)*

### 4.2 NAP (Name/Address/Phone) inconsistency across the web

- **Issue:** The store's **name is inconsistent** across the citation graph: "Car Pros Kia Huntington Beach" on Yelp (1,700+ reviews), Cars.com (460+), CARFAX, KBB, DealerRater, CarGurus — vs. "Sutherlin Kia Huntington Beach" on the new site. Address (18835 Beach Blvd) and phones are consistent, which is good — but the name mismatch alone confuses Google's entity resolution.
- **Impact:** **High** on map-pack ranking and on AI answer engines (see §8).
- **Fix:** Run a citation cleanup across the top 20 directories (Yelp, Cars.com, CARFAX, KBB, DealerRater, CarGurus, Edmunds, Apple Maps, Bing Places, Kia's OEM dealer locator, chamber, BBB). Prioritize the OEM locator and the big-4 auto shopping sites. A tool like Yext/BrightLocal accelerates this, but the OEM locator and Google/Bing/Apple should be done by hand.
- **Priority:** **P0–P1.**

### 4.3 Kia OEM locator & co-op alignment

- **Issue:** Kia's franchise site locator and co-op/OEM digital programs must point to the correct name + canonical domain, or you lose OEM-referred traffic and risk co-op compliance issues.
- **Fix:** Update the Kia dealer locator listing and confirm OEM digital-advertising co-op is pointed at the surviving domain.
- **Priority:** **P1.**

---

## 5. Reputation & Reviews — 🟠 C (great asset, wrong label)

- **Strength:** The store carries **serious review volume** — 1,700+ on Yelp, 460+ on Cars.com, solid KBB dealer ratings. Very few competitors on this stretch of Beach Blvd have that. This is your biggest latent advantage.
- **Issue:** All of it lives under "Car Pros Kia." Under the new "Sutherlin Kia" brand, a shopper doing fresh due diligence sees a near-empty review history and may assume you're brand-new and unproven.
- **Impact:** **High** on conversion — reviews are the #1 trust factor in auto retail. New-brand-with-no-reviews suppresses close rates on exactly the high-intent shoppers you want.
- **Fix:**
  1. Preserve the legacy reviews by renaming the *existing* profiles (GBP §4.1; same on Yelp/Cars.com where the platform allows a name update rather than a new page).
  2. Launch an **aggressive review-generation cadence** under the new brand immediately — SMS/email review requests at every sale and RO close — to build "Sutherlin Kia" volume fast and blend old + new.
  3. Add a **review-schema-backed testimonials strip** on-site and connect Google reviews so the star rating is visible on the homepage and VDP paths.
  4. Set up review monitoring/response SLA (respond to every review within 24–48h — response rate itself is a ranking and trust signal).
- **Priority:** **P1.**

---

## 6. Website, Technical SEO & Platform — 🟠 C–

- **Platform read:** The URL fingerprint (`/new-inventory/index.htm`, `/promotions/index.htm`, `/service/center.htm`, `/used-inventory/index.htm`) is the classic **Cox Automotive Dealer.com / DealerOn-family** legacy pattern. It's OEM-compliant and functional, but templated and shared by thousands of stores — differentiation and technical performance depend on configuration, not the box. **[VERIFY ON-SITE which vendor + current package.]**
- **Findings (external-observable + to-verify):**

| # | Finding | Impact | Priority |
|---|---|---|:---:|
| 6.1 | **Duplicate content across two domains** (same inventory, two hosts) — canonical tags likely not cross-pointing | High | P0 |
| 6.2 | **Canonical / self-reference hygiene** — confirm every VDP/SRP self-canonicals and legacy domain canonicals point to the survivor `[VERIFY]` | High | P0 |
| 6.3 | **Title/meta consistency** — legacy domain already shows new brand in title but old brand in body; sweep all templates for brand strings | Medium | P1 |
| 6.4 | **Core Web Vitals / PageSpeed** — dealer templates are image- and third-party-script-heavy; LCP/INP frequently fail on mobile `[VERIFY via PageSpeed Insights]` | Medium–High | P1 |
| 6.5 | **Structured data** — LocalBusiness/AutoDealer, Vehicle, and Review schema must carry the *new* name + canonical URL; JS-injected schema not visible externally `[VERIFY via Rich Results Test]` | High (SEO + AEO) | P1 |
| 6.6 | **XML sitemap + robots** — ensure the surviving domain's sitemap is clean/submitted and the legacy domain isn't still feeding Google its own sitemap | Medium | P1 |
| 6.7 | **HTTPS/SSL & mixed content** across both domains post-redirect | Medium | P2 |

- **Fix summary:** After the domain consolidation (§3), run a full rendered crawl (Screaming Frog), a PageSpeed pass on 3 template types (home, SRP, VDP) mobile+desktop, and a Rich Results validation. Ticket findings by the table above.

---

## 7. Website UX & Conversion (CRO) — 🟡 C+  [VERIFY ON-SITE]

Standard dealer templates convert acceptably out of the box; the money is in lead-path hygiene. Priorities to verify and tune:

- **One primary CTA per template.** Homepage should push a single dominant action (shop inventory / value trade / get ePrice), not a wall of equal-weight buttons.
- **Lead form friction.** Every extra required field drops completion ~5–10%. Trim to name + phone/email + vehicle; move the rest post-lead.
- **Mobile-first.** The majority of dealer traffic is mobile — click-to-call must be one tap above the fold on every page; sticky call/text bar recommended.
- **Chat/text capture.** Confirm chat and text-us are live, staffed during hours, and routed to the CRM (not a dead widget). Missed chats = missed ups.
- **Digital retail path.** Payment calculator, trade valuation, and credit pre-qual should be present and lead-connected; these are now table stakes for Kia shoppers.
- **Service scheduler.** The service drive is the profit center — the online scheduler must be prominent, mobile-easy, and (critically) on the *surviving* domain so the redirect doesn't break returning-customer bookmarks.
- **Analytics/attribution.** Rebuild GA4 + call tracking + form-lead events on the canonical domain; a rebrand + domain split is the #1 cause of "our leads dropped" that's actually just broken tracking.

---

## 8. AI / Answer-Engine Optimization (AEO) — 🔴 D  *(where Automotive Intelligence lives)*

Shoppers increasingly ask ChatGPT, Google AI Overviews, Gemini, and Perplexity "best Kia dealer near Huntington Beach" — and those engines answer from **consistent, structured, well-cited entity data.**

- **Issue:** Right now an AI engine sees a fractured entity — two domains, two names ("Car Pros" vs "Sutherlin"), and reviews attached to a brand your site no longer uses. It cannot confidently resolve *who you are*, so it either omits you or cites you under the old name.
- **Impact:** **High and growing.** This is the fastest-shifting acquisition channel in auto retail, and you're currently near-invisible / mislabeled in it.
- **Fix:**
  1. Everything in §3–§4 (single domain, consistent NAP) *is* the foundation of AEO — do those first.
  2. Add complete **AutoDealer / LocalBusiness + Review + FAQ schema** with the new name and canonical URL.
  3. Publish a crisp, factual **"About Sutherlin Kia Huntington Beach"** entity page (who, where, since when, the Car Pros→Sutherlin history, brands serviced) — the kind of clean source AI engines quote verbatim.
  4. Keep the OEM locator + top citations identical so the engines see one coherent entity.
- **Priority:** **P1.** This is exactly the capability gap Automotive Intelligence closes.

---

## 9. Prioritized 30 / 60 / 90-day action plan

**First 30 days — stop the bleeding (P0)**
- [ ] Pick the canonical domain; 301 map every legacy URL → survivor
- [ ] Rename the existing GBP (do not recreate); update URL, photos, hours, description
- [ ] Reconcile NAP name on the top 20 citations (start: Kia locator, Google, Bing, Apple, Yelp, Cars.com, CARFAX, KBB, DealerRater, CarGurus)
- [ ] Verify both domains in Search Console; file Change of Address; submit clean sitemap
- [ ] Re-instrument GA4 + call/form tracking on the surviving domain

**Days 31–60 — reclaim the equity (P1)**
- [ ] Publish the "Car Pros Kia is now Sutherlin Kia" bridge (site + GBP + FAQ/press page)
- [ ] Deploy AutoDealer/LocalBusiness/Review/FAQ schema with the new name
- [ ] Launch review-generation cadence (SMS/email at every sale + RO); set 24–48h response SLA
- [ ] Rendered technical crawl + PageSpeed/CWV pass on home/SRP/VDP; ticket fixes
- [ ] Homepage trust strip: live Google rating + testimonials

**Days 61–90 — compound the advantage (P2–P3)**
- [ ] CRO pass: single primary CTA per template, form-field reduction, sticky mobile call/text
- [ ] Confirm chat/text routing into CRM; digital-retail tools lead-connected
- [ ] Local content: Huntington Beach / Orange County Kia landing pages, service-drive pages
- [ ] AEO monitoring: track how ChatGPT/AI Overviews/Perplexity describe the store; iterate

---

## 10. What this is worth — and the next step

You did the hard, expensive part: you bought a great store on a premium Beach Blvd corner with a decade of goodwill baked in. The digital cleanup is comparatively cheap — and until it's done, **you're paying full price for a store whose reputation and rankings are still ringing up under the previous owner's name.**

- **Cost of inaction:** every day, branded searches for the old name leak, the map pack under-ranks a split entity, and fresh shoppers judge a "reviewless" new brand.
- **Upside of action:** consolidate the domain + migrate the profiles correctly and you *inherit* ~10 years of trust and authority in weeks, not years.

**Recommended next step:** a **free 30-minute Automotive Intelligence assessment** where we (1) run the live rendered crawl the firewall blocked here, (2) pull your actual GBP + citation state, and (3) hand you a fixed-scope migration plan. From there our standard path is a **$2,500 deep digital audit + migration blueprint**, and **$7,500 full implementation** (domain consolidation, GBP/citation migration, schema/AEO, analytics rebuild, review engine).

---

### Sources
- Sutherlin Kia Huntington Beach — https://www.sutherlinkia.com/ *(egress-blocked for automated crawl during this pass)*
- Legacy domain (Car Pros Kia) — https://www.orangecountykia.com/
- Sutherlin Automotive acquires McKenna Subaru (CA entry) — https://www.prnewswire.com/news-releases/pinnacle-mergers--acquisitions-sutherlin-automotive-group-acquires-mckenna-subaru-sets-sights-on-building-largest-subaru-dealership-in-the-united-states-302570162.html
- Digital Dealer coverage — https://digitaldealer.com/news/sutherlin-automotive-group-acquires-mckenna-subaru-sets-sights-on-building-largest-subaru-dealership-in-the-united-states/167531/
- CBT News coverage — https://www.cbtnews.com/sutherlin-automotive-expands-into-california-with-mckenna-subaru-acquisition/
- Car Pros Kia HB reviews (Yelp, 1,700+) — https://www.yelp.com/biz/car-pros-kia-huntington-beach-huntington-beach-4
- Car Pros Kia HB reviews (Cars.com, 460+) — https://www.cars.com/dealers/202615/car-pros-kia-huntington-beach/
- Car Pros Kia HB (CARFAX) — https://www.carfax.com/Reviews-Car-Pros-Kia-of-Huntington-Beach-Huntington-Beach-CA_VSKUSZE001
- Car Pros Kia HB (KBB dealer ratings) — https://www.kbb.com/dealers/huntington-beach-ca/65471391/car-pros-kia-huntington-beach/
- Sutherlin Subaru HB (sister store) — https://www.sutherlinsubaruhb.com/
- Competitor: Garden Grove Kia — https://www.ggkia.com/
