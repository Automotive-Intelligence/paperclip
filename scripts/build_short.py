#!/usr/bin/env python3
"""Slipstream video leg — one engine, every brand.

Generalised out of the AIPG build (v1..v6). Everything that was hard-won there is baked in:

  VOICE      Michael's locked clone. Generated in BLOCKS and spliced with REAL silence, so a
             beat can be placed anywhere (ElevenLabs will not breathe on its own). The splice
             re-encodes; concatenating MP3s with -c copy carries encoder delay into the timeline
             and the silence silently vanishes.
  CAPTIONS   Brand's own display face, ALL CAPS, 3 words at a time, the spoken word lit in the
             brand's accent colour. Driven by ElevenLabs character timestamps, so the highlight
             tracks the actual voice instead of a guess.
  CUTS       One cut per spoken line. Push-in/out and a framing offset per cut. Never the same
             clip back to back. Slow-mo hard-capped at 2x, past which footage reads as frozen.
  FPS        Source fps is normalised BEFORE zoompan. Miss this and every cut runs short and the
             picture slides out from under the voice.
  FOOTAGE    Only clips that have passed the gate. A clip is never cut past its live window.

Usage:  python3 build_short.py wd
"""
import base64, json, os, shutil, subprocess, sys, pathlib
from PIL import Image, ImageDraw, ImageFont, ImageFilter

HOME  = pathlib.Path.home()
FONTS = HOME / "avo-telemetry" / "assets" / "fonts"
LIB   = HOME / "stock_library"
VOICE = "LJLkEwKPBENf1OfZKoqy"                    # locked. Jennifer-approved. Do not change.
W, H, FPS = 1080, 1920, 30

# ---------------------------------------------------------------- brands
# Tokens pulled from each brand's live site, not invented.
BRANDS = {
    "wd": {
        "name": "Worship Digital",
        "font": FONTS / "InterTight.ttf",
        "base": (245, 245, 245),                  # #f5f5f5
        "accent": (212, 175, 55),                 # #D4AF37 gold
        "logo": HOME / "worship-digital-site/public/WD-Logo.png",   # the real mark off the live site
        "out": HOME / "avo-telemetry/marketing_deliverables/116_video_leg_activation/renders/wd_v2_9x16.mp4",
    },
    "aipg": {
        "name": "The AI Phone Guy",
        "font": FONTS / "Archivo.ttf",
        "base": (247, 244, 239),                  # #f7f4ef
        "accent": (232, 119, 46),                 # #e8772e
        "logo": HOME / "ai-phone-guy-site/public/aipg-logo.png",
        "out": HOME / "avo-telemetry/marketing_deliverables/116_video_leg_activation/renders/aipg_v7_9x16.mp4",
    },
    "avi": {
        "name": "Automotive Intelligence",
        "font": FONTS / "InterTight.ttf",         # --font-inter-tight, the site's display face
        "base": (232, 234, 237),                  # --fg #e8eaed
        "accent": (45, 212, 191),                 # --signal-cyan #2dd4bf (read from globals.css 2026-07-12)
        "logo": HOME / "automotive-intelligence-site/public/logo.png",        # full wordmark -> end card
        "mark": HOME / "automotive-intelligence-site/public/logo-mark.png",   # "AI" bubble -> corner
        "out": HOME / "avo-telemetry/marketing_deliverables/116_video_leg_activation/renders/avi_v1_9x16.mp4",
    },
    "agent_empire": {
        "name": "Agent Empire",
        "font": FONTS / "SpaceGrotesk.ttf",       # site display face (layout.tsx); variable max = Bold, no Black
        "weight": "Bold",
        "base": (230, 237, 247),                  # --fg #e6edf7
        "accent": (56, 189, 248),                 # --signal-cyan #38bdf8 (globals.css 2026-07-12)
        "logo": HOME / "avo-telemetry/assets/brand/bae_lockup_cyan.png",      # crown + AGENT EMPIRE, in the SITE's cyan
                                                                       # (the gold crown never matched buildagentempire.com. FLAGGED.)
        "mark": HOME / "avo-telemetry/assets/brand/bae_crown_cyan.png",
        "out": HOME / "avo-telemetry/marketing_deliverables/116_video_leg_activation/renders/bae_v1_9x16.mp4",
    },
    "bookd": {
        "name": "Book'd",
        "font": FONTS / "Montserrat.ttf",         # brand type per kit
        "base": (255, 255, 255),                  # site bg white
        "accent": (2, 159, 179),                  # #029FB3 primary cyan
        # real wordmark: bookd.cx header SVG (== bookd-site/public/img/logo.svg), rendered cyan
        "logo": HOME / "avo-telemetry/assets/brand/bookd_logo_cyan.png",
        "out": HOME / "avo-telemetry/marketing_deliverables/116_video_leg_activation/renders/bookd_v1_9x16.mp4",
    },
}

# ---------------------------------------------------------------- scripts
# No em-dashes in outbound copy. WD is a full-service SMB agency, worldwide. Not faith-centred.
SCRIPTS = {
    "wd": {
        "segments": [
            ("You did not open your shop to learn marketing.",                 None),
            ("You opened it because you are good at the thing you do.",        None),
            ("But the customer looking for you right now is not finding you.", None),
            ("He is finding somebody else.",                                   None),
            ("I'm Michael. I run Worship Digital.",                            None),
            ("We build the site, the search presence, and the follow up,",     None),
            ("so the work you already do gets found.",                         None),
            ("No retainer games. No jargon.",                                  None),
            ("Just the phone ringing.",                                        None),
            ("worshipdigital.co",                                              "worshipdigital.co"),
        ],
        "pause_after": {3: 0.7},                  # beat after "He is finding somebody else."
        # (clip stem, in-point, tightness, frame-x, frame-y, zoom). Never the same clip adjacent.
        "cuts": [
            # US-footage gate 2026-07-15 (Michael): the bazaar clip (30277801) and the
            # Peluqueria-signage barber (8867391) are OUT of the pool. Visual gate, not filenames.
            ("barber_shop_owner__pexels_7697539",        1.0, 1.00, 0.50, 0.45, "IN"),
            ("restaurant_owner_kitchen__pexels_4252795", 3.0, 1.15, 0.50, 0.45, "IN"),
            ("shop_owner_opening_store_morning__pexels_6649982", 8.0, 1.05, 0.45, 0.50, "IN"),
            ("restaurant_owner_kitchen__pexels_4253321", 4.0, 1.30, 0.45, 0.50, "OUT"),
            ("barber_cutting_hair__pexels_7426668",      2.0, 1.05, 0.50, 0.45, "IN"),
            ("small_business_owner_shop__pexels_8428492", 12.0, 1.20, 0.55, 0.50, "OUT"),
            ("restaurant_owner_kitchen__pexels_4252795", 18.0, 1.35, 0.50, 0.45, "IN"),
            ("barber_shop_owner__pexels_7697539",        8.0, 1.25, 0.45, 0.50, "IN"),
            ("small_business_owner_shop__pexels_8428492", 30.0, 1.10, 0.50, 0.50, "IN"),
            ("shop_owner_opening_store_morning__pexels_6649434", 9.0, 1.00, 0.50, 0.50, "OUT"),
        ],
    },
    # AvI ICP = dealer principals / GMs / BDC managers. 4 gated clips rotate (reuse is fine,
    # never adjacent, different in-points per reuse). The 8 PM story mirrors the live site's
    # own hero narrative (the Tahoe text that fell between systems), so it is on-ledger.
    "avi": {
        "segments": [
            ("A customer texted your store at 8 PM last night.",            None),
            ("Somebody saw it. Nobody answered it.",                        None),
            ("This morning he bought the same truck across town.",          None),
            ("Not because your people are bad.",                            None),
            ("Because your systems don't talk to each other.",              None),
            ("I'm Michael. I've sold cars for twenty years,",               None),
            ("and I still sell cars for a living.",                         None),
            ("Automotive Intelligence wires your store's systems together,", None),
            ("so the next 8 PM lead gets answered while it's still hot.",   None),
            ("Built by somebody who actually sells cars.",                  None),
            ("automotiveintelligence.io",                                   "automotiveintelligence.io"),
        ],
        "pause_after": {1: 0.7},                  # beat after "Nobody answered it."
        "cuts": [
            ("car_mechanic_service__pexels_4488716",   1.0, 1.05, 0.50, 0.45, "IN"),
            ("car_dealership_showroom__pexels_6817082", 2.0, 1.10, 0.50, 0.45, "IN"),
            ("car_salesman_customer__pexels_7154237",  5.0, 1.05, 0.50, 0.45, "IN"),
            ("car_salesman_customer__pexels_7154241",  1.0, 1.00, 0.50, 0.45, "IN"),
            ("car_mechanic_service__pexels_4488716",   8.0, 1.15, 0.45, 0.45, "OUT"),
            ("car_salesman_customer__pexels_7154241",  8.0, 1.10, 0.55, 0.45, "IN"),
            ("car_dealership_showroom__pexels_6817082", 8.0, 1.05, 0.50, 0.50, "IN"),
            ("car_mechanic_service__pexels_4488716",   4.0, 1.00, 0.50, 0.45, "IN"),
            ("car_salesman_customer__pexels_7154241",  5.0, 1.15, 0.50, 0.45, "OUT"),
            ("car_salesman_customer__pexels_7154237",  1.0, 1.05, 0.50, 0.45, "IN"),
            ("car_dealership_showroom__pexels_6817082", 5.0, 1.10, 0.50, 0.45, "IN"),
        ],
    },
    # Agent Empire ICP = operators with day jobs building before work. Anti-guru voice: the only
    # income language allowed is NEGATING guru income claims. "Sell cars" line is the brand's own
    # public positioning (AvI hero: "All built while I sell cars for a living").
    # 7605040 is live 0-4s ONLY (toast-eating after); 4064867 subject enters ~2s; 20616932 ~5s.
    "agent_empire": {
        "segments": [
            ("It's 6:40 in the morning and I'm already building.",          None),
            ("Not because a guru told me to.",                              None),
            ("Because at eight I go sell cars, and that job funds all of it.", None),
            ("I build AI agents that do real work,",                        None),
            ("the kind with logs, and bugs, and receipts.",                 None),
            ("No course. No lambo. No six figure screenshot.",              None),
            ("Just the build, shared while it happens.",                    None),
            ("If you have a day job and a stubborn streak,",                None),
            ("come build with us.",                                         None),
            ("buildagentempire.com",                                        "buildagentempire.com"),
        ],
        "pause_after": {1: 0.6},                  # beat after "Not because a guru told me to."
        "cuts": [
            ("man_drinking_coffee_laptop_early_morning__pexels_20616932", 5.0, 1.05, 0.50, 0.45, "IN"),
            ("man_in_hoodie_working_laptop_desk__pexels_4064867",        2.5, 1.10, 0.50, 0.45, "IN"),
            ("working_late_laptop_night__pexels_6614783",                1.0, 1.05, 0.50, 0.45, "IN"),
            ("programmer_coding_laptop_home_night__pexels_5495899",      1.0, 1.00, 0.50, 0.45, "IN"),
            ("code_on_computer_screen_close_up__pexels_8720756",         2.0, 1.10, 0.45, 0.40, "IN"),
            ("man_in_hoodie_working_laptop_desk__pexels_6321248",        4.0, 1.05, 0.50, 0.45, "OUT"),
            ("man_in_hoodie_working_laptop_desk__pexels_5483089",        1.0, 1.00, 0.50, 0.45, "IN"),
            ("man_drinking_coffee_laptop_early_morning__pexels_10223724", 3.0, 1.10, 0.50, 0.45, "IN"),
            ("man_drinking_coffee_laptop_early_morning__pexels_20616932", 8.0, 1.15, 0.50, 0.45, "IN"),
            ("man_drinking_coffee_laptop_early_morning__pexels_7605040",  0.5, 1.05, 0.50, 0.45, "IN"),
        ],
    },
    # Book'd ICP = licensed life-insurance agents (file 88 / bookd.cx / Ryan's repo), NOT barbers;
    # the first bookd stock pull was off-ICP and got re-homed to WD 2026-07-12. Voice = Michael as
    # co-founder (either-co-founder rule, owner-corrected 2026-06-29); Ryan credited as the
    # licensed agent. Compliance: no pricing, no income/outcome/guarantee, no "runs itself".
    "bookd": {
        "segments": [
            ("That lead you paid for this morning?",                            None),
            ("Somebody else already called them back.",                         None),
            ("You were with a client, doing the actual job.",                   None),
            ("The follow up you meant to send is still in your head.",          None),
            ("I'm Michael. Ryan and I run Book'd.",                             None),
            ("Ryan is a licensed life insurance agent,",                        None),
            ("and Book'd is the system he built to work his own leads.",        None),
            ("It follows up the moment a lead comes in,",                       None),
            ("and keeps the compliance record as it goes.",                     None),
            ("No chasing. No guesswork. Just leads worked the right way.",      None),
            ("bookd.cx",                                                        "bookd.cx"),
        ],
        "pause_after": {1: 0.7},                  # beat after "Somebody else already called them back."
        "cuts": [
            ("agent_phone_call_office_desk__pexels_6099808",           1.0, 1.05, 0.50, 0.45, "IN"),
            ("man_working_late_office_laptop_phone__pexels_8120358",   2.0, 1.15, 0.50, 0.45, "IN"),
            ("insurance_agent_client_meeting__pexels_7735909",         1.0, 1.00, 0.50, 0.45, "IN"),
            ("paperwork_desk_professional__pexels_6100900",            6.5, 1.10, 0.50, 0.45, "IN"),
            ("insurance_agent_client_meeting__pexels_6931669",         1.0, 1.00, 0.50, 0.45, "IN"),
            ("insurance_agent_client_meeting__pexels_8479285",         2.0, 1.05, 0.50, 0.45, "OUT"),
            ("man_working_late_office_laptop_phone__pexels_7262667",   1.0, 1.05, 0.50, 0.50, "IN"),
            ("agent_phone_call_office_desk__pexels_8061672",           4.0, 1.15, 0.50, 0.45, "IN"),
            ("financial_advisor_meeting_couple__pexels_7691601",       1.0, 1.00, 0.50, 0.45, "IN"),
            ("signing_documents_contract_office__pexels_34719410",     3.0, 1.05, 0.50, 0.50, "OUT"),
            ("signing_documents_contract_office__pexels_8731553",      2.0, 1.10, 0.50, 0.45, "IN"),
        ],
    },
}

brand = sys.argv[1] if len(sys.argv) > 1 else "wd"
B, S  = BRANDS[brand], SCRIPTS[brand]
SEGMENTS, PAUSE_AFTER, CUTS = S["segments"], S["pause_after"], S["cuts"]
OUT   = B["out"]; OUT.parent.mkdir(parents=True, exist_ok=True)
WORK  = OUT.parent / f"_{brand}_work"; WORK.mkdir(exist_ok=True)
CACHE = WORK / "alignment.json"
VO    = WORK / "vo.mp3"

man = json.loads((LIB / "manifest.json").read_text())
def clip_path(stem):
    p = LIB / brand / f"{stem}.mp4"
    if not p.exists():
        sys.exit(f"missing gated clip: {p}")
    return p
def clip_len(stem):
    for v in man.values():
        if pathlib.Path(v["file"]).stem == stem:
            return v["duration"]
    return float(subprocess.run(["ffprobe","-v","error","-show_entries","format=duration",
        "-of","default=nw=1:nk=1",str(clip_path(stem))],capture_output=True,text=True).stdout)

# ---------------------------------------------------------------- VO in blocks + real silence
def adur(p):
    return float(subprocess.run(["ffprobe","-v","error","-show_entries","format=duration",
        "-of","default=nw=1:nk=1",str(p)],capture_output=True,text=True).stdout)

def tts(text):
    body = json.dumps({"text": text, "model_id": "eleven_multilingual_v2",
                       "voice_settings": {"stability":0.45,"similarity_boost":0.9,
                                          "style":0.35,"use_speaker_boost":True}})
    r = subprocess.run(["curl","-sS","--fail","--max-time","180","-X","POST",
        f"https://api.elevenlabs.io/v1/text-to-speech/{VOICE}/with-timestamps",
        "-H", f"xi-api-key: {os.environ['ELEVENLABS_API_KEY'].strip()}",
        "-H","Content-Type: application/json","-d",body], capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"TTS failed: {r.stderr[:200]}")
    return json.loads(r.stdout)

BLOCKS, blk = [], []
for si in range(len(SEGMENTS)):
    blk.append(si)
    if si in PAUSE_AFTER or si == len(SEGMENTS) - 1:
        BLOCKS.append(blk); blk = []

if CACHE.exists() and VO.exists():
    words = [tuple(w) for w in json.loads(CACHE.read_text())["words"]]
    print("cached VO + alignment (no TTS spend)", flush=True)
else:
    words, offset, pieces = [], 0.0, []
    for bi, block in enumerate(BLOCKS):
        d = tts(" ".join(SEGMENTS[si][0] for si in block))
        mp3 = WORK / f"_b{bi}.mp3"; mp3.write_bytes(base64.b64decode(d["audio_base64"]))
        al = d["alignment"]
        st, en = al["character_start_times_seconds"], al["character_end_times_seconds"]
        cur = 0
        for si in block:
            spoken, off = SEGMENTS[si][0], 0
            for w in spoken.split():
                i = cur + spoken.index(w, off)
                j = min(i + len(w) - 1, len(st) - 1)
                words.append((w, si, st[i] + offset, en[j] + offset))
                off = spoken.index(w, off) + len(w)
            cur += len(spoken) + 1
        pieces.append(mp3); offset += adur(mp3)
        gap = PAUSE_AFTER.get(block[-1], 0.0)
        if gap:
            sil = WORK / f"_s{bi}.mp3"
            subprocess.run(["ffmpeg","-v","error","-y","-f","lavfi","-i","anullsrc=r=44100:cl=mono",
                            "-t",f"{gap}","-c:a","libmp3lame","-b:a","192k",str(sil)],check=True)
            pieces.append(sil); offset += gap
            print(f"  beat: +{gap}s after \"{SEGMENTS[block[-1]][0]}\"", flush=True)
    ins = []
    for p in pieces: ins += ["-i", str(p)]
    subprocess.run(["ffmpeg","-v","error","-y",*ins,"-filter_complex",
        "".join(f"[{i}:a]" for i in range(len(pieces))) + f"concat=n={len(pieces)}:v=0:a=1[a]",
        "-map","[a]","-ar","44100","-c:a","libmp3lame","-b:a","192k",str(VO)],check=True)
    for p in pieces: p.unlink()
    CACHE.write_text(json.dumps({"words": words}))
    print("VO built from blocks + real silence, CACHED", flush=True)

TOTAL = adur(VO)
seg_start = {}
for w, si, a, b in words:
    seg_start[si] = min(seg_start.get(si, a), a)
seg_span = {si: (seg_start[si], seg_start.get(si + 1, TOTAL)) for si in range(len(SEGMENTS))}
print(f"[{B['name']}] VO {TOTAL:.1f}s | {len(words)} words | {len(SEGMENTS)} cuts", flush=True)

# ---------------------------------------------------------------- captions
chunks = []
for si, (spoken, displayed) in enumerate(SEGMENTS):
    sw = [w for w in words if w[1] == si]
    if displayed:
        chunks.append(([displayed], -1, sw[0][2], TOTAL)); continue
    for k in range(0, len(sw), 3):
        grp = sw[k:k+3]
        for gi, (word, _, a, b) in enumerate(grp):
            end = grp[gi+1][2] if gi+1 < len(grp) else grp[-1][3]
            chunks.append(([g[0] for g in grp], gi, a, end))

WEIGHT = B.get("weight", "Black")   # Space Grotesk's variable axis stops at Bold; others carry Black
font = ImageFont.truetype(str(B["font"]), 78);  font.set_variation_by_name(WEIGHT)
big  = ImageFont.truetype(str(B["font"]), 96);  big.set_variation_by_name(WEIGHT)
BAND_H, BAND_Y = 460, 1290
SEQ = WORK / "seq"; shutil.rmtree(SEQ, ignore_errors=True); SEQ.mkdir()

SAFE_W = 930          # inside the 1080 frame, clear of the platform UI

def fit(txt, f):
    """Shrink until the longest single token fits the frame.

    A caption wrapper can only break between WORDS. A domain is one word, so "worshipdigital.co"
    at 96px had nowhere to break and ran straight off the right edge of the video. A URL the
    viewer cannot read is a wasted ad. Auto-fit, never clip.
    """
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    size = f.size
    while size > 34:
        t = ImageFont.truetype(str(B["font"]), size); t.set_variation_by_name(WEIGHT)
        if max(probe.textlength(w, font=t) for w in txt) <= SAFE_W:
            return t
        size -= 4
    return f

def render(display, active):
    txt = [d.upper().strip(",") for d in display]
    f = fit(txt, big if len(txt) == 1 else font)
    img = Image.new("RGBA",(W,BAND_H),(0,0,0,0)); sh = Image.new("RGBA",(W,BAND_H),(0,0,0,0))
    d, ds = ImageDraw.Draw(img), ImageDraw.Draw(sh)
    rows, cur = [], []
    for i, t in enumerate(txt):
        trial = cur + [(i,t)]
        if d.textlength(" ".join(x[1] for x in trial), font=f) > SAFE_W and cur:
            rows.append(cur); cur = [(i,t)]
        else: cur = trial
    rows.append(cur)
    y = (BAND_H - len(rows)*(f.size+16)) // 2
    for row in rows:
        x = (W - d.textlength(" ".join(x[1] for x in row), font=f)) / 2
        for i, t in row:
            col = B["accent"] if (active == -1 or i == active) else B["base"]
            ds.text((x+3, y+6), t, font=f, fill=(0,0,0,170))
            d.text((x, y), t, font=f, fill=col + (255,))
            x += d.textlength(t + " ", font=f)
        y += f.size + 16
    return Image.alpha_composite(sh.filter(ImageFilter.GaussianBlur(9)), img)

seen, states = {}, {}
for ch in chunks:
    k = (tuple(ch[0]), ch[1])
    if k not in seen:
        p = SEQ / f"s{len(seen):03d}.png"; render(ch[0], ch[1]).save(p); seen[k] = p
    states[id(ch)] = seen[k]
blank = SEQ / "blank.png"; Image.new("RGBA",(W,BAND_H),(0,0,0,0)).save(blank)
for n in range(int(TOTAL*FPS)+2):
    t, src = n/FPS, blank
    for ch in chunks:
        if ch[2] <= t < ch[3]: src = states[id(ch)]; break
    dst = SEQ / f"f{n:05d}.png"
    if dst.exists(): dst.unlink()
    os.link(src, dst)
print(f"{len(chunks)} chunks -> {len(seen)} states ({B['font'].stem} {WEIGHT}, word-lit {B['accent']})", flush=True)

scrim = WORK / "scrim.png"
sc = Image.new("RGBA",(W,H),(0,0,0,0)); px = sc.load()
for y in range(H):
    a = 0 if y < 1050 else int(165 * min(1.0,(y-1050)/620)**1.4)
    for x in range(W): px[x,y] = (10,12,15,a)
sc.save(scrim)

# ---------------------------------------------------------------- branding
# Two placements, doing two different jobs:
#   CORNER  small, persistent, low-opacity. Brand awareness on every frame of a scroll-by,
#           without stealing the picture. This is the one that compounds.
#   END     the mark, full size, over a dark plate, so the last thing on screen is the brand
#           and the URL together.
# A 32-second brand video with no mark on it builds no brand.
END_T   = seg_start[len(SEGMENTS) - 1] - 0.3          # when the end card takes over
corner  = end_logo = end_plate = None
if B.get("logo") and pathlib.Path(B["logo"]).exists():
    src = Image.open(B["logo"]).convert("RGBA")
    msrc = Image.open(B["mark"]).convert("RGBA") if B.get("mark") and pathlib.Path(B["mark"]).exists() else src

    def scaled(width, im=None):
        im = im if im is not None else src
        bb = im.getbbox()                              # trim transparent padding before sizing
        im = im.crop(bb) if bb else im
        h = int(im.height * (width / im.width))
        return im.resize((width, h), Image.LANCZOS)

    sq = msrc.width and abs(msrc.width - msrc.height) < msrc.width * 0.25   # square-ish = an icon
    c = scaled(150 if sq else 300, msrc)               # persistent corner mark
    c.putalpha(c.getchannel("A").point(lambda v: int(v * 0.82)))
    corner = WORK / "logo_corner.png"; c.save(corner)

    e = scaled(720, src)                               # end card = the WORDMARK, always
    end_logo = WORK / "logo_end.png"; e.save(end_logo)

    plate = Image.new("RGBA", (W, H), (10, 12, 15, 150))   # dark plate so the mark reads
    end_plate = WORK / "end_plate.png"; plate.save(end_plate)
    print(f"branding: {pathlib.Path(B['logo']).name} -> corner (persistent) + end card @ {END_T:.1f}s",
          flush=True)

# ---------------------------------------------------------------- cuts
parts = []
for i, (stem, tin, tight, ox, oy, mode) in enumerate(CUTS):
    d = seg_span[i][1] - seg_span[i][0]
    avail = clip_len(stem) - tin
    take  = max(d/2.0, min(d, avail))              # 2x slow-mo ceiling
    f     = d / take
    cw, ch = int(1458*tight)//2*2, int(2592*tight)//2*2
    cx, cy = int((cw-1458)*ox), int((ch-2592)*oy)
    z = "min(1.0+0.0015*on,1.12)" if mode == "IN" else "max(1.12-0.0015*on,1.0)"
    out = WORK / f"_c{i:02d}.mp4"
    subprocess.run(["ffmpeg","-v","error","-y","-ss",f"{tin}","-t",f"{take:.3f}",
        "-i",str(clip_path(stem)),
        "-filter:v", f"setpts={f:.4f}*PTS,fps={FPS},"
                     f"scale={cw}:{ch}:force_original_aspect_ratio=increase,crop=1458:2592:{cx}:{cy},"
                     f"zoompan=z='{z}':d=1:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={W}x{H}:fps={FPS},"
                     f"eq=saturation=1.05:contrast=1.04,format=yuv420p",
        "-an","-r",str(FPS),"-c:v","libx264","-crf","18",str(out)],check=True)
    print(f"  cut {i:02d} {stem[:38]:<40}{d:4.1f}s  {f:.2f}x", flush=True)
    parts.append(out)

lst = WORK / "concat.txt"; lst.write_text("".join(f"file '{p}'\n" for p in parts))
silent = WORK / "silent.mp4"
subprocess.run(["ffmpeg","-v","error","-y","-f","concat","-safe","0","-i",str(lst),
                "-c","copy",str(silent)],check=True)
ins   = ["-i", str(silent), "-i", str(scrim),
         "-framerate", str(FPS), "-i", str(SEQ / "f%05d.png")]
chain = ["[0:v][1:v]overlay=0:0[bg]"]
prev, nxt = "bg", 3

if corner:
    ins += ["-i", str(corner)]
    # top-left, clear of the platform's own UI chrome
    chain.append(f"[{prev}][{nxt}:v]overlay=54:104:enable='lt(t,{END_T:.2f})'[c]")  # drops out when the end card takes over
    prev, nxt = "c", nxt + 1

    # -loop/-t: a still image is ONE frame at t=0. A time-based filter (fade at t=29.9s) has no
    # stream to act on, so alpha stays at 0 and the logo never appears. Loop it into a real
    # stream first. Overlay tolerates a still (it repeats the last frame); fade does not.
    ins += ["-loop", "1", "-framerate", str(FPS), "-t", f"{TOTAL:.2f}", "-i", str(end_plate)]
    chain.append(f"[{nxt}:v]format=rgba,fade=in:st={END_T:.2f}:d=0.5:alpha=1[plate]")
    chain.append(f"[{prev}][plate]overlay=0:0:enable='gte(t,{END_T:.2f})'[p]"); prev, nxt = "p", nxt + 1

    ins += ["-loop", "1", "-framerate", str(FPS), "-t", f"{TOTAL:.2f}", "-i", str(end_logo)]
    ey = 700
    chain.append(f"[{nxt}:v]format=rgba,fade=in:st={END_T:.2f}:d=0.5:alpha=1[el]")
    chain.append(f"[{prev}][el]overlay=(W-w)/2:{ey}:enable='gte(t,{END_T:.2f})'[e]"); prev, nxt = "e", nxt + 1

chain.append(f"[{prev}][2:v]overlay=0:{BAND_Y}[v]")     # captions always on top
ins += ["-i", str(VO)]

subprocess.run(["ffmpeg","-v","error","-y",*ins,
    "-filter_complex", ";".join(chain),
    "-map","[v]","-map",f"{nxt}:a:0","-shortest",
    "-c:v","libx264","-preset","slow","-crf","19","-pix_fmt","yuv420p",
    "-c:a","aac","-b:a","192k",str(OUT)],check=True)

for p in parts + [lst, silent, scrim] + [x for x in (corner, end_logo, end_plate) if x]:
    try: p.unlink()
    except Exception: pass
shutil.rmtree(SEQ, ignore_errors=True)

vd = adur(OUT)
print(f"\nBUILT {OUT.name} ({OUT.stat().st_size//1024//1024} MB, {vd:.1f}s | VO {TOTAL:.1f}s)", flush=True)
if abs(vd - TOTAL) > 0.4:
    print("  !! picture and voice out of sync — DO NOT SHIP", flush=True)

# attribution: the price of a free API our engine can actually call
cred = OUT.parent / f"{brand}_CREDITS.txt"
used = {c[0] for c in CUTS}
lines = [f"{B['name']} — footage credits (Pexels)"]
for v in man.values():
    if pathlib.Path(v["file"]).stem in used:
        lines.append(f"  {v['credit']} — {v['page']}")
cred.write_text("\n".join(sorted(set(lines))) + "\n")
print(f"credits -> {cred.name}", flush=True)
