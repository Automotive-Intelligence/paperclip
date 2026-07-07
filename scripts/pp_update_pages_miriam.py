"""Paper & Purpose — apply Miriam's revised page copy to the 3 content pages.

Updates body_html for our-story, the-journal, and voice-note with Miriam's
2026-05-27 revision. Backs up the current bodies to scripts/outputs/ first.

Miriam's revision is a substantial rewrite:
  - day count changed 88 -> 90 (her new "Why 90 days" section reasons around it)
  - twelve section names fully rewritten
  - removed a likely-fabricated "Mexican abuela" personal detail (per the
    client-voice guardrail: never invent personal/family details)
  - new physical-spec phrasing and gift-box copy

Mechanical typo fixes applied to her text (everything else verbatim):
  - "quite time" -> "quiet time"            (our-story)
  - "HIs word"   -> "His Word"              (our-story)
  - "Cast Your Cares:Where" -> add space    (the-journal section 2)
  - collapsed a couple of stray double-spaces
Stylistic choices PRESERVED on purpose: "etc...", "girlies", "so whimsy",
"BIG", "What's the TRUTH!?", her sentence fragments.

Day count: pages now say 90 per Miriam. The product description and Klaviyo
Welcome 3 still say 88 — that contradiction is flagged for Michael, NOT
silently reconciled here.

Usage:
    python scripts/pp_update_pages_miriam.py --dry-run
    python scripts/pp_update_pages_miriam.py
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv(REPO_ROOT / ".env", override=True)
except ImportError:
    pass

from tools.shopify import _shop_for, _token_for, _api_version, _shopify_get  # noqa: E402

BUSINESS_KEY = "paperandpurpose"
OUTPUTS = REPO_ROOT / "scripts" / "outputs"

PAGE_IDS = {
    "our-story": "119881433176",
    "the-journal": "119881465944",
    "voice-note": "119881498712",
}

CTA = '<p><a href="/products/be-transformed-guided-mind-renewal-journal"><strong>Pre-order the journal</strong></a></p>'

OUR_STORY = """\
<p>I have a confession. I was uninspired by blank pages. For years.</p>
<p>I would buy a beautiful journal at the bookstore. Bring it home. Set it on my nightstand. And be uninspired to write in it. The blank page would just sit there, staring at me, waiting for me to be profound on cue.</p>
<p>And then there was the other thing. Re-reading past journals gave me anxiety. I would open one from two years ago and get second hand embarrassment. Because there I was. Spiraling. Crying out. Same prayer. Same fear. Same loop. I never got to see how God answered, because the pages were not built to track it. They were just pages.</p>
<p>So I stopped journaling. For a long time.</p>
<p>But the longing did not go away. The wanting-to-sit-with-Jesus did not go away. The needing-a-soft-place-to-land-after-a-9-to-5 did not go away.</p>
<p>I just needed something different. Something with a guide. Something that wasn't just a dumping ground for wrong thoughts without a re-direction to His Word. Something that let me hand my worries to Him and then show me later where He met me in it.</p>
<p>I could not find it. So I made it.</p>
<p>This journal is what I would have given my younger self. The one who was tired. The one who did not know where to start. The one who needed someone to say, here, start here, I have got you.</p>
<p>It is anchored in one verse. Romans 12:2. Do not be conformed to this world, but be transformed by the renewal of your mind. That is the whole thing. That is the prayer. That is the 90 days.</p>
<p>I am not a theologian. I am a woman with a 9-to-5, kids, ministry, etc... A woman who wants her quiet time with the Lord to be structured, beautiful, enjoyable, and most importantly, like a friend sitting next to you saying "What's the TRUTH!? But what does God's Word say about this?"</p>
<p>I made this for sisters like me.</p>
<p>If that is you, I would love for you to be part of this.</p>
""" + CTA

THE_JOURNAL = """\
<p>This is not a regular journal, and I want you to know exactly what you are saying yes to.</p>
<p><strong>Be Transformed by the Renewal of Your Mind.</strong> 90-day guided journal. Hardcover. Spiral binding with gold coils. 7.2" by 8.46". Sized for girlies who have BIG handwriting like me!</p>
<p>It comes in a keepsake gift box. The kind you'd hand to your sister and she'd think is so whimsy.</p>
<p>Inside, every page is colorful. Watercolor, butterflies and florals.</p>
<h3>The twelve guided sections</h3>
<p>You move through twelve named sections across the 90 days. You do not have to fill every one every day. Some days you cast your cares and that is the whole entry. Some days you fill every box. Both count.</p>
<ol>
  <li><strong>Verse of the Day:</strong> Start each day with a Bible verse centered around transformation.</li>
  <li><strong>Cast Your Cares:</strong> Where you leave the things you've been carrying.</li>
  <li><strong>Reflect and Correct:</strong> Where you tell yourself what scripture says about your cares.</li>
  <li><strong>Grateful For:</strong> Self-explanatory. Powerful.</li>
  <li><strong>Bible Verses Read:</strong> Where you dig into God's Word.</li>
  <li><strong>What I Learned About God:</strong> Memorialize it, this is who God is!</li>
  <li><strong>How Can I Apply This To My Life?</strong> Take a moment to reflect on how this Scripture can shape your thoughts, actions, or perspective today.</li>
  <li><strong>Biblical Affirmations:</strong> What do you need to remind yourself of today from the Word?</li>
  <li><strong>Still Small Voice:</strong> Where you write down what He's saying.</li>
  <li><strong>Prayers:</strong> Pray BIG, we serve a BIG God.</li>
  <li><strong>End of Day PTLs.</strong> Praise the Lord moments. Small ones count.</li>
  <li><strong>BIG DREAMS, BIG PRAYERS:</strong> Record ongoing prayers, dreams. Come back and see all He has done!</li>
</ol>
<h3>The keepsake elements</h3>
<ul>
  <li><strong>A divider with a built-in folder</strong> for the keepsakes you collect along the way. Ticket stubs, the note on the counter, the sonogram, whatever the Lord uses to mark these 90 days.</li>
  <li><strong>Perforated prayer tear-out cards.</strong> Write the prayer, tear it out, tuck it in your Bible, tape it to your mirror, hand it to a friend.</li>
  <li><strong>An answered-prayer envelope</strong> built into the back. The prayers you tear out today, you tuck back in here when the Lord answers them. Ninety days from now, you open an envelope full of answered prayers.</li>
</ul>
<h3>Why 90 days</h3>
<p>I tried 30. Not long enough. The renewal of your mind does not fit in a month. I tried 365. Too long, nobody finishes. 90 came out of prayer. Long enough for a habit to take root, short enough that you can see the finish line from the start. You can do hard things for 90 days.</p>
<p>This is a journal for the woman who wants to renew her mind and Be Transformed!</p>
""" + CTA

VOICE_NOTE = """\
<p>I recorded something for you. A voice-memo, from my couch.</p>
<p>I wanted to tell you a couple of things I was not sure I wanted to put in writing. So I just said them out loud instead.</p>
<p><em>[Audio embed goes here. Miriam to upload the 2-minute voice note via Shopify admin (Online Store, Files, upload MP3) and replace this placeholder with the audio player.]</em></p>
<p>If audio is not your thing right now, here is the short version. I am working a 9-to-5. This is the dream project, the thing I do at night, the thing I prayed about for years before I touched a design file. I am not a polished founder. I am getting on camera slowly, voice first, because that is what is honest.</p>
<p>Thank you for being here for the quiet beginning of this.</p>
""" + CTA

NEW_BODIES = {
    "our-story": OUR_STORY,
    "the-journal": THE_JOURNAL,
    "voice-note": VOICE_NOTE,
}


def put_page_body(page_id: str, body_html: str) -> tuple[bool, str]:
    """PUT the page body_html via Admin API. Returns (ok, message)."""
    shop = _shop_for(BUSINESS_KEY)
    token = _token_for(BUSINESS_KEY)
    url = f"https://{shop}.myshopify.com/admin/api/{_api_version()}/pages/{page_id}.json"
    headers = {
        "X-Shopify-Access-Token": token,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    payload = {"page": {"id": int(page_id), "body_html": body_html}}
    try:
        r = requests.put(url, headers=headers, json=payload, timeout=30)
    except requests.exceptions.RequestException as e:
        return False, f"{type(e).__name__}: {e}"
    if r.status_code == 200:
        return True, "ok"
    return False, f"HTTP {r.status_code}: {r.text[:300]}"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    OUTPUTS.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")

    # --- Backup current bodies first ---
    print("[1/3] Backing up current page bodies...")
    backup = {}
    for handle, pid in PAGE_IDS.items():
        resp = _shopify_get(BUSINESS_KEY, f"pages/{pid}.json")
        if isinstance(resp, dict):
            backup[handle] = {
                "id": pid,
                "body_html": resp.get("page", {}).get("body_html", ""),
                "title": resp.get("page", {}).get("title", ""),
            }
            print(f"      {handle}: {len(backup[handle]['body_html'])}b captured")
        else:
            print(f"      {handle}: BACKUP FAILED: {resp}")
            raise SystemExit("Aborting — could not back up before write.")
    backup_path = OUTPUTS / f"pp_pages_backup_{ts}.json"
    backup_path.write_text(json.dumps(backup, indent=2))
    print(f"      backup -> {backup_path}")
    print()

    # --- Apply new bodies ---
    print(f"[2/3] {'(dry-run) ' if args.dry_run else ''}Updating pages...")
    for handle, pid in PAGE_IDS.items():
        new_body = NEW_BODIES[handle]
        old_len = len(backup[handle]["body_html"])
        new_len = len(new_body)
        eighty_eights = new_body.count("88")
        print(f"  {handle} (id={pid}): {old_len}b -> {new_len}b  | '88' leftover in new copy: {eighty_eights}")
        if args.dry_run:
            continue
        ok, msg = put_page_body(pid, new_body)
        print(f"      PUT: {'ok' if ok else 'FAILED -> ' + msg}")
        time.sleep(0.3)
    print()

    if args.dry_run:
        print("Dry-run complete; nothing written.")
        return

    # --- Verify ---
    print("[3/3] Verifying live state...")
    all_ok = True
    for handle, pid in PAGE_IDS.items():
        resp = _shopify_get(BUSINESS_KEY, f"pages/{pid}.json")
        if not isinstance(resp, dict):
            print(f"  {handle}: verify GET failed")
            all_ok = False
            continue
        live = resp.get("page", {}).get("body_html", "")
        matches = live.strip() == NEW_BODIES[handle].strip()
        n88 = live.count("88")
        n90 = live.count("90")
        flag = "OK" if matches else "MISMATCH"
        if not matches:
            all_ok = False
        print(f"  {handle}: {flag}  live={len(live)}b  '88'={n88}  '90'={n90}")

    print()
    print("ALL PAGES UPDATED" if all_ok else "SOME PAGES FAILED VERIFY")


if __name__ == "__main__":
    main()
