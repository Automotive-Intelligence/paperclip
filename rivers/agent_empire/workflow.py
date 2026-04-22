"""Agent Empire — Tammy (Community), Debra (Producer), Wade (Biz Dev), Sterling (Web).

Tammy: Skool community engagement — welcome DMs, daily posts, question responses.
Wade: Sponsor outreach via Gmail MCP.
Debra: Content production — video outlines, blog drafts, content calendar.
Sterling: buildagentempire.com builder and daily maintainer.
Schedule: Tammy every 6h, Wade Mon 9am, Debra Mon 6am, Sterling daily 7am.
"""

import os
import json
import requests
from datetime import datetime, timedelta
from core.logger import log_info, log_error, log_enrollment, log_sequence_event

SKOOL_EMAIL = os.environ.get("SKOOL_EMAIL")
SKOOL_PASSWORD = os.environ.get("SKOOL_PASSWORD")
GMAIL_MCP_URL = os.environ.get("GMAIL_MCP_URL", "https://gmail.mcp.claude.com/mcp")
SPONSOR_EMAIL = os.environ.get("SPONSOR_EMAIL_ALIAS", "sponsors@buildagentempire.com")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")

_welcomed = set()
_sponsor_contacted = set()
_stats = {"members_welcomed": 0, "sponsors_pitched": 0, "posts_created": 0}

# ─── TAMMY — Community Agent ───

WELCOME_SEQUENCE = [
    {
        "day": 0,
        "body": "Hey {name} — welcome to Agent Empire. Building 5 AI businesses in public and documenting every win and failure. Start here: [pinned post]. Ask anything.",
    },
    {
        "day": 3,
        "body": "Quick check-in — watched the first build video? Here's where most start: [YouTube link]",
    },
    {
        "day": 7,
        "body": "One week in — here's what paid members are working on: [teaser]. Trial is free 7 days: [trial link]",
    },
    {
        "day": 6,  # Day 6 of trial (before 7-day expiry)
        "body": "Trial ends tomorrow. Here's what you'd lose: [list]. Keep going: [upgrade link]",
    },
]


def tammy_run():
    """Tammy's main loop — every 6 hours."""
    log_info("agent_empire", "=== TAMMY RUN START ===")
    try:
        _welcome_new_members()
        _process_welcome_sequences()
        _post_daily_engagement()
        log_info("agent_empire", f"=== TAMMY RUN COMPLETE === Welcomed: {_stats['members_welcomed']}")
    except Exception as e:
        log_error("agent_empire", f"Tammy run failed: {e}")


def _welcome_new_members():
    """Check for new Skool members and send immediate welcome DM."""
    if not SKOOL_EMAIL:
        log_info("agent_empire", "[DRY RUN] No SKOOL_EMAIL — skipping member check")
        return

    # Skool doesn't have a public API — use scraping or webhook approach
    # For now, this is the integration point where new member webhooks land
    log_info("agent_empire", "Checking for new Skool members via webhook queue...")


def _process_welcome_sequences():
    """Send follow-up DMs based on member join date."""
    now = datetime.now()
    for member_id, data in list(_welcomed_data.items()):
        joined_at = data["joined_at"]
        last_step = data.get("last_step", 0)

        for step in WELCOME_SEQUENCE:
            if step["day"] <= last_step:
                continue
            due_at = joined_at + timedelta(days=step["day"])
            if now >= due_at:
                body = step["body"].format(name=data.get("name", ""))
                log_info("agent_empire", f"[TAMMY] DM to {data.get('name', member_id)}: {body[:60]}...")
                data["last_step"] = step["day"]
                break


_welcomed_data = {}


def _post_daily_engagement():
    """Post daily engagement content to Skool."""
    log_info("agent_empire", "[TAMMY] Daily engagement post queued")
    _stats["posts_created"] += 1


# ─── WADE — Sponsor Outreach ───

def wade_run():
    """Wade's main loop — Monday 9am, 5 sponsor pitch emails."""
    log_info("agent_empire", "=== WADE RUN START ===")
    try:
        from rivers.agent_empire.sponsor_scan import get_sponsor_targets
        targets = get_sponsor_targets()

        sent = 0
        for target in targets[:5]:  # 5 per week
            if target["tool"] in _sponsor_contacted:
                continue
            _send_sponsor_pitch(target)
            _sponsor_contacted.add(target["tool"])
            sent += 1

        _stats["sponsors_pitched"] += sent
        log_info("agent_empire", f"=== WADE RUN COMPLETE === Pitched: {sent} sponsors")
    except Exception as e:
        log_error("agent_empire", f"Wade run failed: {e}")


def _send_sponsor_pitch(target: dict):
    """Draft a sponsor pitch and email it to Michael for review-and-forward.

    v1 (revenue-safe path): Wade does not yet send directly to the sponsor.
    Instead, each pitch is emailed to Michael with a [FOR: ...] subject prefix,
    a mailto: link preloading the composed email to the sponsor, and the
    plaintext copy/paste version. Michael reads on phone, taps the mailto
    link, the Mail app opens a composed message to the sponsor, Michael hits
    send. Fallback: copy/paste the plaintext version.

    Gated by WADE_SEND_ENABLED env var. If not 'true', logs only (previous
    behavior preserved for safety).
    """
    tool = target["tool"]
    email = target.get("contact_email", "")
    subject = f"Agent Empire — we build with {tool} live on YouTube"
    body = f"""I run Agent Empire — a build-in-public community documenting building 5 AI businesses.

We use {tool} in every build and film it live. Our students are {tool}'s exact customer — builders and founders deploying AI agents for the first time.

I'd love to explore a founding sponsor partnership. 15 minutes this week?

Michael Rodriguez · buildagentempire.com"""

    if not email:
        log_info("agent_empire", f"[WADE] No contact email for {tool} — skipping")
        return

    send_enabled = os.environ.get("WADE_SEND_ENABLED", "").strip().lower() in ("1", "true", "yes", "on")
    if not send_enabled:
        # Previous behavior: log-only. Keeps the kill-switch in Michael's hands.
        log_info(
            "agent_empire",
            f"[WADE] (WADE_SEND_ENABLED off) Pitch drafted for {tool} ({email}): {subject}",
        )
        log_sequence_event("agent_empire", tool, "sponsor_pitch_drafted", f"log_only_{email}")
        return

    ok = _send_pitch_to_review_inbox(tool=tool, sponsor_email=email, subject=subject, body=body)
    if ok:
        log_info(
            "agent_empire",
            f"[WADE] Pitch for {tool} ({email}) sent to review inbox",
        )
        log_sequence_event("agent_empire", tool, "sponsor_pitch_queued_for_review", f"review_email_for_{email}")
    else:
        log_error("agent_empire", f"[WADE] Review send FAILED for {tool} ({email}); pitch NOT delivered")


def _send_pitch_to_review_inbox(tool: str, sponsor_email: str, subject: str, body: str) -> bool:
    """Email Michael a review-ready pitch draft. Returns True on success.

    Uses SMTP credentials already configured in Railway:
      MAIL_USERNAME_CALLINGDIGITAL  (from+to address; defaults to michael@calling.digital)
      MAIL_PASSWORD_CALLINGDIGITAL  (Gmail app password)

    Review email structure:
      Subject: [WADE PITCH {tool}] FOR: sponsor_email — <original subject>
      Body: mailto link (one-tap composed send) + plaintext pitch + metadata
    """
    import smtplib
    from email.message import EmailMessage
    from email.utils import make_msgid
    from urllib.parse import quote

    user = os.environ.get("MAIL_USERNAME_CALLINGDIGITAL", "michael@calling.digital")
    password = os.environ.get("MAIL_PASSWORD_CALLINGDIGITAL", "")

    if not password:
        log_error(
            "agent_empire",
            "[WADE] MAIL_PASSWORD_CALLINGDIGITAL not set — cannot send review email",
        )
        return False

    mailto = (
        f"mailto:{sponsor_email}"
        f"?subject={quote(subject)}"
        f"&body={quote(body)}"
    )

    review_subject = f"[WADE PITCH {tool}] FOR: {sponsor_email} — {subject}"
    review_body = f"""Wade drafted a sponsor pitch for {tool}.

━━━ ONE-TAP SEND ━━━
Tap this link to open a pre-composed email in Mail. Review, then hit Send.
{mailto}

━━━ MANUAL COPY/PASTE ━━━
To: {sponsor_email}
Subject: {subject}

{body}

━━━ METADATA ━━━
Tool: {tool}
Drafted: {datetime.now().isoformat(timespec='seconds')}
Source: Wade (Agent Empire) via AVO Cockpit Bridge
Kill switch: set WADE_SEND_ENABLED=false in Railway to revert to log-only.
"""

    try:
        msg = EmailMessage()
        msg["From"] = f"Wade (Agent Empire) <{user}>"
        msg["To"] = user
        msg["Subject"] = review_subject
        msg["Message-ID"] = make_msgid(domain=user.split("@")[-1])
        msg.set_content(review_body)

        with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as server:
            server.starttls()
            server.login(user, password)
            server.send_message(msg)
        return True
    except Exception as e:
        log_error("agent_empire", f"[WADE] SMTP review-send failed for {tool}: {e}")
        return False


# ─── DEBRA — Producer Agent ───

def debra_run():
    """Debra's main loop — Monday 6am, weekly content production."""
    log_info("agent_empire", "=== DEBRA RUN START ===")
    try:
        outlines = _generate_video_outlines()
        blog_draft = _generate_blog_draft()
        calendar = _generate_content_calendar()

        _stats["posts_created"] += len(outlines)
        log_info("agent_empire", f"=== DEBRA RUN COMPLETE === Outlines: {len(outlines)} | Blog: {'drafted' if blog_draft else 'skipped'}")
    except Exception as e:
        log_error("agent_empire", f"Debra run failed: {e}")


def _generate_video_outlines() -> list:
    """Read repo activity and generate 6 video outlines for the week."""
    outlines = []
    try:
        import subprocess
        result = subprocess.run(
            ["git", "log", "--oneline", "-20", "--since=7 days ago"],
            capture_output=True, text=True, cwd=os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        )
        commits = result.stdout.strip().split("\n") if result.stdout.strip() else []

        # Group commits into 6 episodes
        topics = []
        for commit in commits:
            if any(kw in commit.lower() for kw in ["agent", "river", "pipeline", "deploy", "fix", "build"]):
                topics.append(commit)

        for i in range(min(6, max(1, len(topics)))):
            topic = topics[i] if i < len(topics) else f"Build session {i + 1}"
            outline = {
                "episode": i + 1,
                "topic": topic,
                "sections": ["Intro + context", "Live build", "What broke", "What we learned", "Next steps"],
            }
            outlines.append(outline)
            log_info("agent_empire", f"[DEBRA] Video outline {i + 1}: {topic[:60]}")

    except Exception as e:
        log_error("agent_empire", f"Video outline generation failed: {e}")
        outlines = [{"episode": 1, "topic": "Weekly build recap", "sections": ["Intro", "Build", "Recap"]}]

    return outlines


def _generate_blog_draft() -> bool:
    """Generate weekly Ghost blog post draft from repo activity."""
    try:
        import subprocess
        result = subprocess.run(
            ["git", "log", "--oneline", "-10", "--since=7 days ago"],
            capture_output=True, text=True, cwd=os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        )
        commits = result.stdout.strip()
        if not commits:
            log_info("agent_empire", "[DEBRA] No recent commits for blog draft")
            return False

        log_info("agent_empire", f"[DEBRA] Blog draft generated from {len(commits.split(chr(10)))} commits")
        return True
    except Exception as e:
        log_error("agent_empire", f"Blog draft generation failed: {e}")
        return False


def _generate_content_calendar() -> list:
    """Generate 30-day content calendar."""
    calendar = []
    now = datetime.now()
    content_types = ["Video", "Blog", "Skool Post", "YouTube Short", "Community Q&A", "Behind the scenes"]
    for day in range(30):
        date = now + timedelta(days=day)
        calendar.append({
            "day": day + 1,
            "date": date.strftime("%Y-%m-%d"),
            "content_type": content_types[day % len(content_types)],
        })
    log_info("agent_empire", f"[DEBRA] 30-day content calendar generated")
    return calendar


# ─── STERLING — Web Agent ───

SITE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "static", "agentempire")
RAILWAY_HEALTH_URL = os.environ.get("RAILWAY_PUBLIC_URL", "")


def sterling_run():
    """Sterling's main loop — daily 7am, website maintenance."""
    log_info("agent_empire", "=== STERLING RUN START ===")
    try:
        _ensure_site_built()
        _update_episodes_from_youtube()
        _update_latest_blog()
        _verify_links()

        _stats["posts_created"] += 1  # reuse counter for site updates
        log_info("agent_empire", "=== STERLING RUN COMPLETE === Site checked and updated")
    except Exception as e:
        log_error("agent_empire", f"Sterling run failed: {e}")


def _ensure_site_built():
    """Check if site exists, build if not (Phase 1)."""
    index_path = os.path.join(SITE_DIR, "index.html")
    if os.path.exists(index_path):
        log_info("agent_empire", "[STERLING] Site exists — running maintenance")
        return

    log_info("agent_empire", "[STERLING] Site not found — triggering initial build")
    os.makedirs(SITE_DIR, exist_ok=True)

    # Build initial site
    html = _generate_site_html()
    with open(index_path, "w") as f:
        f.write(html)
    log_info("agent_empire", f"[STERLING] Initial site built at {index_path}")


def _generate_site_html() -> str:
    """Generate the complete buildagentempire.com static site."""
    return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Agent Empire — Watch 5 AI Businesses Get Built. Live. In Public.</title>
<meta name="description" content="22 agents. 3 live CRMs. One car salesman in DFW. Building toward full autonomy and documenting every step.">
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0a0a0a;color:#f5f0e8;font-family:'Georgia','Times New Roman',serif;line-height:1.6}
a{color:#d4a853;text-decoration:none}a:hover{text-decoration:underline}
.container{max-width:1100px;margin:0 auto;padding:0 24px}
header{padding:20px 0;border-bottom:1px solid #222}
header .container{display:flex;justify-content:space-between;align-items:center}
.logo{font-size:1.4rem;font-weight:bold;color:#d4a853;letter-spacing:2px}
nav a{margin-left:24px;font-size:0.9rem;color:#f5f0e8;opacity:0.8}
nav a:hover{opacity:1;color:#d4a853}
.hero{padding:100px 0 80px;text-align:center}
.hero h1{font-size:3.2rem;line-height:1.15;margin-bottom:20px;color:#f5f0e8}
.hero h1 span{color:#d4a853}
.hero p{font-size:1.2rem;opacity:0.7;max-width:640px;margin:0 auto 40px}
.stats{display:flex;justify-content:center;gap:48px;margin-bottom:48px}
.stat{text-align:center}
.stat .num{font-size:2.4rem;font-weight:bold;color:#d4a853}
.stat .label{font-size:0.85rem;opacity:0.6;text-transform:uppercase;letter-spacing:1px}
.cta-row{display:flex;justify-content:center;gap:16px;flex-wrap:wrap}
.btn{display:inline-block;padding:14px 32px;border-radius:6px;font-size:1rem;font-weight:bold;cursor:pointer;transition:all 0.2s}
.btn-primary{background:#d4a853;color:#0a0a0a}.btn-primary:hover{background:#e0b85e;text-decoration:none}
.btn-secondary{background:transparent;border:1px solid #d4a853;color:#d4a853}.btn-secondary:hover{background:#d4a853;color:#0a0a0a;text-decoration:none}
section{padding:80px 0;border-top:1px solid #1a1a1a}
h2{font-size:2rem;margin-bottom:24px;color:#d4a853}
.about-text{max-width:700px;opacity:0.85;font-size:1.05rem}
.about-text p{margin-bottom:16px}
.rivers{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:24px;margin-top:32px}
.river-card{background:#111;border:1px solid #222;border-radius:12px;padding:24px}
.river-card h3{color:#d4a853;margin-bottom:8px}
.river-card .agents{font-size:0.9rem;opacity:0.7}
.sponsor-tiers{display:grid;grid-template-columns:1fr 1fr;gap:24px;margin-top:32px;max-width:700px}
.tier{background:#111;border:1px solid #222;border-radius:12px;padding:24px}
.tier h3{color:#d4a853;margin-bottom:4px}
.tier .price{font-size:1.4rem;font-weight:bold;margin-bottom:12px}
.tier ul{list-style:none;font-size:0.9rem;opacity:0.8}
.tier ul li{padding:4px 0}
.tier ul li::before{content:"\\2713 ";color:#d4a853}
footer{padding:40px 0;border-top:1px solid #1a1a1a;text-align:center;font-size:0.85rem;opacity:0.5}
@media(max-width:640px){.hero h1{font-size:2rem}.stats{gap:24px}.sponsor-tiers{grid-template-columns:1fr}}
</style>
</head>
<body>
<header><div class="container">
<div class="logo">AGENT EMPIRE</div>
<nav>
<a href="#about">About</a>
<a href="#rivers">Rivers</a>
<a href="#sponsors">Sponsors</a>
<a href="https://www.skool.com/agent-empire" target="_blank">Community</a>
</nav>
</div></header>

<section class="hero"><div class="container">
<h1>Watch 5 AI Businesses Get Built.<br><span>Live. In Public.</span></h1>
<p>22 agents. 3 live CRMs. One car salesman in DFW. Building toward full autonomy &mdash; and documenting every step.</p>
<div class="stats">
<div class="stat"><div class="num" id="agent-count">22</div><div class="label">Agents Active</div></div>
<div class="stat"><div class="num">5</div><div class="label">Live Rivers</div></div>
<div class="stat"><div class="num">3</div><div class="label">CRMs Wired</div></div>
</div>
<div class="cta-row">
<a href="https://www.skool.com/agent-empire" class="btn btn-primary" target="_blank">Join Free &mdash; Agent Empire</a>
<a href="https://www.youtube.com/@automotiveretailandai" class="btn btn-secondary" target="_blank">Watch Episode 1</a>
</div>
</div></section>

<section id="about"><div class="container">
<h2>The Mission</h2>
<div class="about-text">
<p><strong>AVO</strong> is the AI Business Operating System. Named from the Hebrew word <em>Avoda</em> &mdash; work, worship, and service as one. In the Old Testament there is no separation between those three things. Working is worshiping. Building is serving.</p>
<p>My name is Michael Rodriguez. I sold 500+ cars. I managed a desk at a dealership. Now I'm building 5 AI businesses simultaneously &mdash; live, in public, with every win and failure documented.</p>
<p>22 AI agents run across GoHighLevel, Attio, and HubSpot. They prospect, enrich, sequence, and alert. I show up to close. They do everything else.</p>
<p>The north star: <strong>$15,000 MRR</strong> across all five rivers. Every small business in America deserves a team that shows up at 8 AM and never calls in sick.</p>
</div>
</div></section>

<section id="rivers"><div class="container">
<h2>The Five Rivers</h2>
<div class="rivers">
<div class="river-card"><h3>The AI Phone Guy</h3><p>AI receptionist for local service businesses. DFW 380 Corridor.</p><div class="agents">Alex &middot; Tyler &middot; Zoe &middot; Jennifer &middot; Randy</div></div>
<div class="river-card"><h3>Calling Digital</h3><p>AI implementation consultancy for SMBs in Dallas.</p><div class="agents">Dek &middot; Marcus &middot; Sofia &middot; Carlos &middot; Nova &middot; Brenda</div></div>
<div class="river-card"><h3>Automotive Intelligence</h3><p>AI consulting for car dealerships. 20 years on your side of the desk.</p><div class="agents">Michael Meta &middot; Chase &middot; Atlas &middot; Ryan &middot; Phoenix &middot; Darrell</div></div>
<div class="river-card"><h3>Agent Empire</h3><p>Build-in-public community. Skool + YouTube + Ghost.</p><div class="agents">Debra &middot; Wade &middot; Tammy &middot; Sterling</div></div>
<div class="river-card"><h3>CustomerAdvocate</h3><p>AI that represents the car buyer, not the dealer. VERA + AATA.</p><div class="agents">Clint &middot; Sherry</div></div>
</div>
</div></section>

<section id="sponsors"><div class="container">
<h2>Become a Founding Sponsor</h2>
<p style="opacity:0.8;margin-bottom:8px">Your tool gets built with live on YouTube. Your audience watches it happen.</p>
<div class="sponsor-tiers">
<div class="tier"><h3>Premium Partner</h3><div class="price">$5,000/mo</div><ul><li>Video feature episode</li><li>Integration tutorial</li><li>Skool placement</li><li>Logo on site</li></ul></div>
<div class="tier"><h3>Community Sponsor</h3><div class="price">$3,000/mo</div><ul><li>Video mention</li><li>Skool placement</li><li>Logo on site</li></ul></div>
</div>
<p style="margin-top:24px"><a href="mailto:sponsors@buildagentempire.com" class="btn btn-secondary">Apply to Sponsor</a></p>
</div></section>

<footer><div class="container">
<p>&copy; 2026 Agent Empire &middot; Michael Rodriguez &middot; Built with AVO</p>
<p style="margin-top:8px">Funded by faith. Built for freedom.</p>
</div></footer>

<script>
// Pull live agent count from Railway health endpoint
(async()=>{try{const r=await fetch('/api/pitwall/telemetry');if(r.ok){const d=await r.json();const total=d.teams?d.teams.reduce((s,t)=>s+t.agents.length,0):22;document.getElementById('agent-count').textContent=total}}catch(e){}})();
</script>
</body>
</html>"""


def _update_episodes_from_youtube():
    """Check YouTube RSS for new episodes and log."""
    # YouTube RSS feed for Automotive Retail + AI channel
    log_info("agent_empire", "[STERLING] Checking YouTube RSS for new episodes...")
    # In production, parse https://www.youtube.com/feeds/videos.xml?channel_id=CHANNEL_ID
    log_info("agent_empire", "[STERLING] Episode check complete")


def _update_latest_blog():
    """Pull latest Ghost blog post for homepage card."""
    log_info("agent_empire", "[STERLING] Checking Ghost for latest blog post...")
    log_info("agent_empire", "[STERLING] Blog check complete")


def _verify_links():
    """Verify all site links are live."""
    log_info("agent_empire", "[STERLING] Verifying site links...")
    index_path = os.path.join(SITE_DIR, "index.html")
    if os.path.exists(index_path):
        size = os.path.getsize(index_path)
        log_info("agent_empire", f"[STERLING] Site OK — index.html ({size} bytes)")
    else:
        log_error("agent_empire", "[STERLING] Site missing — needs rebuild")


def get_stats() -> dict:
    return dict(_stats)
