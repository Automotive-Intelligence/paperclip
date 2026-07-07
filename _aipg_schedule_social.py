import os, sys, json
sys.path.insert(0, os.path.expanduser("~/paperclip/tools"))
import ghl

IMG = "https://ai-phone-guy-site.vercel.app/social-images/"
PUBLISH = os.getenv("PUBLISH") == "1"
ONLY = os.getenv("ONLY", "").strip()  # comma list of post numbers to act on
loc = os.getenv("GHL_LOCATION_ID", "").strip()
user_id = os.getenv("GHL_USER_ID", "").strip()

# 12 posts: (n, image, scheduleDate Central, summary)
TZ = "-05:00"  # Central Daylight
def dt(d): return f"{d}T09:00:00{TZ}"

POSTS = [
 (1, "01-missed-it.png", dt("2026-06-29"),
  "The call you missed at 2:14 today is parked in your competitor's calendar now.\n"
  "You were on a ladder. You didn't do anything wrong.\n"
  "That call still hung up and dialed the next guy.\n"
  "The Guy picks up so it never gets there.\n\n"
  "Hear The Guy answer your calls. Link in bio.\n\n"
  "#HVAC #Plumbing #HomeServices #MissedCall #ContractorLife"),
 (4, "04-meet-the-guy.png", dt("2026-07-01"),
  "Hi. I'm The Guy.\n"
  "I answer your phone, ask the right questions, and put the job on your calendar.\n"
  "Then I text you the details so you're never caught off guard.\n"
  "You swing the wrench. I'll work the phones.\n\n"
  "Meet The Guy. Link in bio.\n\n"
  "#AIPhoneGuy #HomeServices #Contractors #Trades #Receptionist"),
 (5, "05-text-back.png", dt("2026-07-03"),
  "Playbook tip: every missed call gets a text back inside the minute.\n"
  "A silent voicemail box loses the lead. A quick \"saw your call, when works?\" keeps it.\n"
  "The Guy does the texting for you.\n\n"
  "Grab the free Missed-Call Playbook. Link in bio.\n\n"
  "#MissedCallPlaybook #HomeServices #Electrician #Trades #LeadGen"),
 (2, "02-clock-out.png", dt("2026-07-06"),
  "Your phone doesn't clock out when you do.\n"
  "Homeowners call at 9pm with a flooded basement, and somebody answers.\n"
  "Make sure it's your number, not theirs.\n"
  "The Guy works nights, weekends, and holidays.\n\n"
  "See The Guy answer your calls. Link in bio.\n\n"
  "#Plumbing #EmergencyService #AfterHours #Trades #SmallBusiness"),
 (7, "07-done-for-you.png", dt("2026-07-08"),
  "You don't have time to build a phone system. That's the point.\n"
  "We set the whole thing up for you. You just take the booked jobs.\n"
  "Done-for-you, top to bottom.\n\n"
  "Let us set it up for you. Link in bio.\n\n"
  "#DoneForYou #HomeServices #HVAC #SmallBusiness #Contractors"),
 (3, "03-answer-first.png", dt("2026-07-10"),
  "Homeowners don't shop around. They call till someone picks up.\n"
  "First voice they hear usually gets the job.\n"
  "Be first. Every time. On every channel.\n\n"
  "Want to answer first? Watch The Guy work. Link in bio.\n\n"
  "#Roofing #HomeServices #LeadGen #Contractors #TradeLife"),
 (6, "06-garage.png", dt("2026-07-13"),
  "Garage door stuck with a car trapped inside is a today problem, not a Monday problem.\n"
  "If you don't pick up, that homeowner finds someone who does.\n"
  "The Guy answers, qualifies, and books it while you finish the install you're on.\n\n"
  "See The Guy answer your calls. Link in bio.\n\n"
  "#GarageDoor #HomeServices #Contractors #MissedCall #Trades"),
 (10, "10-no-bad-mood.png", dt("2026-07-15"),
  "People ask if The Guy gets tired.\n"
  "Nope. No lunch break, no bad mood, no \"let me call you back.\"\n"
  "Every caller gets the same sharp answer at 6am or midnight.\n"
  "That's the job.\n\n"
  "Meet The Guy. Link in bio.\n\n"
  "#AIPhoneGuy #HomeServices #AfterHours #Trades #Contractors"),
 (9, "09-qualify.png", dt("2026-07-17"),
  "Playbook tip: qualify before you quote.\n"
  "Asking the right two or three questions up front saves you the drive to a job that was never real.\n"
  "The Guy qualifies every caller before it hits your calendar.\n\n"
  "Grab the free Missed-Call Playbook. Link in bio.\n\n"
  "#MissedCallPlaybook #HVAC #Contractors #Trades #HomeServices"),
 (8, "08-multi-channel.png", dt("2026-07-20"),
  "Leads don't all come through the phone anymore.\n"
  "Some call. Some message your page. Some hit the chat on your site.\n"
  "The Guy answers all of it, qualifies the lead, and books the job.\n"
  "One voice across every channel.\n\n"
  "See The Guy work every channel. Link in bio.\n\n"
  "#HomeServices #MultiChannel #Roofing #LeadGen #Trades"),
 (11, "11-electrical.png", dt("2026-07-22"),
  "Power's out and the panel's sparking. They're not leaving a voicemail.\n"
  "They're calling the next electrician on the list.\n"
  "The Guy answers after hours so that next call is still yours.\n\n"
  "See The Guy answer your calls. Link in bio.\n\n"
  "#Electrician #EmergencyService #AfterHours #HomeServices #Trades"),
 (12, "12-roofing.png", dt("2026-07-24"),
  "Every call you miss is a job somebody else just booked.\n"
  "You can't be on the roof and on the phone at the same time.\n"
  "The Guy can. He answers, qualifies, books, and texts you the details.\n\n"
  "Hear The Guy answer your calls. Link in bio.\n\n"
  "#Roofing #HomeServices #MissedCall #Contractors #DoneForYou"),
]

accts = ghl._get_ghl_social_accounts(loc)
# TikTok only accepts video posts; these are image posts -> exclude it.
img_accts = [a for a in accts if a.get("platform") != "tiktok"]
account_ids = [a.get("id") or a.get("_id") for a in img_accts]
print(f"loc={bool(loc)} user_id={bool(user_id)} image_accounts={len(account_ids)} (tiktok excluded) mode={'PUBLISH' if PUBLISH else 'DRY'}")
for a in img_accts:
    print("   ", a.get("platform"))

only = set(int(x) for x in ONLY.split(",") if x.strip()) if ONLY else None
for n, img, sched, summary in POSTS:
    if only and n not in only:
        continue
    media_url = IMG + img
    print(f"\n[{n:>2}] {sched}  {img}")
    print("     ", summary.split(chr(10))[0][:70])
    if not PUBLISH:
        continue
    payload = {
        "accountIds": account_ids,
        "summary": summary,
        "status": "scheduled",
        "scheduleDate": sched,
        "type": "post",
        "media": [{"url": media_url, "type": "image/png"}],
    }
    if user_id:
        payload["userId"] = user_id
    try:
        d = ghl._ghl_request("sched_social", "POST", f"/social-media-posting/{loc}/posts",
                             json_body=payload, timeout=25, api_version="2021-07-28")
        rec = d.get("post", d) if isinstance(d, dict) else {}
        pid = rec.get("_id") or rec.get("id") or d.get("id")
        nmedia = len(rec.get("media") or [])
        print(f"     -> OK id={pid} media_attached={nmedia} status={rec.get('status')}")
    except Exception as e:
        print("     -> ERROR:", str(e)[:220])
