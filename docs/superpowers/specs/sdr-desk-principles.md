# The AVO SDR Desk — Binding Principles

**Status:** BINDING on every sub-project (#1-#4) and every skill in the SDR desk. A skill that violates one of these is not done, regardless of output quality.
**Origin:** distilled from the week of 2026-07-15 (verification failures) + analysis of the 42-skill SDR desk article (2026-07-27). We are not reinventing the wheel; we are taking the sharpest pieces and holding them to a quant-desk standard.
**The one-line moat:** in an outbound world drowning in AI slop, we are the shop that does not lie. Verified, honest, fail-closed. That is the product, not the 42 skills.

---

## Part 1 — Honesty (non-negotiable, inherited from the founder constraints)

1. **Never fabricate a company, a person, a defect, a quote, a number, or a relationship.** If it cannot be verified, it is not said. This is the whole business.
2. **Every skill file MUST carry a `Failure mode + Guard` field.** Every SDR skill has exactly one way it lies (invents a comment count, a quote, a name, an email pattern, a deadline). The skill names its failure mode and the hard rule that stops it. Required field, like a test. Examples of the guard vocabulary: `UNVERIFIED`, `NONE FOUND`, `SAMPLE TOO SMALL`, `GUESS`, `NO FIT`, `NOTHING SAID`, `UNASSIGNED`. These words are features, not hedges.
3. **No source, no claim.** Any factual assertion (a change, a trigger, a complaint, a stat) carries a checkable source or it is cut.
4. **The chokepoint gate runs first.** The Verification Gate (sub-project #1) verifies the raw material once, hard, BEFORE any of the 42 skills touch a prospect. The article's guards are per-skill and honor-system; ours is a structural chokepoint. Nothing enters the desk unverified.
5. **Self-check before return.** Every generation skill runs its own check before emitting: "Could this exact message be sent to any other company? If yes, rewrite." This is a cheap early gate that sits on top of the hard approval_queue gate, not instead of it.

## Part 2 — Fail closed (the week's lesson, encoded)

6. **Optimism is a bug.** "Sounds interesting, send me info" is LATER, never INTERESTED. Ambiguous goes to the human pile. The money number (interested / positive reply / booked) counts only unambiguous positive signal — it fails CLOSED. A metric that fails open flatters the pipeline and lies to the operator.
7. **Uncertainty escalates, never auto-proceeds.** When a model judgment is off-contract, missing, or malformed, the verdict is NEEDS_HUMAN, not PASS. Verified in the gate; binding everywhere.
8. **A CRM stage is not contact history.** Never infer a prior relationship, a "following up", or any warmth from a pipeline stage. Cold until two-way contact is proven from the outbound thread.

## Part 3 — The quant-desk edge (how we find whales and close)

9. **Signal decay: trade the fresh catalyst.** The edge is recency. Bucket every trigger: MOVE TODAY (<14 days), MOVE THIS MONTH (15-45), TOO LATE (>45). A 6 that just raised beats a 9 that is cold. Recency beats score.
10. **Situation beats firmographics.** A whale is not a big company. It is an account with a fresh catalyst (funding, new head of the function we sell to, a public complaint, a competitor move) that touches a stated priority (from a job posting or an earnings call) that we can reach this week. Target the moment, not the company type. "B2B SaaS, 50-200" is not a reason.
11. **Backtest the ICP against reality.** Score prospects against real closed-won AND closed-lost, not the aspirational deck. Any factor that appears in both is not predictive — drop it. HONEST CAVEAT for us today: we are pre-revenue with a thin closed set, so `SAMPLE TOO SMALL` applies now; the muscle sharpens with every close. Never invent confidence we do not have.
12. **Alpha is public data nobody bothers to read.** Complaints in forums, the vendor stack readable off job postings, the priorities leadership repeats on the earnings call. Reading these is the whale-finder, not a bigger list.
13. **Position sizing.** Cap the daily TODAY list to what the human closers (Michael, Ryan, Teagan) can actually work. A list of 40 is the same as no list.
14. **Kill discipline.** Audit live sequences on a schedule; KILL anything at half the best one's positive-reply rate. Fix the ONE biggest bottleneck stage, not five things. Cut losers fast, press the winner.

## Part 4 — Architecture (no lock-in, self-producing)

15. **Portable by construction.** Skill logic lives as plain-text files in paperclip; tools are code; the model sits behind one adapter (`studio_social_llm.llm_json` today, OpenRouter-swappable). Never a Claude Project. If Claude is cut off, re-point the adapter and the desk runs.
16. **Self-producing, 24/7, off the laptop.** The desk runs as a Railway engine (the blog/social pattern), not an operator opening a Project on a Tuesday. Full autonomy, gated by the Verification Gate + approval_queue: clean auto-dispatches, ambiguous queues for one click, garbage is dropped.
17. **Human keeps two things, always:** the price conversation and any angry/annoyed reply. These never auto-fire.
    **OWNER AMENDMENT 2026-08-07 (Michael, verbatim intent: "no approval queue -- install guardrails in context and be intelligent enough that we don't need approval queues. We're a top executive solution").** The first-contact decision moves from human-held to MACHINE-WITH-STRUCTURAL-GUARDRAILS, effective with SP4. The precondition in the original clause was met (the gate's verify-rate was proven in the 2026-07/08 shadow runs), and the owner exercised his Part-5 authority over the live-send switch. The replacement for human review is not trust -- it is construction: (a) only gate-PASS opportunities can reach the send path; (b) first-touch copy is template + verified-fact slots, and a deterministic validator proves every factual claim against the gate's evidence log -- no evidence, no sentence; (c) hard fail-closed pre-send gates (suppression/DNC, verified contact only, one first-touch per company ever, 5/day/brand cap, business-hours window, no pricing content, opt-out line); (d) an in-line adversarial model pass that refutes weak drafts -- refuted drafts die as digest exceptions, they never queue; (e) full audit + kill switch. Approval QUEUES are retired for first-touch; approval remains only where clause 17's two permanent holds apply.

## Part 5 — What is held for the owner
Spend, pricing/quotes, contracts, discounts, and the live-send switch. The desk produces and verifies fully autonomously up to first contact; those five are released by Michael.

---

**Build order (from the article's data + our gate-first safety):** Verification Gate (done) -> Prospect Researcher -> Voice Writer -> Reply Classifier -> Sequence Builder -> the rest. Research and voice move reply rate most; the gate makes all of it safe.
