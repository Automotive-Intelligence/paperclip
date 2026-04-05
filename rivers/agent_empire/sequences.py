"""Debra — Producer Agent for Agent Empire.

Turns VS Code logs and build sessions into content.
Weekly output: 6 video outlines, 6 show notes, 1 Ghost blog post, thumbnail copy.
"""

import os
import re
from pathlib import Path
from datetime import datetime
from core.logger import log_info

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))


def generate_content_calendar(days: int = 30) -> list:
    """Generate a 30-day content calendar from codebase activity."""
    log_info("agent_empire", "[DEBRA] Generating content calendar...")

    topics = _extract_build_topics()
    calendar = []
    current_date = datetime.now()

    for i, topic in enumerate(topics[:days]):
        from datetime import timedelta
        pub_date = current_date + timedelta(days=i)
        calendar.append({
            "date": pub_date.strftime("%Y-%m-%d"),
            "day_of_week": pub_date.strftime("%A"),
            "topic": topic["title"],
            "type": topic["type"],
            "outline": topic["outline"],
            "show_notes": topic.get("show_notes", ""),
            "thumbnail_copy": topic.get("thumbnail", ""),
        })

    log_info("agent_empire", f"[DEBRA] Generated {len(calendar)} content items")
    return calendar


def _extract_build_topics() -> list:
    """Extract content topics from the codebase — what's being built."""
    topics = [
        {
            "title": "I Built 5 AI Businesses in One Codebase — Here's the Architecture",
            "type": "video",
            "outline": "Walk through the Project Paperclip monorepo. 5 rivers, named agents, CRM integrations. Show the real code.",
            "show_notes": "Project Paperclip overview. 5 rivers: AI Phone Guy, Calling Digital, Automotive Intelligence, Agent Empire, CustomerAdvocate.",
            "thumbnail": "5 AI BUSINESSES | 1 CODEBASE",
        },
        {
            "title": "Randy the AI Agent Enrolls Leads While I Sleep",
            "type": "video",
            "outline": "Show Randy (GHL RevOps agent) monitoring tags, auto-enrolling prospects, firing ICP-specific SMS/email sequences.",
            "show_notes": "Randy monitors GoHighLevel for tyler-prospect tags. Auto-enrolls immediately. 12-day sequence with ICP-specific copy.",
            "thumbnail": "MY AI AGENT SELLS FOR ME",
        },
        {
            "title": "How I Cleaned 690 Dirty HubSpot Contacts with Python",
            "type": "video",
            "outline": "Show the HubSpot cleanup script. Auto-classify dealers vs vendors vs guests. Create saved views.",
            "show_notes": "HubSpot cleanup: auto-classify by email domain and company name. Skip dirty job title field.",
            "thumbnail": "690 CONTACTS → CLEAN CRM",
        },
        {
            "title": "Sophie the AI Receptionist — Demo Day",
            "type": "video",
            "outline": "Live demo of Sophie answering calls for plumbers, HVAC, dental. Show the personalization per ICP.",
            "show_notes": "Sophie AI Receptionist demo. Different scripts per vertical. 24/7 coverage.",
            "thumbnail": "AI ANSWERS YOUR PHONE",
        },
        {
            "title": "Building an AI Scoring Engine for Car Buyers (VERA)",
            "type": "video",
            "outline": "Walk through VERA behavioral scoring. 6 dimensions. Negotiation profiles. The future of car buying.",
            "show_notes": "VERA collects behavioral signals, scores across 6 dimensions, assigns negotiation profile.",
            "thumbnail": "AI KNOWS YOUR WALK-AWAY PRICE",
        },
        {
            "title": "From $0 to $15K MRR — The 90-Day AI Empire Plan",
            "type": "video",
            "outline": "Break down revenue targets across all 5 rivers. Show the math. Show the pipeline.",
            "show_notes": "Revenue targets: AI Phone Guy, Calling Digital retainers, Automotive audits, Agent Empire memberships + sponsors.",
            "thumbnail": "$15K MRR IN 90 DAYS",
        },
        {
            "title": "The Sponsor Pitch That Writes Itself — Wade's Codebase Scanner",
            "type": "video",
            "outline": "Show Wade scanning the repo for every tool used. Auto-generating pitch emails. Real sponsor outreach.",
            "show_notes": "Wade scans imports, requirements.txt, .env for tools. Generates personalized sponsor pitches.",
            "thumbnail": "AI FINDS MY SPONSORS",
        },
        {
            "title": "Faith, Freedom, and Five Rivers — Why I'm Building in Public",
            "type": "blog",
            "outline": "Personal essay. Why Agent Empire exists. The north star. Building for freedom, funded by faith.",
            "show_notes": "",
            "thumbnail": "",
        },
    ]
    return topics


def generate_weekly_output() -> dict:
    """Generate Debra's weekly content output."""
    calendar = generate_content_calendar(7)
    videos = [c for c in calendar if c["type"] == "video"]
    blogs = [c for c in calendar if c["type"] == "blog"]

    return {
        "video_outlines": len(videos),
        "show_notes": len(videos),
        "blog_posts": len(blogs),
        "thumbnail_copies": len([v for v in videos if v["thumbnail"]]),
        "items": calendar,
    }
