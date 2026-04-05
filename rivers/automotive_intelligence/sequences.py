"""Email sequences for Automotive Intelligence.

Insider tone — Michael sold 500+ cars, was a desk manager.
NEVER mention pricing. NEVER sound like a vendor.
"""

import os

BOOKING_LINK = os.environ.get("BOOKING_LINK_AI", "")

SEQUENCE = [
    {
        "day": 0, "channel": "email",
        "subject": "20 years on your side of the desk",
        "body": """I sold over 500 cars. I've sat in the desk manager chair. I've watched a BDC go dark at 6pm on a Friday while leads piled up.

I'm not here to sell you software — I'm here to show you what's possible when the tools actually understand how a dealership works.

I built an AI platform specifically for dealers because I got tired of watching tech companies sell tools to people who've never walked a lot.

If that resonates, I'd love to connect.

— Michael Rodriguez""",
    },
    {
        "day": 3, "channel": "email",
        "subject": "Where does your dealership actually stand on AI?",
        "body": """I've been offering a free AI Readiness Mini Audit to dealers who want a straight answer.

30 minutes. I score your dealership across 5 pillars. You walk away knowing exactly where the gap is and what it's costing you per month.

No pitch. No pressure. Just clarity from someone who's been on your side of the desk.

{booking_link}

— Michael Rodriguez""",
    },
    {
        "day": 7, "channel": "email",
        "subject": "The dealer two towns over just deployed this",
        "body": """AI adoption in automotive is accelerating faster than most dealers realize.

The dealers who move first aren't just saving time — they're owning their zip code. While everyone else is still figuring out what AI means, they're already using it to respond faster, follow up better, and close more.

The gap is widening every month.

If you want to know where your dealership stands, the mini audit is still on the table.

{booking_link}

— Michael Rodriguez""",
    },
    {
        "day": 10, "channel": "email",
        "subject": "The 5 pillars we look at in every dealership audit",
        "body": """When I audit a dealership, I look at 5 things:

1. Lead Intelligence — how fast and how smart is your first response?
2. Personalization — does your follow-up feel custom or copy-paste?
3. Sales Automation — what happens after hours? On weekends?
4. Revenue Optimization — are you leaving money on the table in F&I, service, or trade-ins?
5. Customer Lifecycle — what happens after the sale?

Most dealers score well in 1-2 pillars and have massive gaps in the rest.

The mini audit takes 30 minutes and gives you a clear picture.

{booking_link}

— Michael Rodriguez""",
    },
    {
        "day": 14, "channel": "email",
        "subject": "Signing off — for now",
        "body": """Last email from me on this.

I know you're busy running a dealership — I've been there. The offer for a free AI Readiness Audit stands whenever the timing is right.

No pressure. No follow-up. Just know that when you're ready to see what AI can actually do for your store, I'm here.

And I'm one of the few people in this space who's actually sold cars.

— Michael Rodriguez

{booking_link}""",
    },
]


def get_sequence() -> list:
    return SEQUENCE


def render_message(template: dict, contact: dict) -> dict:
    rendered = {}
    for key, val in template.items():
        if isinstance(val, str):
            rendered[key] = val.format(
                firstName=contact.get("firstName", ""),
                lastName=contact.get("lastName", ""),
                company=contact.get("company", ""),
                booking_link=BOOKING_LINK,
            )
        else:
            rendered[key] = val
    return rendered
