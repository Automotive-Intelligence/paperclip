"""Paper & Purpose — load the 5 welcome-sequence emails into Klaviyo as templates.

Each email becomes one Klaviyo template (editor_type=CODE) with the email
body wrapped in minimal email-safe HTML. The subject lines, preview text,
send timing, and CTA URLs are NOT stored on the template (Klaviyo holds
those on the flow's email message, not on the template). They are written
to a metadata sidecar JSON file so flow assembly later is a paste job.

Source: deliverable 31_pp_email_welcome_sequence.md (Google Drive,
pasted by Michael 2026-05-26).

Usage:
    python scripts/pp_load_welcome_templates.py --dry-run
    python scripts/pp_load_welcome_templates.py

Idempotency: if a template with the same name already exists, the
script skips it. Re-running is safe.

Required env:
    KLAVIYO_API_KEY_PAPERANDPURPOSE
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv(REPO_ROOT / ".env")
except ImportError:
    pass

from tools.klaviyo import _klaviyo_request, _api_key_for  # noqa: E402

BUSINESS_KEY = "paperandpurpose"

# ----------------------------------------------------------------------
# Email-safe HTML scaffold
# ----------------------------------------------------------------------

WRAPPER_OPEN = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{name}</title>
</head>
<body style="margin:0; padding:0; background-color:#F2EDE4;">
<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="background-color:#F2EDE4;">
  <tr>
    <td align="center" style="padding:32px 16px;">
      <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="600" style="max-width:600px; background-color:#FFFFFF; padding:40px; border-radius:4px; font-family:Georgia, 'Cormorant Garamond', serif; font-size:17px; line-height:1.6; color:#2A2A26;">
        <tr><td>
"""

WRAPPER_CLOSE = """\
        </td></tr>
      </table>
      <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="600" style="max-width:600px; padding:24px 16px; font-family:Arial, sans-serif; font-size:12px; line-height:1.5; color:#6c6c6c; text-align:center;">
        <tr><td>
          Paper &amp; Purpose &middot; hello@paperandpurpose.co<br>
          You are receiving this because you signed up at paperandpurpose.co.<br>
          <a href="{{% unsubscribe %}}" style="color:#6c6c6c;">Unsubscribe</a>
        </td></tr>
      </table>
    </td>
  </tr>
</table>
</body>
</html>
"""

# Button style — used for each email's CTA. Bone Cream on Forest Olive per brand kit.
BUTTON_TEMPLATE = """\
<p style="margin:28px 0;">
  <a href="{url}" style="display:inline-block; padding:14px 28px; background-color:#4A5340; color:#F2EDE4; text-decoration:none; font-family:Arial, sans-serif; font-size:15px; font-weight:bold; letter-spacing:0.04em; border-radius:4px;">
    {label}
  </a>
</p>
"""

# Paragraph + blockquote styles inlined for email-client compatibility.
def p(text: str, italic: bool = False, muted: bool = False) -> str:
    style = "margin:0 0 18px 0;"
    if italic:
        style += " font-style:italic;"
    if muted:
        style += " color:#6c6c6c;"
    return f'<p style="{style}">{text}</p>'


def blockquote(text: str) -> str:
    return (
        '<blockquote style="margin:24px 0; padding:8px 0 8px 18px; '
        'border-left:3px solid #B89968; font-style:italic; color:#4A5340;">'
        f"{text}"
        "</blockquote>"
    )


def list_item(label: str, rest: str) -> str:
    return f'<li style="margin-bottom:10px;"><strong>{label}</strong> {rest}</li>'


# ----------------------------------------------------------------------
# Email bodies (verbatim from file 31, with no em-dashes inserted)
# ----------------------------------------------------------------------

EMAIL_1_BODY = "\n".join([
    p("Hi friend,"),
    p("I'm Miriam. The woman behind Paper &amp; Purpose."),
    p("Real quick. I want to tell you why this exists. Because if you're here, there's a good chance some of this story is yours too."),
    p("I was uninspired by blank pages."),
    p("I'd buy the pretty journal. The one with the linen cover. The one I saved on Pinterest for months. I'd write for a week, maybe two, and then it would sit on my nightstand. Unopened. Judging me."),
    p("By February, I'd give up. By December, I'd buy another one. Repeat."),
    p("And honestly? Re-reading the old ones gave me anxiety. Same prayers. Same struggles. Same circles. No movement. I'd close them faster than I'd opened them."),
    p("So I prayed about it. For two years. I asked the Lord what it would look like to actually be renewed. Not just to write more. To be transformed."),
    p("Romans 12:2 wouldn't leave me alone."),
    blockquote("Do not conform to the pattern of this world, but be transformed by the renewal of your mind."),
    p("That's the verse this whole thing is built on. 88 days. 392 pages. One verse. A guided path through the renewal of your mind. Not a Bible-study program. Not blank pages either. A guided journey."),
    p("I made the journal I wished I had."),
    p("You're going to hear more from me this week. I'll walk you through what's actually inside. The prayer cards. The answered-prayer envelope. The gold coils I obsessed over. The watercolor butterflies on every page because I refused to do black-and-white on the inside."),
    p("For now, welcome. I'm really glad you're here."),
    p("Miriam"),
])

EMAIL_1_PS = p("P.S. The pre-launch opens soon. 220 spots. $50 each. I'll tell you the rest later this week.", italic=True, muted=True)

EMAIL_2_BODY = "\n".join([
    p("Hi friend,"),
    p("Okay. Today I want to give you the tour. Because this isn't a regular journal. I want you to know what you're saying yes to."),
    p('<strong>Be Transformed by the Renewal of Your Mind.</strong> 88-day guided journal. 392 pages. Hardcover. Spiral binding with gold coils. 7.2" by 8.46". Sized to hold open in your hands without a fight.'),
    p("It comes in a keepsake gift box. The kind you'd hand to your sister and she'd cry before she opened it."),
    p("Inside, every page is colorful. Watercolor butterflies and florals throughout. I want to say this clearly: this is the only Christian journal on the market with this much color on the inside. Most are black-and-white. I refused."),
    p("<strong>12 named prompt sections</strong> guide you through the 88 days. A few of them:"),
    '<ul style="margin:0 0 20px 0; padding-left:20px;">' + "\n".join([
        list_item("Cast Your Cares.", "Where you leave the things you've been carrying."),
        list_item("Reflect and Correct.", "Where you tell the truth about the week."),
        list_item("Grateful For.", "Self-explanatory. Powerful."),
        list_item("Still Small Voice.", "Where you write down what He's saying. Quietly."),
        list_item("End of Day PTLs.", "Praise the Lord moments. Small ones count."),
    ]) + "</ul>",
    p("And then the keepsake elements. These were the parts I obsessed over:"),
    '<ul style="margin:0 0 20px 0; padding-left:20px;">' + "\n".join([
        list_item("A divider with a built-in folder", "for the keepsakes you collect along the way. Ticket stubs from the church retreat. The note your husband left on the counter. The sonogram. Whatever the Lord uses to mark these 88 days."),
        list_item("Perforated prayer tear-out cards.", "Write the prayer. Tear it out. Tuck it in your Bible. Give it to a friend. Tape it to your mirror."),
        list_item("An answered-prayer envelope.", "Built into the back. The prayers you tear out today, you tuck back in here when the Lord answers them. You'll have a record. You'll have proof."),
    ]) + "</ul>",
    p("I think about that last one a lot. Eighty-eight days from now, you're going to open an envelope full of answered prayers. That's the point."),
    p("Tomorrow I'll tell you why I chose 88 days and not 30. There's a reason."),
    p("Miriam"),
])

EMAIL_3_BODY = "\n".join([
    p("Hi friend,"),
    p("I want to tell you why this journal is 88 days. And why it's a journal, not a Bible-study book."),
    p("<strong>88 days.</strong>"),
    p("I tried 30. It wasn't long enough. The renewal of your mind doesn't fit in a month. I've lived that."),
    p("I tried 365. Too long. Nobody finishes. I've lived that too."),
    p("88 came out of prayer. It's three months, give or take. It's long enough for a habit to actually take root. Short enough that you can see the finish line from the beginning. You can do hard things for 88 days. That's the math."),
    p("<strong>Transformation. Not Bible study.</strong>"),
    p("Here's where I want to be really honest with you."),
    p("I love a good Bible study. Daily Grace Co does that beautifully. So does She Reads Truth. They are doing important work and I send women to them all the time."),
    p("This is not that."),
    p("This is for the woman who wants her faith to move. Not her notes. Not her highlighter. Her actual life. Her actual mind. Her actual relationships."),
    p("That's why every prompt in this journal pulls you toward action and reflection. Not commentary. Not exegesis. Just: What's the Lord saying to me right now? What am I carrying? What needs to change? What did He answer?"),
    p("A journal for the woman, not the theologian."),
    p("This is also where this journal is different from the other beautiful ones on the market. Well-Watered Women makes a gorgeous product. I own three. But the insides are black-and-white. I wanted color. I wanted butterflies. I wanted the inside to feel as alive as I want this season of my life to feel."),
    p("Colorful on the inside. That's the brand."),
    p("Pre-launch opens soon. I'll send the link in a couple of days. 220 women. $50 each. That's the goal that funds the print run."),
    p("I'd love for you to be one of them."),
    p("Miriam"),
])

EMAIL_4_BODY = "\n".join([
    p("Hi friend,"),
    p("I recorded something for you. Two minutes. Voice memo style. From my couch. The button at the bottom of this email plays it."),
    p("I want to tell you a couple of things in it that I wasn't sure I wanted to put in writing. Here's the short version, in case audio isn't your thing right now."),
    p("I'm working a 9-to-5. This is the dream project. The thing I do at night. The thing I prayed about for two years before I touched a design file. The thing my husband watched me chase down at the kitchen table while the rice was burning."),
    p("I'm not a polished founder. I'm not the woman with the perfect ring light and the perfect feed. I'm getting on camera slowly. Voice first, then face, then full Reels. I'm doing it on my own timeline because that's what's honest."),
    p("And I keep coming back to that thing I told you on day one."),
    p("Re-reading my past journals gave me anxiety."),
    p("I want this one to feel like the opposite. I want you to be able to flip back through 88 days and see the Lord move. I want you to be able to open the answered-prayer envelope and weep over what He did."),
    p("That's why I'm doing this."),
    p("If you want to hear me say it out loud, the voice note is below. If you don't, that's fine too. I'll be back in a couple of days with the pre-launch link."),
    p("Talk soon."),
    p("Miriam"),
])

EMAIL_4_PS = p("P.S. If you want to reply to this email and tell me what season you're in right now, I read every one. Promise.", italic=True, muted=True)

EMAIL_5_BODY = "\n".join([
    p("Hi friend,"),
    p("This is the one."),
    p("The pre-launch is open. I want to tell you exactly what's happening so you can decide if it's for you."),
    p("<strong>The goal: 220 women. $50 each.</strong>"),
    p("That number isn't random. 220 pre-orders at $50 funds the printer order. The first run is 1,000 units. I have to put $10K down to make the first print happen. The 220 women who reserve in the pre-launch are the ones who make this real."),
    p("That's the deal. You reserve now. I print in batch one. You get the journal in your hands first, ahead of the public launch."),
    p("<strong>What you're getting in the pre-launch bundle:</strong>"),
    '<ul style="margin:0 0 20px 0; padding-left:20px;">' + "\n".join([
        list_item("The journal.", "Be Transformed by the Renewal of Your Mind. 88 days. 392 pages. Hardcover with gold coil spiral. Watercolor butterflies and florals on every interior page. Keepsake gift-box packaging."),
        list_item("The pencil pouch.", "Pre-launch exclusive. Matches the journal. Holds your favorite pens, your tear-out prayer cards, the things you collect along the way."),
        list_item("The 12 named prompt sections", " &mdash; Cast Your Cares, Reflect and Correct, Grateful For, Still Small Voice, End of Day PTLs, and the rest."),
        list_item("The keepsake builds", " &mdash; perforated tear-out prayer cards, the divider folder for keepsakes, the answered-prayer envelope built into the back."),
    ]) + "</ul>",
    p("$50. Locked. The post-launch price will be higher."),
    p("<strong>Here's the honest part.</strong>"),
    p("I made this for myself first. Then I realized other women might need it too. The 220 women who pre-launch this with me are the ones who get to say they were here at the beginning. You're going to be in the first photo. You're going to be in the first thank-you note."),
    p("If this isn't your season, that's okay. You're still on this list. I'd love to keep writing to you."),
    p("If it is your season, reserve below. I'd be honored."),
    p("Miriam"),
])

EMAIL_5_PS = p("P.S. Romans 12:2. 88 days. One verse. If the Lord has been nudging you toward something different this year, this might be the doorway. I prayed over every page. I'd pray over yours too. Just hit reply and tell me your name.", italic=True, muted=True)


EMAILS = [
    {
        "name": "P&P Welcome 1 - Why I made this",
        "send_offset": "0 days (immediately on signup)",
        "subject_a": "I was uninspired by blank pages.",
        "subject_b": "Quick story (and a welcome).",
        "preview_a": "So I made the journal I wished I had.",
        "preview_b": "This is how Paper & Purpose started.",
        "cta_label": "Read the full story",
        "cta_url": "https://paperandpurpose.co/pages/our-story",
        "body_main": EMAIL_1_BODY,
        "body_postscript": EMAIL_1_PS,
    },
    {
        "name": "P&P Welcome 2 - What's inside the journal",
        "send_offset": "+1 day after Welcome 1",
        "subject_a": "Let me walk you through it.",
        "subject_b": "What's actually inside this thing.",
        "preview_a": "12 sections. Gold coils. Watercolor butterflies.",
        "preview_b": "A full tour of the 88-day journal.",
        "cta_label": "See the full product walkthrough",
        "cta_url": "https://paperandpurpose.co/pages/the-journal",
        "body_main": EMAIL_2_BODY,
        "body_postscript": "",
    },
    {
        "name": "P&P Welcome 3 - Why 88 days",
        "send_offset": "+3 days after signup",
        "subject_a": "Why 88 days (not 30).",
        "subject_b": "Transformation, not Bible-study homework.",
        "preview_a": "Romans 12:2 takes longer than a month.",
        "preview_b": "Here's what makes this journal different.",
        "cta_label": "Get on the pre-launch list",
        "cta_url": "https://paperandpurpose.co/products/be-transformed-guided-mind-renewal-journal",
        "body_main": EMAIL_3_BODY,
        "body_postscript": "",
    },
    {
        "name": "P&P Welcome 4 - Voice note from Miriam",
        "send_offset": "+5 days after signup",
        "subject_a": "Hi. Voice memo from me.",
        "subject_b": "Real talk. From my couch.",
        "preview_a": "A two-minute audio note from Miriam.",
        "preview_b": "The part I wasn't sure I wanted to share.",
        "cta_label": "Listen to the voice note (2 min)",
        "cta_url": "https://paperandpurpose.co/pages/voice-note",
        "body_main": EMAIL_4_BODY,
        "body_postscript": EMAIL_4_PS,
    },
    {
        "name": "P&P Welcome 5 - Pre-launch invite",
        "send_offset": "+7 days after signup",
        "subject_a": "It's open. 220 spots. $50.",
        "subject_b": "Pre-launch is live (and this is the moment).",
        "preview_a": "Reserve your journal. This is the batch that funds the print run.",
        "preview_b": "Pencil pouch included. Read this first.",
        "cta_label": "Reserve your journal - $50",
        "cta_url": "https://paperandpurpose.co/products/be-transformed-guided-mind-renewal-journal",
        "body_main": EMAIL_5_BODY,
        "body_postscript": EMAIL_5_PS,
    },
]


# ----------------------------------------------------------------------
# Render + load
# ----------------------------------------------------------------------

def build_html(email: dict) -> str:
    button = BUTTON_TEMPLATE.format(url=email["cta_url"], label=email["cta_label"])
    inner = "\n".join(filter(None, [email["body_main"], button, email["body_postscript"]]))
    return WRAPPER_OPEN.format(name=email["name"]) + inner + WRAPPER_CLOSE


def existing_template_names() -> set[str]:
    """Return the names of templates already in the account, for idempotency."""
    resp = _klaviyo_request("GET", BUSINESS_KEY, "templates/")
    if isinstance(resp, str) and resp.startswith("ERROR"):
        raise SystemExit(resp)
    names = set()
    for t in resp.get("data", []):
        attrs = t.get("attributes", {})
        if attrs.get("name"):
            names.add(attrs["name"])
    return names


def create_template(email: dict) -> tuple[str | None, str | None]:
    """Return (template_id, error_message)."""
    html = build_html(email)
    body = {
        "data": {
            "type": "template",
            "attributes": {
                "name": email["name"],
                "editor_type": "CODE",
                "html": html,
            }
        }
    }
    resp = _klaviyo_request("POST", BUSINESS_KEY, "templates/", json_body=body)
    if isinstance(resp, str) and resp.startswith("ERROR"):
        return None, resp
    if isinstance(resp, dict):
        tid = resp.get("data", {}).get("id")
        if tid:
            return tid, None
    return None, f"unexpected response shape: {str(resp)[:300]}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would happen without writing to Klaviyo.")
    args = parser.parse_args()

    if not _api_key_for(BUSINESS_KEY):
        raise SystemExit(
            "ERROR: KLAVIYO_API_KEY_PAPERANDPURPOSE not set. "
            "Source paperclip/.env first."
        )

    print(f"=== Paper & Purpose welcome-sequence template loader (dry-run={args.dry_run}) ===")
    print()

    existing = set()
    if not args.dry_run:
        existing = existing_template_names()
        print(f"Existing templates in account: {len(existing)}")
        for n in sorted(existing):
            print(f"  - {n}")
        print()

    results = []
    for i, email in enumerate(EMAILS, 1):
        print(f"Email {i}: {email['name']}")
        if args.dry_run:
            html = build_html(email)
            print(f"  (dry-run) body HTML: {len(html)} chars")
            print(f"  subject A: {email['subject_a']}")
            print(f"  subject B: {email['subject_b']}")
            print(f"  CTA: {email['cta_label']} -> {email['cta_url']}")
            results.append({
                "template_id": None,
                "html_bytes": len(html),
                **{k: email[k] for k in ("name", "send_offset", "subject_a", "subject_b",
                                          "preview_a", "preview_b", "cta_label", "cta_url")},
            })
            print()
            continue

        if email["name"] in existing:
            print(f"  SKIP (template with this name already exists)")
            results.append({
                "template_id": None,
                "skipped_reason": "already_exists",
                **{k: email[k] for k in ("name", "send_offset", "subject_a", "subject_b",
                                          "preview_a", "preview_b", "cta_label", "cta_url")},
            })
            print()
            continue

        tid, err = create_template(email)
        if err:
            print(f"  FAIL: {err}")
            results.append({"template_id": None, "error": err, **email})
        else:
            print(f"  created -> template_id={tid}")
            results.append({
                "template_id": tid,
                **{k: email[k] for k in ("name", "send_offset", "subject_a", "subject_b",
                                          "preview_a", "preview_b", "cta_label", "cta_url")},
            })
        print()

    # Sidecar metadata file for flow assembly.
    outputs_dir = REPO_ROOT / "scripts" / "outputs"
    outputs_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    suffix = "_DRYRUN" if args.dry_run else ""
    sidecar = outputs_dir / f"pp_welcome_templates_{ts}{suffix}.json"
    sidecar.write_text(json.dumps(results, indent=2))
    print(f"Metadata sidecar: {sidecar}")


if __name__ == "__main__":
    main()
