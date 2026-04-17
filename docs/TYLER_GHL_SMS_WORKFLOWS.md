# Tyler GHL SMS Workflows — Click-by-Click Build Guide

**Date:** 2026-04-17
**Business:** The AI Phone Guy
**Purpose:** SMS touchpoints between Instantly email steps + calculator lead magnet
**Prerequisites:** Calculator URL live (calculator.theaiphoneguy.ai or bit.ly short link)

---

## Before You Start

### Create these tags in GHL (Settings → Tags → Add Tag):
- `tyler-sms-day1`
- `tyler-sms-day5`
- `calculator-clicked`
- `calculator-completed`

The `tyler-prospect` tag already exists (Tyler's pipeline creates it automatically).

### Create a custom field (Settings → Custom Fields → Add Field):
- Name: `Calculator Lost Revenue`
- Key: `contact.calculator_lost_revenue`
- Type: Text
- Folder: Tyler AI Prospecting

This field will be populated by the calculator API when a lead submits their email.

---

## Workflow 1: Tyler SMS Day 1

**What it does:** 24 hours after Tyler finds a prospect, send them an SMS with the missed call calculator link. This fires the day after Instantly sends the first email — so the prospect gets an email, then a text the next day.

### Build Steps:

1. **Automation → Workflows → + Create Workflow → Start from Scratch**

2. **Name it:** `Tyler — SMS Day 1 (Calculator CTA)`

3. **Add Trigger → Contact Tag**
   - Trigger: `Contact Tag Added`
   - Tag: `tyler-prospect`
   - *(This fires every time Tyler's pipeline creates a new prospect in GHL)*

4. **Add Action → Wait**
   - Wait for: `1 Day`
   - *(This ensures the SMS goes out Day 1, not Day 0 when the email fires)*

5. **Add Action → If/Else Condition**
   - Condition: Contact `Does Not Have Tag` → `do-not-contact`
   - *(Safety check — skip quarantined contacts)*
   - If YES → continue to SMS
   - If NO → end

6. **Inside the YES branch → Add Action → Send SMS**
   - **From:** Your connected phone number
   - **Message body:**
   ```
   Hey {{contact.first_name}}, quick question — do you know how much {{contact.company_name}} loses to missed calls every month?

   Most {{contact.business_type}} businesses in DFW lose $3K-$12K/mo and don't even realize it.

   Free 60-second calculator — see your number:
   [CALCULATOR LINK]

   — Michael, The AI Phone Guy
   ```
   - Replace `[CALCULATOR LINK]` with your actual calculator URL or bit.ly short link

7. **After the SMS → Add Action → Add Tag**
   - Tag: `tyler-sms-day1`

8. **Save → Publish**

---

## Workflow 2: Tyler SMS Day 5

**What it does:** 4 days after SMS 1, send a follow-up SMS — but only if the prospect hasn't already clicked the calculator and submitted their email. No point texting someone who already converted.

### Build Steps:

1. **Automation → Workflows → + Create Workflow → Start from Scratch**

2. **Name it:** `Tyler — SMS Day 5 (Calculator Follow-up)`

3. **Add Trigger → Contact Tag**
   - Trigger: `Contact Tag Added`
   - Tag: `tyler-sms-day1`
   - *(This fires after Workflow 1 completes — chaining via tag)*

4. **Add Action → Wait**
   - Wait for: `4 Days`

5. **Add Action → If/Else Condition**
   - Condition: Contact `Does Not Have Tag` → `calculator-completed`
   - *(Skip if they already submitted their email on the calculator)*
   - If YES → continue to SMS
   - If NO → end

6. **Add a second If/Else inside the YES branch**
   - Condition: Contact `Does Not Have Tag` → `do-not-contact`
   - If YES → continue
   - If NO → end

7. **Inside the YES branch → Add Action → Send SMS**
   - **From:** Your connected phone number
   - **Message body:**
   ```
   {{contact.first_name}}, following up — {{contact.business_type}} businesses in {{contact.city}} that answer every call book 30%+ more jobs.

   Worth 60 seconds to see what you're leaving on the table:
   [CALCULATOR LINK]

   — Michael, The AI Phone Guy
   ```

8. **After the SMS → Add Action → Add Tag**
   - Tag: `tyler-sms-day5`

9. **Save → Publish**

---

## Workflow 3: Calculator Lead → Hot Alert

**What it does:** When someone submits their email on the calculator, immediately flag them as a hot lead. This is a BOX BOX moment — they engaged with your content and gave you their email. Michael needs to know NOW.

### Build Steps:

1. **Automation → Workflows → + Create Workflow → Start from Scratch**

2. **Name it:** `Tyler — Calculator Lead (BOX BOX Alert)`

3. **Add Trigger → Contact Tag**
   - Trigger: `Contact Tag Added`
   - Tag: `calculator-completed`
   - *(The Paperclip API applies this tag when someone submits their email on the calculator)*

4. **Add Action → Add Tag**
   - Tag: `pit-p2`
   - *(Joshua's grid position — P2 means BOX BOX, clicked/engaged)*

5. **Add Action → Remove Tag** (clean up old position if exists)
   - Tag: `pit-p18`
   - *(Removes the "no opens" tag if Joshua had previously tagged them)*

6. **Add Action → Internal Notification**
   - Type: `Email` or `In-App Notification` (your preference)
   - To: Your email / phone
   - Subject: `BOX BOX — Calculator Lead: {{contact.first_name}} at {{contact.company_name}}`
   - Body:
   ```
   HOT LEAD from the Missed Call Calculator:

   Name: {{contact.first_name}} {{contact.last_name}}
   Company: {{contact.company_name}}
   Business Type: {{contact.business_type}}
   City: {{contact.city}}
   Phone: {{contact.phone}}
   Email: {{contact.email}}

   Lost Revenue Estimate: {{contact.calculator_lost_revenue}}

   This prospect used the calculator and gave you their email.
   Call them within 24 hours.

   Book link if you want to send it: https://bit.ly/4sMZpTi
   ```

7. **Add Action → Create Opportunity**
   - Pipeline: Your Tyler pipeline
   - Stage: First stage (e.g., "New Lead" or "Assessment Scheduled")
   - Name: `{{contact.company_name}} — Calculator Lead`
   - Value: `2500` *(AI Readiness Assessment value)*
   - Contact: `{{contact.id}}`

8. **Save → Publish**

---

## The Complete Multichannel Timeline

Once all 3 workflows are published, here's what a Tyler prospect experiences:

```
Day 0   [Instantly]  Email: "quick question about {{companyName}}"
        [GHL]        Contact created, tagged tyler-prospect
        [GHL]        Workflow 1 triggers, starts 1-day wait

Day 1   [GHL SMS]    "Hey {{first_name}}, do you know how much
                      {{company_name}} loses to missed calls?"
                      + calculator link
        [GHL]        Tagged tyler-sms-day1
        [GHL]        Workflow 2 triggers, starts 4-day wait

Day 3   [Instantly]  Email: "one thing about {{companyName}}"

Day 5   [GHL SMS]    "{{first_name}}, following up on the calculator..."
                      + calculator link (only if they haven't submitted)
        [GHL]        Tagged tyler-sms-day5

Day 6   [Instantly]  Email: "30% fewer missed calls" (Sophie story)

Day 9   [Instantly]  Email: "closing the loop"
```

**If prospect clicks calculator + submits email at any point:**
- Workflow 3 fires immediately
- Tagged `calculator-completed` + `pit-p2`
- Michael gets BOX BOX alert
- Opportunity created in pipeline
- Instantly nurture campaign starts (5 emails, separate from Tyler's cold sequence)
- Prospect goes from cold outreach to warm lead in one click

---

## Testing Checklist

Before enabling for real prospects:

- [ ] Create a test contact with tag `tyler-prospect`, email `salesdroid@icloud.com`, phone your personal number
- [ ] Verify SMS 1 arrives after 1 day (or temporarily set wait to 1 minute for testing, then change back)
- [ ] Verify SMS 2 arrives 4 days after SMS 1 (or test with shortened wait)
- [ ] Manually add tag `calculator-completed` to test contact
- [ ] Verify internal notification arrives
- [ ] Verify opportunity is created in pipeline
- [ ] Verify `pit-p2` tag is applied and `pit-p18` is removed
- [ ] Delete test contact and opportunity after verification
- [ ] Set all wait times back to production values (1 day, 4 days)
- [ ] Publish all 3 workflows

---

## Notes

- **SMS compliance:** GHL handles A2P 10DLC registration. Make sure your phone number is registered for business texting. If not registered, SMS won't deliver.
- **Calculator link:** Use a bit.ly or similar short link for SMS. Long URLs in text messages look spammy and may get filtered.
- **Do not blast:** These workflows are trigger-based (tyler-prospect tag), not bulk sends. Each prospect gets exactly 2 SMS messages across 9 days — that's respectful, not spammy.
- **Unsubscribe:** If someone replies STOP to an SMS, GHL automatically marks them DND. The `do-not-contact` check in both workflows is an extra safety layer.
