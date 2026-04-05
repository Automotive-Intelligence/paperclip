"""Email sequences for Calling Digital — Track A (cold) and Track B (warm).

IRON RULES:
- NEVER mention pricing ($2,500/mo or $5K-$8K/mo)
- Every message is written to the OWNER
- ICP-specific copy
"""

import os

BOOKING_LINK = os.environ.get("BOOKING_LINK_CD", "")

TRACK_A = [
    {
        "day": 1, "channel": "email",
        "subject": "What AI actually does for a {industry} business in DFW",
        "body": "Hey {firstName},\n\nThere's a lot of noise about AI right now. Most of it is hype.\n\nBut for {industry} businesses in North Texas, there are 3-4 things that actually move the needle — and they're not what most people think.\n\nI put together a quick breakdown of what's real and what's not for your space. No pitch — just clarity.\n\nWorth a look if you've been wondering where AI fits for {businessName}.",
    },
    {
        "day": 4, "channel": "email",
        "subject": "Real numbers from a North Texas {industry} client",
        "body": "Hey {firstName},\n\nWanted to share something concrete. We worked with a {industry} business in DFW — similar size to {businessName}.\n\nWithin 60 days they automated 3 workflows that were eating 15+ hours a week. The owner got their evenings back.\n\nNo magic. Just the right tools applied to the right problems.\n\nHappy to share the details if you're curious.",
    },
    {
        "day": 8, "channel": "email",
        "subject": "The real cost of waiting on AI",
        "body": "Hey {firstName},\n\nEvery month a {industry} business runs without automation, the gap widens. Not because AI is magic — but because your competitors are starting to use it.\n\nThe businesses that move first don't just save time. They compound the advantage.\n\nJust something to think about.",
    },
    {
        "day": 14, "channel": "email",
        "subject": "One last thing before I go",
        "body": "Hey {firstName},\n\nLast email from me. I know you're busy running {businessName}.\n\nIf AI is something you want to explore for your business, I'm offering a free 30-minute AI audit. No pitch, no pressure — just a clear picture of where you stand and what's possible.\n\n{booking_link}\n\nEither way, I appreciate your time.\n\n— Marcus",
    },
]

TRACK_B = [
    {
        "day": 1, "channel": "email",
        "subject": "Here's exactly what we'd build for {businessName}",
        "body": "Hey {firstName},\n\nI've been looking at {businessName} and I see 3 workflows we could automate right away:\n\n1. Inbound lead response — instant, personalized, 24/7\n2. Follow-up sequences that actually feel human\n3. Review generation that runs on autopilot\n\nThese aren't hypothetical. We've built all three for {industry} businesses in DFW.\n\nWant me to walk you through what it would look like for {businessName} specifically?",
    },
    {
        "day": 3, "channel": "email",
        "subject": "A case study from your industry",
        "body": "Hey {firstName},\n\nQuick follow-up. Here's what happened when we deployed these same 3 workflows for another {industry} business:\n\n- Response time went from 4 hours to 4 minutes\n- Follow-up completion rate jumped from 30% to 95%\n- Reviews doubled in 90 days\n\nSame tools. Same market. Similar business to yours.\n\n{booking_link}",
    },
    {
        "day": 6, "channel": "email",
        "subject": "Ready to show you the full picture?",
        "body": "Hey {firstName},\n\nI've put together a custom proposal preview for {businessName}. It covers the 3 workflows, timeline, and expected outcomes — based on what we've seen with similar {industry} businesses.\n\n15 minutes to walk through it. If it makes sense, we go. If not, you've got a free roadmap.\n\n{booking_link}\n\n— Marcus",
    },
    {
        "day": 10, "channel": "email",
        "subject": "Last note from Marcus",
        "body": "Hey {firstName},\n\nLast email from me on this. I know timing is everything and it might not be right for {businessName} today.\n\nBut if AI automation is on your radar at any point, the door's open. I'll keep your file warm.\n\nAppreciate you, {firstName}.\n\n— Marcus",
    },
]


def get_track_sequence(track: str) -> list:
    return TRACK_B if track == "B" else TRACK_A


def render_message(template: dict, contact: dict) -> dict:
    rendered = {}
    for key, val in template.items():
        if isinstance(val, str):
            rendered[key] = val.format(
                firstName=contact.get("firstName", ""),
                businessName=contact.get("businessName", ""),
                industry=contact.get("industry", "your"),
                booking_link=BOOKING_LINK,
            )
        else:
            rendered[key] = val
    return rendered
