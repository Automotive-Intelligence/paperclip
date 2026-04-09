# Marcus CD Outreach — 4 Vertical-Specific Sequences

**For: Calling Digital · Sender: Michael Rodriguez via connected Gmail**
**Created: 2026-04-09**

## What this is

Four distinct sequences, one per OwnerPhones vertical. Each is written to the psychology and pain of that buyer — not a generic "Texas businesses" template.

The merge fields (`{{person.first_name}}`, `{{company.name}}`, `{{company.cd_industry}}`, `{{company.cd_prospect_notes}}`) are already populated on all 197 imported People. They will resolve correctly inside Attio's sequence editor.

## How to set this up in Attio (15-20 minutes total)

### Once for ALL sequences

1. Open Attio → **Sequences**
2. Right-click the existing **Marcus CD Outreach Sequence** → **Duplicate** (do this 4 times)
3. Rename the 4 duplicates:
   - `Marcus — Med Spa`
   - `Marcus — PI Law`
   - `Marcus — Real Estate`
   - `Marcus — Custom Home Builder`
4. (Optional) Delete or archive the original `Marcus CD Outreach Sequence` to avoid confusion

### For EACH sequence (repeat 4 times)

1. Open the sequence
2. Click into **Step 1**
3. Replace the **Subject** line with the one below for that vertical
4. Replace the **Body** with the one below for that vertical
5. Verify **Sending window** matches the recommendation below (different per vertical)
6. Top right → **Enroll recipients**
7. Filter: `Vertical` `is` `<that vertical>` (e.g. `med-spa`)
8. Select all → Add to sequence
9. Click **Publish sequence** at the top
10. Toggle **Enable sequence** ON

That's it. Repeat for the other 3.

---

## SEQUENCE 1 — Marcus · Med Spa

**Recipients:** Filter `Vertical = med-spa` (~50 contacts)
**Sending window:** Tuesday-Friday, 9:00 AM - 12:00 PM CST (med spas are slowest in mornings, owners check email then)
**Recommended sender:** michael@calling.digital

### Step 1 — Subject

```
{{person.first_name}}, what happens after 6pm at {{company.name}}?
```

### Step 1 — Body

```
Hey {{person.first_name}},

I came across {{company.name}} while researching med spas in Texas — you have a beautiful brand.

Quick question: when someone fills out a consult form at 9pm, or texts asking about a treatment on a Saturday, what happens to that inquiry?

For most med spas I talk to, those after-hours inquiries either get lost overnight or wait until Monday morning — and by then 30-40% of those leads have already booked somewhere else.

{{company.cd_prospect_notes}}

I help med spas in Texas put a 24/7 AI front desk in place that books consults the same way your best receptionist would — without changing how the front desk runs during business hours.

Worth a 15-minute conversation to see if it's a fit for {{company.name}}?

— Michael
Calling Digital · michael@calling.digital
```

**Why this works:**
- Subject is a question with their name + company → opens like a personal text
- First line is observation, not pitch
- Second line forces them to mentally answer the question (they know the answer is "nothing")
- Third line names the specific cost in numbers
- Fourth line is the personalized note from cd_prospect_notes
- Fifth line is the offer, framed as adding without disrupting
- Close is low-pressure, 15 min not 30, "see if it's a fit" not "book a demo"

---

## SEQUENCE 2 — Marcus · PI Law

**Recipients:** Filter `Vertical = pi-law` (~50 contacts)
**Sending window:** Tuesday-Thursday, 7:00 AM - 9:00 AM CST OR 5:00 PM - 8:00 PM CST (lawyers check email before/after court)
**Recommended sender:** michael@calling.digital

### Step 1 — Subject

```
{{person.first_name}} — the call you missed last Tuesday at 9pm
```

### Step 1 — Body

```
{{person.first_name}},

I came across {{company.name}} while researching personal injury firms in Texas.

Quick math you've probably already done: a single missed intake call from a serious injury case is $25K-$80K in lost fees. Most PI firms in Texas lose 2-3 of those per month to voicemail after 5pm.

{{company.cd_prospect_notes}}

I help PI firms in Texas put an AI intake layer in front of their phone line that:
- Answers every call 24/7 (including weekends and holidays)
- Qualifies the case in under 90 seconds (jurisdiction, statute of limitations, severity)
- Drops the qualified ones directly into your CRM with notes
- Sends you a SMS for anything that looks like a real case

It doesn't replace your intake team — it makes sure they're only talking to the cases that matter.

Worth 15 minutes this week to see what it would look like at {{company.name}}?

— Michael
Calling Digital · michael@calling.digital
```

**Why this works:**
- Subject is hyper-specific and provocative — they will open it to figure out what call you're talking about
- Opens with the observation, no pitch
- "Quick math you've probably already done" → respects them as a sharp operator, doesn't talk down
- Specific dollar range ($25K-$80K) anchors the cost
- Bullet list shows the system without selling — every bullet is a tangible action, not a feature
- "Doesn't replace your intake team" preempts the #1 objection lawyers have
- "see what it would look like at {{company.name}}" → not generic, says we'll show YOUR firm

---

## SEQUENCE 3 — Marcus · Real Estate

**Recipients:** Filter `Vertical = real-estate` (~50 contacts)
**Sending window:** Monday-Friday, 8:00 AM - 10:00 AM CST (agents check email between showings, mornings are best)
**Recommended sender:** michael@calling.digital

### Step 1 — Subject

```
{{person.first_name}}, between showings — quick one
```

### Step 1 — Body

```
Hey {{person.first_name}},

I came across {{company.name}} while researching real estate teams in Texas.

I'll keep this short because I know you're between showings.

The buyer who texted you at 2pm on Saturday — the one you couldn't get back to until Sunday night — already toured a house with another agent that afternoon. That happens to most agents in Texas 1-2 times a month and it's the most expensive miss in the business.

{{company.cd_prospect_notes}}

I help Texas agents put an AI front desk in place that:
- Answers every inbound call, text, and form fill in under 90 seconds
- Handles the basics (price range, area, timeline, financing pre-qual) so you walk into the next conversation already informed
- Books showings directly to your calendar
- SMS-pings you the moment a serious buyer comes in

It runs while you're driving, showing, or off the phone — and disappears the moment you're back in.

Worth a 15-min call between showings this week to see if it fits how you work?

— Michael
Calling Digital · michael@calling.digital
```

**Why this works:**
- Subject acknowledges their reality (between showings) — they will open it because it sounds like another agent or a client
- First line names the universal pain in vivid scene-setting (Saturday text, Sunday return call, lost deal)
- "Most expensive miss in the business" → speaks their language
- Bullet list shows specific actions, not features
- "Runs while you're driving" → fits their physical reality
- "Disappears the moment you're back in" → preempts "I want to talk to my own clients" objection
- Close acknowledges their schedule constraints

---

## SEQUENCE 4 — Marcus · Custom Home Builder

**Recipients:** Filter `Vertical = home-builder` (~50 contacts)
**Sending window:** Monday-Wednesday, 6:30 AM - 8:30 AM CST (builders are at jobsites by 7-8am, so catch them before)
**Recommended sender:** michael@calling.digital

### Step 1 — Subject

```
{{person.first_name}}, the inquiry you got last Thursday from a real buyer
```

### Step 1 — Body

```
{{person.first_name}},

I came across {{company.name}} while researching custom home builders in Texas.

Most custom builders I talk to are referral-dependent — and the inquiries that come in from your website or Houzz or Instagram either go to a generic info@ email that nobody checks or get returned three days later when you're back from a jobsite. By then, the serious ones have already booked a meeting with another builder.

The painful part: you can't tell which inquiries were tire-kickers and which were real $800K-$2M projects until weeks later.

{{company.cd_prospect_notes}}

I help Texas custom builders put an AI inbound system in place that:
- Answers every inquiry within 60 seconds (call, text, web form)
- Asks the qualification questions you'd ask on a first call (lot status, budget range, timeline, financing)
- Books a discovery call directly to your calendar
- Flags the real prospects so you only spend time on people who can actually move forward

It's not lead generation — you don't need more leads. It's making sure the leads you already get don't slip through the cracks while you're at a jobsite.

Worth 15 minutes to see what it would look like for {{company.name}}?

— Michael
Calling Digital · michael@calling.digital
```

**Why this works:**
- Subject is mysterious and specific — they will open to find out which inquiry
- "Referral-dependent" → they nod immediately, that's their identity
- Names the exact pain of a generic info@ inbox + 3-day delay → vivid and true
- "$800K-$2M projects" → speaks in their actual deal sizes
- "It's not lead generation — you don't need more leads" → preempts the #1 builder objection (they hate being sold leads)
- "Making sure the leads you already get don't slip through the cracks" → reframes the offer as protecting existing pipeline, not adding to it
- "While you're at a jobsite" → respects their physical reality

---

## Cross-cutting notes

### Why each sequence has a different sending window
Each ICP has a different daily rhythm:
- **Med spas:** Slow mornings, busy afternoons. Owner checks email 9-12.
- **PI lawyers:** In court 9-5. Check email before court (7-9 AM) or after (5-8 PM).
- **Real estate agents:** Showings dominate afternoons. Mornings are when they're at their desk.
- **Home builders:** At jobsites by 7:30 AM. Catch them before they leave.

These windows are inside the existing 9-5 default. If you want to maximize, manually edit each sequence's sending window in **Settings → Delivery → Sending window**.

### Why each subject line uses {{person.first_name}}
Open rates jump significantly when the subject line contains the recipient's first name. Generic subject lines ("Quick question") get filtered or skimmed past. Personalized ones get opened.

### Why no pricing is mentioned anywhere
Iron rule from PAPERCLIP_EXECUTE.md: **NEVER mention pricing in outbound. Pricing comes from Michael on the call.** All 4 sequences follow this. If a recipient asks about pricing in a reply, you handle that in the conversation, not in the cold email.

### Why each one closes with a 15-minute ask, not a "demo"
"Demo" sounds like a sales pitch. "15 minutes to see if it fits" sounds like a peer conversation. Reply rates are noticeably better with the second framing across every B2B vertical.

### What to do AFTER you publish all 4

The first 24-48 hours will tell you everything:
- Which subject line gets the highest open rate
- Which vertical replies the most
- What objections people raise (those become Step 2)

Once you have 2-3 days of data, come back and we'll write Steps 2-4 for each sequence based on what's actually working.

## Recipient counts (as of right now in Attio)

| Vertical | Filter | Approx count |
|---|---|---|
| Med Spa | `vertical = med-spa` | ~50 |
| PI Law | `vertical = pi-law` | ~50 |
| Real Estate | `vertical = real-estate` | ~50 |
| Home Builder | `vertical = home-builder` | ~50 |
| **TOTAL** | | **~197** |

## What to NOT do

- ❌ Don't enroll anyone with `pipeline_stage = marcus-salvage` — those are the 16 manual review records, save them for personal outreach
- ❌ Don't enable all 4 sequences in the same minute — stagger by 30 seconds each so Gmail doesn't see a sudden burst from your account
- ❌ Don't change the merge field names (`{{person.first_name}}` etc.) — they're case-sensitive and have to match Attio's syntax exactly
- ❌ Don't worry about A/B testing yet — get the first batch out, learn from real replies, then iterate

## Reference

- Sender: connected Gmail account on michael@calling.digital
- Sequence editor: `https://app.attio.com/calling-digital/sequences/<id>`
- Live dashboard: https://paperclip-production-ba14.up.railway.app/dashboard
- Iron rule: NEVER mention pricing in outbound
